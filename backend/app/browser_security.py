"""Central browser mutation policy and response hardening. HA stays independent."""

import json
import re

from starlette.datastructures import Headers, MutableHeaders
from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .origins import canonical_origin

SAFE_METHODS = frozenset({"GET", "HEAD", "OPTIONS"})
MACHINE_ENDPOINTS = frozenset({
    ("POST", "/api/webhook/home-assistant"),
    ("POST", "/api/webhook/home-assistant/correction"),
})
LOGIN_BODY_LIMIT = 16384
CSP = ("default-src 'self'; script-src 'self'; style-src 'self'; object-src 'none'; "
       "base-uri 'none'; frame-ancestors 'none'; form-action 'self'")
SECURITY_HEADERS = {
    "Content-Security-Policy": CSP,
    "X-Content-Type-Options": "nosniff",
    "Referrer-Policy": "no-referrer",
    "X-Frame-Options": "DENY",
}


def is_api(path: str) -> bool:
    return path == "/api" or path.startswith("/api/")


def reject_constant(_value: str):
    raise ValueError("Non-JSON constant")


class BrowserMutationMiddleware:
    """Default deny for every unsafe API method except the two exact HA POSTs.

    Runs after session validation, before routing/body parsing/Argon2. Login
    bodies are bounded while streaming, regardless of Content-Length.
    """

    def __init__(self, app: ASGIApp, allowed_origins: list[str]):
        self.app = app
        self.allowed_origins = frozenset(allowed_origins)

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        endpoint = (scope.get("method"), scope.get("path", ""))
        if (scope["type"] != "http" or not is_api(endpoint[1])
                or endpoint[0] in SAFE_METHODS or endpoint in MACHINE_ENDPOINTS):
            await self.app(scope, receive, send)
            return
        headers = Headers(scope=scope)

        async def reject(code: int, detail: str) -> None:
            await JSONResponse({"detail": detail}, status_code=code)(scope, receive, send)

        origins = headers.getlist("origin")
        try:
            allowed = len(origins) == 1 and canonical_origin(origins[0]) in self.allowed_origins
        except ValueError:
            allowed = False
        if not allowed or headers.getlist("x-work-tracker-request") != ["1"]:
            await reject(403, "Niedozwolone żądanie przeglądarki.")
            return
        if headers.getlist("content-encoding") not in ([], ["identity"]):
            await reject(415, "Nieobsługiwane kodowanie treści.")
            return
        content_types = headers.getlist("content-type")
        json_type = len(content_types) == 1 and re.fullmatch(
            r'application/json(?:\s*;\s*charset\s*=\s*(?:utf-8|"utf-8"))?\s*',
            content_types[0], flags=re.IGNORECASE,
        ) is not None
        if content_types and not json_type:
            await reject(415, "Wymagany format application/json.")
            return
        login = endpoint == ("POST", "/api/auth/login")
        body = bytearray()
        while True:
            message = await receive()
            if message["type"] == "http.disconnect":
                return
            chunk = message.get("body", b"")
            if login and len(body) + len(chunk) > LOGIN_BODY_LIMIT:
                await reject(413, "Zbyt duże żądanie logowania.")
                return
            if chunk and not json_type:
                await reject(415, "Wymagany format application/json.")
                return
            body.extend(chunk)
            if not message.get("more_body", False):
                break
        if body:
            try:
                json.loads(body.decode("utf-8"), parse_constant=reject_constant)
            except (ValueError, RecursionError):
                # Never reflect JSON input, especially credentials.
                await reject(422, "Nieprawidłowy JSON.")
                return

        async def replay():
            nonlocal body
            if body is not None:
                message = {"type": "http.request", "body": bytes(body), "more_body": False}
                body = None
                return message
            return await receive()

        await self.app(scope, replay, send)


class SecurityHeadersMiddleware:
    def __init__(self, app: ASGIApp):
        self.app = app

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] != "http":
            await self.app(scope, receive, send)
            return

        async def hardened_send(message):
            if message["type"] == "http.response.start":
                headers = MutableHeaders(scope=message)
                headers.update(SECURITY_HEADERS)
                if (is_api(scope.get("path", "")) or message["status"] >= 400
                        or headers.get("content-type", "").startswith("text/html")):
                    headers["Cache-Control"] = "no-store"
            await send(message)

        await self.app(scope, receive, hardened_send)
