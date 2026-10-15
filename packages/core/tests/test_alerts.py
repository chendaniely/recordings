import socket

import pytest
from recordings import alerts
from recordings.alerts import AlertConfig, ping_deadman, send
from recordings.config import ConfigError, load_config


def test_an_alert_posts_to_the_topic_with_title_priority_and_tags(ntfy):
    ac = AlertConfig(ntfy_server=ntfy.url, topic="topic-do-not-print_1", deadman_url=None)
    assert send(
        ac,
        title="recordings: backup failed",
        message="backup failed (BackupError; exit 1). See Status.",
    )
    (req,) = ntfy.requests
    assert (req["method"], req["path"]) == ("POST", "/topic-do-not-print_1")
    assert req["headers"]["Title"] == "recordings: backup failed"
    assert (req["headers"]["Priority"], req["headers"]["Tags"]) == ("4", "warning")
    assert req["body"] == "backup failed (BackupError; exit 1). See Status."
    assert "Authorization" not in req["headers"]


def test_without_a_server_or_topic_nothing_is_sent(ntfy):
    assert not send(AlertConfig(ntfy.url, None, None), title="t", message="m")
    assert not send(AlertConfig(None, "topic", None), title="t", message="m")
    assert ntfy.requests == []


def test_a_failed_delivery_never_raises_and_never_logs_the_topic(ntfy, caplog):
    ntfy.status = 500
    ac = AlertConfig(ntfy_server=ntfy.url, topic="topic-do-not-print", deadman_url=None)
    with caplog.at_level("WARNING"):
        assert send(ac, title="recordings: check failed", message="m") is False
    with socket.socket() as s:  # a port with nothing listening
        s.bind(("127.0.0.1", 0))
        closed = f"http://127.0.0.1:{s.getsockname()[1]}"
    with caplog.at_level("WARNING"):
        assert (
            send(AlertConfig(closed, "topic-do-not-print", None), title="t", message="m") is False
        )
    assert "topic-do-not-print" not in caplog.text and "HTTP 500" in caplog.text


def test_the_dead_mans_switch_speaks_healthchecks(ntfy):
    # why: Healthchecks.io takes the URL alone as success, /start and /fail around a run, and /log
    # as a note that leaves the check's state alone (healthchecks.io/docs/http_api).
    ac = AlertConfig(None, None, f"{ntfy.url}/ping/abc-do-not-print/")
    assert ping_deadman(ac, "start") and ping_deadman(ac)
    assert ping_deadman(ac, "fail", message="backup failed (BackupError; exit 1). See Status.")
    assert ping_deadman(ac, "log", message="recordings alert-test")
    assert [(r["method"], r["path"]) for r in ntfy.requests] == [
        ("POST", "/ping/abc-do-not-print/start"),
        ("POST", "/ping/abc-do-not-print"),
        ("POST", "/ping/abc-do-not-print/fail"),
        ("POST", "/ping/abc-do-not-print/log"),
    ]
    assert ntfy.requests[2]["body"] == "backup failed (BackupError; exit 1). See Status."
    assert not ping_deadman(AlertConfig(None, None, None))


def test_the_config_reads_the_secrets_from_files_and_hides_them(tmp_path):
    (tmp_path / "topic").write_text("topic-do-not-print-20\n", encoding="utf-8")
    (tmp_path / "ping").write_text("https://hc.example/ping/do-not-print\n", encoding="utf-8")
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text('[alerts]\nntfy_server = "https://ntfy.sh/"\n', encoding="utf-8")
    environ = {
        "RECORDINGS_CONFIG": str(cfg_file),
        "RECORDINGS_NTFY_TOPIC_FILE": str(tmp_path / "topic"),
        "RECORDINGS_DEADMAN_URL_FILE": str(tmp_path / "ping"),
    }
    ac = alerts.from_config(load_config(environ), environ)
    assert (ac.ntfy_server, ac.topic, ac.enabled) == (
        "https://ntfy.sh",
        "topic-do-not-print-20",
        True,
    )
    assert "do-not-print" not in repr(ac) and "do-not-print" not in str(ac)
    for server in ("ntfy.sh", "http://ntfy.example"):
        cfg_file.write_text(f'[alerts]\nntfy_server = "{server}"\n', encoding="utf-8")
        with pytest.raises(ConfigError, match="ntfy_server"):
            alerts.from_config(load_config(environ), environ)


def test_an_ntfy_token_goes_in_a_bearer_header_and_nowhere_else(tmp_path, ntfy):
    (tmp_path / "token").write_text("tk_do-not-print\n", encoding="utf-8")
    cfg_file = tmp_path / "config.toml"
    cfg_file.write_text(f'[alerts]\nntfy_server = "{ntfy.url}"\n', encoding="utf-8")
    environ = {
        "RECORDINGS_CONFIG": str(cfg_file),
        "RECORDINGS_NTFY_TOPIC": "alerts-topic",
        "RECORDINGS_NTFY_TOKEN_FILE": str(tmp_path / "token"),
    }
    ac = alerts.from_config(load_config(environ), environ)
    assert send(ac, title="recordings: test alert", message="m")
    assert ntfy.requests[0]["headers"]["Authorization"] == "Bearer tk_do-not-print"
    assert "do-not-print" not in repr(ac)


@pytest.mark.parametrize(
    "field, value",
    [
        ("ntfy_server", "ntfy.sh"),
        ("ntfy_server", "http://ntfy.example"),
        ("ntfy_server", "https://user:do-not-print@ntfy.example"),
        ("deadman_url", "http://hc-ping.example/do-not-print"),
        ("deadman_url", "hc-ping.com/do-not-print"),
        ("topic", "do-not-print/1"),
        ("topic", "x" * 65),
    ],
)
def test_plain_http_credentials_in_a_url_and_odd_topics_are_refused(field, value):
    # why: https keeps the topic and the ping URL's token off the wire in clear (security review);
    # ntfy topics are [-_A-Za-z0-9]{1,64} (ops review). The error never repeats the value.
    fields = {"ntfy_server": None, "topic": None, "deadman_url": None, field: value}
    with pytest.raises(ConfigError) as caught:
        AlertConfig(**fields)
    assert "do-not-print" not in str(caught.value) and "x" * 65 not in str(caught.value)
    AlertConfig("http://127.0.0.1:8080", "alerts-topic", "http://localhost:1/p")  # loopback is fine


@pytest.mark.parametrize("code", [301, 302, 303, 307, 308])
def test_a_redirect_is_never_followed_and_the_alert_is_not_delivered(
    ntfy, other_ntfy, caplog, code
):
    # why: urlopen's default opener follows a 301, 302 or 303 answer to a POST as a GET, to any host
    # and even from https down to http, with the Authorization and Title headers (security review).
    # Here nothing may reach the second server, and a redirected alert is not a delivered one.
    ntfy.redirect, ntfy.redirect_code = other_ntfy.url, code
    ac = AlertConfig(ntfy.url, "alerts-topic", f"{ntfy.url}/ping/abc", token="tk_do-not-print")
    with caplog.at_level("WARNING"):
        assert send(ac, title="recordings: backup failed", message="m") is False
        assert ping_deadman(ac, "fail", message="m") is False
    assert len(ntfy.requests) == 2 and other_ntfy.requests == []
    assert f"HTTP {code}" in caplog.text and "do-not-print" not in caplog.text


@pytest.mark.parametrize(
    "field, value",
    [
        ("deadman_url", "https://hc-ping.example/key/do-not-print?create=1"),
        ("deadman_url", "https://hc-ping.example/do-not-print?"),
        ("deadman_url", "https://hc-ping.example/do-not-print#x"),
        ("ntfy_server", "https://ntfy.example/?do-not-print"),
        ("ntfy_server", "https://ntfy.example\tdo-not-print"),
        ("ntfy_server", "https://ntfy.example:do-not-print"),
        ("ntfy_server", "https://ntf\u0443.example/do-not-print"),
    ],
)
def test_a_url_whose_suffix_would_not_land_on_its_path_is_refused(field, value):
    # why: /start, /fail and the topic are added to the end of the URL. After a ? or a # they land
    # in the query or are dropped, and Healthchecks takes ".../slug?create=1/fail" as a plain
    # success ping, so every failed backup would keep the dead-man's switch quiet. A tab, which
    # urlsplit drops but the request keeps, a bad port and a look-alike non-ASCII host are refused
    # too (security council).
    fields = {"ntfy_server": None, "topic": None, "deadman_url": None, field: value}
    with pytest.raises(ConfigError) as caught:
        AlertConfig(**fields)
    assert "do-not-print" not in str(caught.value)


def test_a_topic_that_is_the_only_lock_is_long_and_a_token_is_one_visible_word():
    # why: on a public server such as ntfy.sh the topic is the password (ntfy.sh's terms): whoever
    # guesses it reads the alerts and can send false ones, such as "recovered". The runbook asks for
    # 20 or more random characters (security council).
    with pytest.raises(ConfigError, match="20") as caught:
        AlertConfig("https://ntfy.sh", "do-not-print", None)
    assert "do-not-print" not in str(caught.value)
    AlertConfig("https://ntfy.sh", "x" * 20, None)
    AlertConfig("https://ntfy.example", "alerts", None, token="tk_abc")  # access control: a token
    AlertConfig("http://127.0.0.1:8080", "alerts", None)  # the tests' server on this machine
    for token in ("tk_a\r\n X-Folded: do-not-print", "tk do-not-print", "tk_\u00e9do-not-print"):
        with pytest.raises(ConfigError) as caught:
            AlertConfig("https://ntfy.example", "alerts", None, token=token)
        assert "do-not-print" not in str(caught.value)


def test_an_http_proxy_never_carries_an_alert_and_nothing_secret_is_logged(
    ntfy, other_ntfy, monkeypatch, caplog
):
    # why: urllib sends plain http through HTTP_PROXY as the whole URL with every header, the topic,
    # the ping URL and the token in clear. Only an https proxy is used, and through it an alert
    # travels in a CONNECT tunnel that shows the proxy the host name alone (security council).
    for name in ("HTTP_PROXY", "http_proxy", "ALL_PROXY", "all_proxy"):
        monkeypatch.setenv(name, other_ntfy.url)
    for name in ("NO_PROXY", "no_proxy"):
        monkeypatch.delenv(name, raising=False)
    monkeypatch.setattr(alerts, "_OPENER", alerts._build_opener())
    ac = AlertConfig(
        ntfy.url, "topic-do-not-print", f"{ntfy.url}/ping/do-not-print", token="tk_do-not-print"
    )
    with caplog.at_level("DEBUG"):
        assert send(ac, title="recordings: test alert", message="m")
        assert ping_deadman(ac, "fail", message="m")
        ntfy.status = 404
        assert send(ac, title="recordings: test alert", message="m") is False
    assert other_ntfy.requests == [] and len(ntfy.requests) == 3
    assert "do-not-print" not in caplog.text
