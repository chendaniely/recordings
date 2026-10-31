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
- **The Plaud sync's own check** (stage 2b §16) has its own URL, `deadman_sync_url`, mounted in
  the worker alone. `worker.conclude_turn` pings it through `ping_url`, which returns the HTTP
  status for Status; nothing else here sends to it.

The ntfy server and both ping URLs must be https, so the topic, the token and the ping URLs never
cross a network in clear;
plain http is allowed only to this machine's loopback (the tests' fake server). None may hold a
query or a fragment, since /start, /fail and the topic are added to its end. A redirect is never
followed, because it would carry the token and the title to another host, and only an https proxy
is used: a CONNECT tunnel shows it the host name alone. Without a token the topic is the alerts'
only lock (ntfy.sh's topics are public), so it is at least MIN_OPEN_TOPIC long. Alert text comes
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
MIN_OPEN_TOPIC = 20  # without a token: the runbook's "20 or more random letters and digits"
TOKEN_RE = re.compile(r"[!-~]{1,256}")  # visible ASCII, one word: nothing folded into a header
PINGS = {"success": "", "start": "/start", "fail": "/fail", "log": "/log"}
_LOOPBACK = frozenset({"127.0.0.1", "::1", "localhost"})


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """urlopen follows a 301, 302 or 303 answer to a POST as a GET to any host, even from https down
    to http, with the Authorization and Title headers: an alert is never redirected."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None  # HTTPDefaultErrorHandler raises HTTPError(code): logged, not delivered


def _build_opener() -> urllib.request.OpenerDirector:
    """Proxies from the environment only, and only for https: through one, an alert travels in a
    CONNECT tunnel, and the proxy sees the host name, never the topic, the token or the ping URL.
    Plain http (the loopback) never goes to a proxy, which would get the whole request in clear.
    The default handler would also ask macOS's system configuration, which starts threads that
    never end (and makes a later fork() warn)."""
    proxies = urllib.request.getproxies_environment()
    return urllib.request.build_opener(
        urllib.request.ProxyHandler({k: v for k, v in proxies.items() if k == "https"}), _NoRedirect
    )


_OPENER = _build_opener()


def _check_url(url: object, what: str) -> None:
    """https, with a host and no user name or password in it; http only to the loopback. No query
    or fragment: after a ? or a #, the /fail or the topic added to the end would land in the query
    (Healthchecks takes it as a plain success ping) or be dropped. Visible ASCII only, so the URL
    checked is the URL sent (urlsplit drops a tab or a newline the request keeps) and a host can't
    be a look-alike. The error names the setting, never the URL, which may be a secret."""
    plain = (
        isinstance(url, str)
        and url.isascii()
        and url.isprintable()
        and not any(c in url for c in " ?#")
    )
    try:
        parts = urlsplit(url) if plain else None
        host = parts.hostname if parts else None
        _ = parts.port if parts else None  # a port that isn't a number raises ValueError
    except ValueError:  # a malformed IPv6 address, or port
        parts, host = None, None
    if (
        parts is None
        or not host
        or "@" in parts.netloc
        or not (parts.scheme == "https" or (parts.scheme == "http" and host in _LOOPBACK))
    ):
        raise ConfigError(
            f"{what} must be an https:// URL in plain ASCII, with no user name, "
            "password, query (?) or fragment (#) in it"
        )


@dataclass(frozen=True)
class AlertConfig:
    ntfy_server: str | None
    topic: str | None
    deadman_url: str | None
    token: str | None = None  # an ntfy access token, for a server that needs one
    # The Plaud sync's own Healthchecks check (stage 2b §16), apart from the backup's: `worker`
    # only. Pinged by worker.conclude_turn after a good sync, never by the backup's jobs.
    deadman_sync_url: str | None = None

    def __post_init__(self) -> None:
        if self.ntfy_server is not None:
            _check_url(self.ntfy_server, "[alerts] ntfy_server")
        if self.deadman_url is not None:
            _check_url(self.deadman_url, "RECORDINGS_DEADMAN_URL")
        if self.deadman_sync_url is not None:
            _check_url(self.deadman_sync_url, "RECORDINGS_DEADMAN_SYNC_URL")
        if self.topic is not None and not TOPIC_RE.fullmatch(self.topic):
            raise ConfigError("RECORDINGS_NTFY_TOPIC must be 1 to 64 letters, digits, '-' or '_'")
        if self.token is not None and not TOKEN_RE.fullmatch(self.token):
            raise ConfigError("RECORDINGS_NTFY_TOKEN must be one word of visible ASCII")
        if (
            self.topic is not None
            and self.token is None
            and self.ntfy_server is not None
            and urlsplit(self.ntfy_server).hostname not in _LOOPBACK
            and len(self.topic) < MIN_OPEN_TOPIC
        ):
            raise ConfigError(
                f"RECORDINGS_NTFY_TOPIC must be {MIN_OPEN_TOPIC} or more random "
                "characters: with no token, it is all that keeps the alerts private"
            )

    @property
    def enabled(self) -> bool:
        return bool(self.ntfy_server and self.topic)

    def __repr__(self) -> str:  # never the topic, the ping URL or the token
        return (
            f"AlertConfig(ntfy_server={self.ntfy_server!r}, "
            f"topic={'set' if self.topic else None}, "
            f"deadman_url={'set' if self.deadman_url else None}, "
            f"token={'set' if self.token else None}, "
            f"deadman_sync_url={'set' if self.deadman_sync_url else None})"
        )

    __str__ = __repr__


def from_config(cfg: Config, environ: Mapping[str, str]) -> AlertConfig:
    server = cfg.data.get("alerts", {}).get("ntfy_server")
    return AlertConfig(
        ntfy_server=server.rstrip("/") if isinstance(server, str) else server,
        topic=secret("ntfy_topic", environ),
        deadman_url=secret("deadman_url", environ),
        token=secret("ntfy_token", environ),
        deadman_sync_url=secret("deadman_sync_url", environ),
    )


def _deliver(request: urllib.request.Request) -> bool:
    try:
        with _OPENER.open(request, timeout=TIMEOUT) as response:
            return 200 <= response.status < 300
    except urllib.error.HTTPError as exc:
        log.warning("delivery failed: HTTP %s", exc.code)
        exc.close()
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        log.warning("delivery failed: %s", type(exc).__name__)
    return False


def send(
    ac: AlertConfig,
    *,
    title: str,
    message: str,
    priority: int = 4,
    tags: Sequence[str] = ("warning",),
) -> bool:
    """Push one alert. The title and the message come from fixed templates (Review Focus 4)."""
    if not ac.enabled:
        return False
    headers = {
        "Title": title,
        "Priority": str(priority),
        "Tags": ",".join(tags),
        "User-Agent": f"recordings/{__version__}",
    }
    if ac.token:
        headers["Authorization"] = f"Bearer {ac.token}"
    delivered = _deliver(
        urllib.request.Request(
            f"{ac.ntfy_server}/{ac.topic}",
            data=message.encode("utf-8"),
            method="POST",
            headers=headers,
        )
    )
    if not delivered:
        log.warning("alert %r was not delivered", title)
    return delivered


def ping_url(url: str, kind: str = "success", *, message: str = "") -> int | None:
    """One Healthchecks ping to `url`, `kind` being a key of PINGS, with a fixed body. `url` must
    come from an AlertConfig, which checks it: this function doesn't. Returns the HTTP status, so
    Status can say "last ping failed (HTTP 404)", or None when the check couldn't be reached. A
    redirect is never followed: its 3xx is the answer. Never raises; a failure is logged by status
    or class, never with the URL."""
    try:
        request = urllib.request.Request(
            url.rstrip("/") + PINGS[kind],
            data=message.encode("utf-8"),
            method="POST",
            headers={"User-Agent": f"recordings/{__version__}"},
        )
        with _OPENER.open(request, timeout=TIMEOUT) as response:
            return response.status
    except urllib.error.HTTPError as exc:
        log.warning("ping failed: HTTP %s", exc.code)
        exc.close()
        return exc.code
    except (urllib.error.URLError, http.client.HTTPException, OSError, ValueError) as exc:
        log.warning("ping failed: %s", type(exc).__name__)
        return None


def ping_deadman(ac: AlertConfig, kind: str = "success", *, message: str = "") -> bool:
    """One ping of the backup's check (`deadman_url`), `kind` being a key of PINGS. The body is
    a fixed text. True when Healthchecks took it."""
    if not ac.deadman_url:
        return False
    status = ping_url(ac.deadman_url, kind, message=message)
    return status is not None and 200 <= status < 300
