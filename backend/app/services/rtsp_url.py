from __future__ import annotations

"""Shared RTSP URL helpers. Never log credentials."""

from urllib.parse import quote, urlparse, urlunparse


def redact_rtsp_url(url: str) -> str:
    """Return RTSP URL safe for logs/UI (password stripped)."""
    try:
        parsed = urlparse(url)
    except Exception:
        return "rtsp://***"
    host = parsed.hostname or ""
    port = f":{parsed.port}" if parsed.port else ""
    user = parsed.username or ""
    auth = f"{user}:***@" if user or parsed.password else ""
    path = parsed.path or ""
    query = f"?{parsed.query}" if parsed.query else ""
    return f"{parsed.scheme}://{auth}{host}{port}{path}{query}"


def build_rtsp_url(base_url: str, username: str | None = None, password: str | None = None) -> str:
    """Merge separate credentials into an RTSP URL when needed."""
    parsed = urlparse(base_url)
    if not username and not password:
        return base_url
    if parsed.username or parsed.password:
        # URL already embeds credentials; prefer explicit override only if provided.
        user = username if username is not None else parsed.username
        pwd = password if password is not None else parsed.password
    else:
        user = username
        pwd = password
    if not user:
        return base_url
    auth = quote(user, safe="")
    if pwd is not None:
        auth = f"{auth}:{quote(pwd, safe='')}"
    host = parsed.hostname or ""
    netloc = f"{auth}@{host}"
    if parsed.port:
        netloc = f"{netloc}:{parsed.port}"
    return urlunparse((parsed.scheme, netloc, parsed.path, parsed.params, parsed.query, parsed.fragment))


def validate_rtsp_url_shape(url: str) -> tuple[bool, str]:
    parsed = urlparse(url)
    if parsed.scheme not in {"rtsp", "rtsps"}:
        return False, "RTSP URL must start with rtsp:// or rtsps://"
    if not parsed.hostname:
        return False, "RTSP URL is missing a host"
    return True, "RTSP URL format is valid"
