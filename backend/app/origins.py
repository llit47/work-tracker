"""Strict HTTP origin parsing, shared with deployment (stdlib only)."""

import ipaddress
import re
import sys
from urllib.parse import urlsplit


def canonical_origin(value: str) -> str:
    if not value or not value.isascii() or re.search(r"[\x00-\x20\x7f\\%]", value):
        raise ValueError("Origin must be a single explicit HTTP(S) origin")
    parsed = urlsplit(value)
    if (parsed.scheme not in {"http", "https"} or not parsed.netloc
            or parsed.path or parsed.query or parsed.fragment
            or any(char in value for char in "@?#") or parsed.netloc.endswith(":")):
        raise ValueError("Origin must contain only scheme, host and optional port")
    host = parsed.hostname
    if not host:
        raise ValueError("Origin host is required")
    try:
        address = ipaddress.ip_address(host)
    except ValueError:
        if (len(host) > 253 or not all(re.fullmatch(
                r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label
        ) for label in host.split("."))):
            raise ValueError("Invalid origin host") from None
        # Reject alternative numeric IP spellings interpreted differently by browsers.
        if re.fullmatch(r"[0-9.]+", host) or any(
                label.startswith("0x") for label in host.split(".")):
            raise ValueError("Use canonical IP addresses")
    else:
        host = f"[{address.compressed}]" if address.version == 6 else str(address)
    port = parsed.port
    if port is not None and not 1 <= port <= 65535:
        raise ValueError("Invalid origin port")
    suffix = "" if port in {None, 80 if parsed.scheme == "http" else 443} else f":{port}"
    return f"{parsed.scheme}://{host}{suffix}"


def origin_list(value: str, *, required: bool = False) -> list[str]:
    if not value.strip():
        if required:
            raise ValueError("BROWSER_ALLOWED_ORIGINS must be explicitly configured and nonempty")
        return []
    return list(dict.fromkeys(canonical_origin(item.strip()) for item in value.split(",")))


def validate_browser_origins(browser: str, cors: str) -> None:
    if not set(origin_list(cors)) <= set(origin_list(browser, required=True)):
        raise ValueError("CORS_ORIGINS must be a subset of BROWSER_ALLOWED_ORIGINS")


if __name__ == "__main__":
    try:
        if sys.argv[1] == "subset":
            validate_browser_origins(sys.argv[2], sys.argv[3])
        else:
            origin_list(sys.argv[2], required=sys.argv[1] == "browser_origins")
    except ValueError as error:
        print(str(error), file=sys.stderr)
        sys.exit(1)
