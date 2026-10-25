"""Opt-in split ingress. Trust the transport peer, never a routing header.

The companion nginx profile binds its upstream sockets to distinct loopback
addresses. Uvicorn must retain these peers (--no-proxy-headers).
"""

from ipaddress import ip_address, ip_network

from starlette.responses import JSONResponse
from starlette.types import ASGIApp, Receive, Scope, Send

from .browser_security import MACHINE_ENDPOINTS
from .login_limits import peer_ip_key

BROWSER_PEER = "127.0.0.2"
HA_PEER = "127.0.0.3"


def single_header(scope: Scope, name: bytes) -> str | None:
    values = [value for key, value in scope["headers"] if key.lower() == name]
    if len(values) != 1:
        return None
    return values[0].decode("latin-1")


def canonical_path(scope: Scope) -> bool:
    raw = scope.get("raw_path", b"")
    try:
        path = raw.decode("ascii")
    except UnicodeDecodeError:
        return False
    return bool(path.startswith("/") and path == scope["path"]
                and not any(ord(char) <= 32 or ord(char) == 127 for char in path)
                and not any(char in path for char in "%\\;?#")
                and "//" not in path
                and not any(part in {".", ".."} for part in path.split("/")))


class SplitIngressMiddleware:
    def __init__(self, app: ASGIApp, browser_host: str, hsts_seconds: int = 0):
        self.app = app
        self.browser_host = browser_host
        self.hsts_seconds = hsts_seconds

    async def __call__(self, scope: Scope, receive: Receive, send: Send) -> None:
        if scope["type"] not in {"http", "websocket"}:
            await self.app(scope, receive, send)
            return
        # No WebSocket endpoints are part of the application contract.
        if scope["type"] == "websocket":
            await send({"type": "websocket.close", "code": 1008})
            return
        peer = scope.get("client")
        peer_host = peer[0] if peer else None
        is_browser = peer_host == BROWSER_PEER
        allowed = canonical_path(scope)
        if peer_host == HA_PEER:
            allowed = allowed and (scope["method"], scope["path"]) in MACHINE_ENDPOINTS
        elif is_browser:
            allowed = (allowed
                       and single_header(scope, b"host") == self.browser_host
                       and single_header(scope, b"x-forwarded-proto") == "https"
                       and not scope["path"].lower().startswith("/api/webhook")
                       and not any(key.lower() == b"cf-worker" for key, _ in scope["headers"]))
        else:
            allowed = False
        if not allowed:
            await JSONResponse({"detail": "Niedozwolona ścieżka dostępu."}, status_code=403,
                               headers={"Cache-Control": "no-store"})(scope, receive, send)
            return

        if is_browser:
            # Keep scope.client untouched. Only the login budget uses this key.
            value = single_header(scope, b"cf-connecting-ip")
            try:
                if not value or value != value.strip() or "%" in value:
                    raise ValueError
                address = ip_address(value)
                if address.version == 6 and address.ipv4_mapped is not None:
                    address = address.ipv4_mapped
                if ((address.version == 4 and address in ip_network("240.0.0.0/4"))
                        or str(address) == "2a06:98c0:3600::103"):
                    raise ValueError
                ip_key = peer_ip_key(value)
            except ValueError:
                ip_key = None
            scope = dict(scope, scheme="https", state={**scope.get("state", {}),
                                                     "ingress_ip_key": ip_key})

        async def hardened_send(message):
            if is_browser and self.hsts_seconds and message["type"] == "http.response.start":
                message = dict(message, headers=[*message["headers"],
                    (b"strict-transport-security", f"max-age={self.hsts_seconds}".encode("ascii"))])
            await send(message)

        await self.app(scope, receive, hardened_send)
