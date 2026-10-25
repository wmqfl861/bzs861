"""IRI helpers for the agent-facing layers: Chinese/IDN host names become punycode.

searchd percent-encodes non-ASCII path/query bytes itself; only the host needs converting here.
"""
from __future__ import annotations

from urllib.parse import urlsplit, urlunsplit


def to_uri(url: str) -> str:
    url = url.strip()
    try:
        parts = urlsplit(url)
        host = parts.hostname or ""
        port = parts.port
    except ValueError:
        return url  # let searchd report a precise error
    if not host or host.isascii() or parts.username is not None or parts.password is not None:
        # Do not erase forbidden userinfo while normalizing an internationalized host.
        return url
    try:
        import idna  # IDNA 2008 / UTS-46 (installed with aiohttp's yarl)
        ascii_host = idna.encode(host, uts46=True).decode("ascii")
    except Exception:  # noqa: BLE001 - fall back to the stdlib IDNA 2003 codec
        try:
            ascii_host = host.encode("idna").decode("ascii")
        except UnicodeError:
            return url
    netloc = ascii_host + (f":{port}" if port else "")
    return urlunsplit((parts.scheme, netloc, parts.path, parts.query, parts.fragment))
