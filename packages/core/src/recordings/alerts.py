"""Alerts (spec §14): push notifications through ntfy, and a dead-man's switch.

- **ntfy** (https://docs.ntfy.sh/publish/): a POST to <server>/<topic>, with the message as the
  body and Title, Priority and Tags headers. The topic is a secret, because anyone who knows it
  can read the alerts. It is never logged, printed, or put in an error. A server that needs an
  access token gets one as `Authorization: Bearer` (RECORDINGS_NTFY_TOKEN_FILE); ntfy.sh needs
  none.
- **The dead-man's switch** is a Healthchecks.io check (https://healthchecks.io/docs/http_api/).
  Each backup pings <url>/start, then <url> when it worked or <url>/fail when it didn't.
  `recordings alert-test` pings <url>/log, which leaves the check's state alone. Healthchecks
  alerts when the pings stop, and that is what notices a stopped container or a dead server. Its
  period is 1 hour and its grace at least 3 hours, because the loop runs one job at a time and a
  long check or restore test holds back the next backup (docs/runbooks/first-run.md).

Both URLs must be https, so the topic, the token and the ping URL never cross a network in clear;
plain http is allowed only to this machine's loopback (the tests' fake server). Alert text comes
from fixed templates (a job, an exception's class, an exit code), never a recording's title, a
path or a tool's own output (Review Focus 4). Sending never raises: a failed delivery is logged
without its URL.
"""

from __future__ import annotations

import http.client
import logging
import re
import urllib.error
import urllib.request
from collections.abc import Mapping, Sequence
from dataclasses import dataclass
from urllib.parse import urlsplit

from recordings import __version__
from recordings.config import Config, ConfigError, secret

log = logging.getLogger(__name__)
TIMEOUT = 10.0
TOPIC_RE = re.compile(r"[-_A-Za-z0-9]{1,64}")
PINGS = {"success": "", "start": "/start", "fail": "/fail", "log": "/log"}
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


def _check_url(url: object, what: str) -> None:
    """https, with a host and no user name or password in it; http only to the loopback. The
    error names the setting, never the URL, which may be a secret."""
    try:
        parts = urlsplit(url) if isinstance(url, str) else None
        host = parts.hostname if parts else None
    except ValueError:  # a malformed IPv6 address
        parts, host = None, None
    if parts is None or not host or "@" in parts.netloc or not (
            parts.scheme == "https" or (parts.scheme == "http" and host in _LOOPBACK)):
        raise ConfigError(f"{what} must be an https:// URL, with no user name or password in it")


@dataclass(frozen=True)
class AlertConfig:
    ntfy_server: str | None
    topic: str | None
    deadman_url: str | None
    token: str | None = None  # an ntfy access token, for a server that needs one

    def __post_init__(self) -> None:
        if self.ntfy_server is not None:
            _check_url(self.ntfy_server, "[alerts] ntfy_server")
        if self.deadman_url is not None:
            _check_url(self.deadman_url, "RECORDINGS_DEADMAN_URL")
        if self.topic is not None and not TOPIC_RE.fullmatch(self.topic):
            raise ConfigError("RECORDINGS_NTFY_TOPIC must be 1 to 64 letters, digits, '-' or '_'")

    @property
    def enabled(self) -> bool:
        return bool(self.ntfy_server and self.topic)

    def __repr__(self) -> str:  # never the topic, the ping URL or the token
        return (f"AlertConfig(ntfy_server={self.ntfy_server!r}, "
                f"topic={'set' if self.topic else None}, "
                f"deadman_url={'set' if self.deadman_url else None}, "
                f"token={'set' if self.token else None})")

    __str__ = __repr__


def from_config(cfg: Config, environ: Mapping[str, str]) -> AlertConfig:
    server = cfg.data.get("alerts", {}).get("ntfy_server")
    return AlertConfig(ntfy_server=server.rstrip("/") if isinstance(server, str) else server,
                       topic=secret("ntfy_topic", environ),
                       deadman_url=secret("deadman_url", environ),
                       token=secret("ntfy_token", environ))


def _deliver(request: urllib.request.Request) -> bool:
    try:
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        log.warning("delivery failed: HTTP %s", exc.code)
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        log.warning("delivery failed: %s", type(exc).__name__)
    return False


def send(ac: AlertConfig, *, title: str, message: str, priority: int = 4,
         tags: Sequence[str] = ("warning",)) -> bool:
    """Push one alert. The title and the message come from fixed templates (Review Focus 4)."""
    if not ac.enabled:
        return False
    headers = {"Title": title, "Priority": str(priority), "Tags": ",".join(tags),
               "User-Agent": f"recordings/{__version__}"}
    if ac.token:
        headers["Authorization"] = f"Bearer {ac.token}"
    delivered = _deliver(urllib.request.Request(
        f"{ac.ntfy_server}/{ac.topic}", data=message.encode("utf-8"), method="POST",
        headers=headers))
    if not delivered:
        log.warning("alert %r was not delivered", title)
    return delivered


def ping_deadman(ac: AlertConfig, kind: str = "success", *, message: str = "") -> bool:
    """One Healthchecks ping, `kind` being a key of PINGS. The body is a fixed text."""
    if not ac.deadman_url:
        return False
    return _deliver(urllib.request.Request(
        ac.deadman_url.rstrip("/") + PINGS[kind], data=message.encode("utf-8"), method="POST",
        headers={"User-Agent": f"recordings/{__version__}"}))
