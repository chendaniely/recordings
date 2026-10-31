"""Where the Plaud client may point (stage 2b spec §10.1): `API_BASE` and `check_api_base`, and the
URL rules the client's link check shares with them.

Network-free: the standard library's `ipaddress` and `urllib.parse`, and `ConfigError`. The
settings reader imports this module, never the client, and `web` reads the settings, so the web
app never loads the Plaud client, even at runtime (spec §9.1: "It imports no client, token or sync
module"; 2b phase 1 plan, decision 31). `client` imports both names from here, so
`client.API_BASE` and `client.check_api_base` still work.
"""

from __future__ import annotations

import ipaddress
from urllib.parse import SplitResult, urlsplit

from recordings.config import ConfigError

API_BASE = "https://platform.plaud.ai/developer/api"  # as copied from audio-router's client
_LOOPBACK_NAMES = frozenset({"localhost"})


def is_loopback(host: str | None) -> bool:
    if not host:
        return False
    if host in _LOOPBACK_NAMES:
        return True
    try:
        return ipaddress.ip_address(host).is_loopback
    except ValueError:
        return False


def plain_url(url: object) -> bool:
    """Visible ASCII, no space: the URL checked is the URL sent (urlsplit drops a tab or a newline
    that the request would keep), and a host can't be a look-alike."""
    return isinstance(url, str) and url.isascii() and url.isprintable() and " " not in url


def split_url(url: object) -> SplitResult | None:
    """The URL's parts, or None unless it is plain, with a host, a numeric port if any, and no
    user name or password."""
    if not plain_url(url):
        return None
    try:
        parts = urlsplit(str(url))
        _ = parts.port  # a port that isn't a number raises ValueError
    except ValueError:  # a malformed IPv6 address, or port
        return None
    return parts if parts.hostname and "@" not in parts.netloc else None


def check_api_base(url: object) -> str:
    """`[plaud] api_base`: https://, or http:// to the loopback for tests (§10.1), with no query
    or fragment, since paths are added to its end. The error names the setting, never the URL."""
    parts = split_url(url)
    if (
        parts is None
        or any(c in str(url) for c in "?#")
        or not (parts.scheme == "https" or (parts.scheme == "http" and is_loopback(parts.hostname)))
    ):
        raise ConfigError(
            "[plaud] api_base must be an https:// URL in plain ASCII (http:// only to this "
            "machine's loopback), with no user name, password, query (?) or fragment (#)"
        )
    return str(url).rstrip("/")
