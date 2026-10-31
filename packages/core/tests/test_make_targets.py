"""Make targets that reach the server (stage 2b spec §21's shell targets), run for real against a
fake `ssh` on PATH: the remote command runs in a temporary folder that stands for the server's
checkout, with the real docker/consent-add.sh. Synthetic IDs only; nothing leaves the machine."""

import os
import re
import shutil
import stat
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[3]
HEX_A, HEX_B, HEX_C = (f"{n:032x}" for n in (1, 2, 3))  # synthetic Plaud IDs, canonical
PLAUD_A = "of_" + HEX_A
KEPT = f"{9:032x}"  # the seeded list's own exclusion
SEED = f"# Consent list (synthetic)\n- `{KEPT}` — asked\n"
ADDED = r"added \d{4}-\d{2}-\d{2}"
DOCUMENTED = "make consent-add ID='<id>'"  # the README's, the runbook's and `make help`'s form


def mac_environment(extra_path: Path) -> dict[str, str]:
    """This machine's environment, less what make would read as one of the target's variables."""
    env = {k: v for k, v in os.environ.items() if k not in ("ID", "SERVER", "REPO_DIR")}
    return env | {"PATH": f"{extra_path}{os.pathsep}{os.environ['PATH']}"}


def run(command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)


def make(server, *args: str, cwd: Path = REPO, env: dict[str, str] | None = None):
    """`make --no-print-directory <args>` on the Mac, with the fake ssh on PATH."""
    return run(["make", "--no-print-directory", *args], cwd=cwd, env=env or server.env)


@pytest.fixture
def server(tmp_path):
    """The stand-in: a checkout with docker/consent-add.sh, a deploy.env naming a consent folder
    that holds a list (SEED, mode 0600), an up-deployed.sh that only records its arguments (and
    fails while `server.readback_fails` exists), and an ssh that logs the host and the command,
    then runs the command there (or exits with $FAKE_SSH_EXIT, as ssh does if it can't connect)."""
    if shutil.which("make") is None:
        pytest.skip("make is not installed")
    checkout = tmp_path / "server" / "repo"
    (checkout / "docker").mkdir(parents=True)
    shutil.copy2(REPO / "docker" / "consent-add.sh", checkout / "docker" / "consent-add.sh")
    consent = tmp_path / "server" / "consent"
    consent.mkdir(mode=0o700)
    (checkout / "docker" / "deploy.env").write_text(
        f"RECORDINGS_CONSENT_HOST={consent}\n", encoding="utf-8"
    )
    listing = consent / "consent-list.md"
    listing.write_text(SEED, encoding="utf-8")
    listing.chmod(0o600)
    log = tmp_path / "ssh.log"
    fails = tmp_path / "readback-fails"
    (checkout / "docker" / "up-deployed.sh").write_text(
        f'printf "up-deployed %s\\n" "$*" >> "{log}"\n'
        f'if [ -e "{fails}" ]; then c=$(cat "{fails}"); exit "${{c:-1}}"; fi\n',
        encoding="utf-8",
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ssh").write_text(
        "#!/bin/sh\n"
        f'printf "host %s\\n" "$1" >> "{log}"\n'
        "shift\n"
        f'printf "command %s\\n" "$*" >> "{log}"\n'
        '[ -z "${FAKE_SSH_EXIT:-}" ] || exit "$FAKE_SSH_EXIT"\n'
        'exec sh -c "$*"\n',
        encoding="utf-8",
    )
    (bin_dir / "ssh").chmod(0o755)
    return SimpleNamespace(
        checkout=checkout,
        list=listing,
        log=log,
        readback_fails=fails,
        env=mac_environment(bin_dir),
    )


def consent_add(server, value: str, env: dict[str, str] | None = None):
    return make(
        server,
        "consent-add",
        f"ID={value}",
        "SERVER=homelab-test",
        f"REPO_DIR={server.checkout}",
        env=env,
    )


def script(server, value: str, env: dict[str, str] | None = None):
    """The server's half, run directly in the stand-in checkout."""
    return run(
        ["sh", "docker/consent-add.sh", value],
        cwd=server.checkout,
        env=env or {"PATH": os.environ["PATH"]},
    )


def test_consent_add_refuses_a_bad_id_before_ssh(server, tmp_path):
    marker = tmp_path / "expanded"
    for bad in (
        "not-an-id",
        "0" * 31,
        "of_" + "0" * 33,
        "- `" + "g" * 32 + "` —",
        "x\n" + PLAUD_A,
        f"$(shell touch {marker})",  # make takes the ID as written: nothing in it runs
    ):
        done = consent_add(server, bad)
        assert done.returncode != 0 and "not a Plaud ID" in done.stderr, bad
    assert not marker.exists()
    assert not server.log.exists()  # ssh never ran
    assert server.list.read_text(encoding="utf-8") == SEED
    usage = make(server, "consent-add")
    assert usage.returncode != 0 and "usage: make consent-add ID='<" in usage.stderr
    # the server's own check refuses too, before the list is touched
    direct = script(server, f"{PLAUD_A}; touch hacked")
    assert direct.returncode == 2 and "not a Plaud ID" in direct.stderr
    assert server.list.read_text(encoding="utf-8") == SEED
    assert not (server.checkout / "hacked").exists()


def test_two_ids_on_one_line_are_refused_and_one_with_a_note_is_not(server):
    # why: pasting two Copy IDs together once added the first and silently dropped the second,
    # which then stayed importable.
    both = f"- `{PLAUD_A}` —- `of_{HEX_B}` —"
    done = consent_add(server, both)
    assert done.returncode != 0 and "not a Plaud ID" in done.stderr
    in_the_note = f"- `{PLAUD_A}` — see {HEX_B}"
    assert consent_add(server, in_the_note).returncode != 0
    assert not server.log.exists()
    assert server.list.read_text(encoding="utf-8") == SEED
    ok = consent_add(server, f"- `{PLAUD_A}` — asked by phone, 2026-10-23")
    assert ok.returncode == 0, ok.stderr
    assert server.list.read_text(encoding="utf-8").splitlines()[-1].startswith(f"- `{HEX_A}` —")


def test_consent_add_writes_by_rename_and_keeps_the_mode(server):
    # why: the worker watches the folder; a list written in place could be read half-written.
    server.list.write_text(f"# Consent list (synthetic)\n- `{KEPT}` — asked", encoding="utf-8")
    server.list.chmod(0o400)  # the runbook makes it read-only
    before = server.list.stat().st_ino
    done = consent_add(server, PLAUD_A)
    assert done.returncode == 0, done.stderr
    after = server.list.stat()
    assert after.st_ino != before  # a new file, renamed over the old one
    assert stat.S_IMODE(after.st_mode) == 0o400
    lines = server.list.read_text(encoding="utf-8").splitlines()
    assert lines[:2] == ["# Consent list (synthetic)", f"- `{KEPT}` — asked"]
    assert re.fullmatch(rf"- `{HEX_A}` — {ADDED}", lines[2]) and len(lines) == 3
    assert [p.name for p in server.list.parent.iterdir()] == ["consent-list.md"]  # no temp left
    assert "up-deployed exec -T worker recordings plaud consent" in server.log.read_text()
    assert "within a few seconds" in done.stdout  # the readback may precede the worker's next look


def test_consent_add_accepts_the_copy_id_form(server):
    # Review Focus 4: the Sync screen's Copy ID copies the list's own form; pasted into
    # `make consent-add ID='…'`, it adds the ID, never a typo line that stops every import. Every
    # form is written as the canonical ID, which the list matches whatever form Plaud lists.
    done = consent_add(server, f"- `{PLAUD_A}` —")
    assert done.returncode == 0, done.stderr
    assert re.fullmatch(
        rf"{re.escape(SEED)}- `{HEX_A}` — {ADDED}\n", server.list.read_text(encoding="utf-8")
    )
    assert stat.S_IMODE(server.list.stat().st_mode) == 0o600  # the list's own mode, kept
    assert consent_add(server, HEX_B.upper()).returncode == 0  # bare, upper case
    assert consent_add(server, f"OF_{HEX_C}").returncode == 0  # the prefix in any case
    lines = server.list.read_text(encoding="utf-8").splitlines()
    assert re.fullmatch(rf"- `{HEX_B}` — {ADDED}", lines[3])
    assert re.fullmatch(rf"- `{HEX_C}` — {ADDED}", lines[4])


# Lists the parser reads as empty or comments-only after NFKC and without invisible characters,
# which the script must refuse too (a BOM before a comment header is the realistic one).
INVISIBLE_STOPS = (
    "\ufeff",  # a BOM alone
    "\ufeff# comments\n",  # a BOM, then comments
    "\u00a0\n",  # a no-break-space-only line
    "\u200b# zero-width space before the number sign\n",
    "\uff03 header\n",  # a fullwidth number sign
    "\ufe5f small number sign\n",
    "\u3000\n",  # an ideographic space only
    "\x1f\n",  # a unit separator only
)


def test_consent_add_never_makes_a_list_or_extends_an_empty_one(server):
    # why: stage 2b spec §12.1. A missing or empty list is a consent stop that fails closed; one
    # exclusion added to it would lift the stop while the real list's recordings stay unlisted.
    folder = server.list.parent
    for text in (
        None,
        "",
        " \n\t\n\r\n\n",
        *INVISIBLE_STOPS,
        "# only\n   # comments (indented)\n\n",
    ):
        if text is None:
            server.list.unlink()
        else:
            server.list.write_bytes(text.encode())
        done = script(server, PLAUD_A)
        assert done.returncode == 2, text
        assert (
            "missing, empty or only comments" in done.stderr and "section 6 step 1" in done.stderr
        ), text
        assert "Restoring for real" in done.stderr, text
        assert [p.name for p in folder.iterdir()] == ([] if text is None else ["consent-list.md"])
        if text is not None:
            assert server.list.read_bytes() == text.encode()
    # through make: ssh ran, the server refused, and the Mac says the list is unchanged
    done = consent_add(server, PLAUD_A)
    assert done.returncode != 0 and "adding the ID" in done.stderr and "unchanged" in done.stderr
    assert [p.name for p in folder.iterdir()] == ["consent-list.md"]


def test_a_list_with_comments_and_a_kept_line_is_extended(server):
    # why: such a list is a stop too (no exclusion), but one Dan wrote: his first exclusion is
    # what he means (the controller's ruling). Only blank lines and comments are refused.
    text = f"# header\n  # indented\n\nkept: `{KEPT}` — a recording I keep\n"
    server.list.write_text(text, encoding="utf-8")
    done = consent_add(server, PLAUD_A)
    assert done.returncode == 0, done.stderr
    lines = server.list.read_text(encoding="utf-8").splitlines()
    assert "\n".join(lines[:4]) + "\n" == text
    assert re.fullmatch(rf"- `{HEX_A}` — {ADDED}", lines[4]) and len(lines) == 5


def test_a_second_id_the_parser_would_read_is_refused_too(server):
    # why: the parser reads the text after NFKC and without invisible characters, so a second ID in
    # fullwidth digits, or split by a zero-width space, is one it would list; it must not pass.
    fullwidth = "".join(chr(0xFF10 + int(c, 16)) if c.isdigit() else c for c in HEX_B)
    split = HEX_B[:16] + "\u200b" + HEX_B[16:]
    for second in (fullwidth, split):
        done = consent_add(server, f"- `{PLAUD_A}` —- `of_{second}` —")
        assert done.returncode != 0 and "not a Plaud ID" in done.stderr, second
    assert not server.log.exists()
    assert server.list.read_text(encoding="utf-8") == SEED


def test_a_bom_before_a_real_list_is_still_extended(server):
    # why: the BOM is invisible to the parser, which reads the first line as it is; only a list with
    # nothing but blanks and comments is refused.
    text = f"\ufeff- `{KEPT}` — x\n"
    server.list.write_text(text, encoding="utf-8")
    done = consent_add(server, PLAUD_A)
    assert done.returncode == 0, done.stderr
    assert server.list.read_text(encoding="utf-8").startswith(text)


def test_an_unreadable_list_is_not_told_to_be_put_back(server):
    # why: advice to put the real list back could put an older list over a newer one.
    if os.geteuid() == 0:
        pytest.skip("root reads a mode-000 file, so the test would prove nothing")
    server.list.chmod(0)
    try:
        done = script(server, PLAUD_A)
    finally:
        server.list.chmod(0o600)
    assert done.returncode == 2 and "cannot read" in done.stderr and "owner and mode" in done.stderr
    assert "Put the real list" not in done.stderr
    assert server.list.read_text(encoding="utf-8") == SEED


def english_utf8_locale() -> str | None:
    names = run(["locale", "-a"], cwd=REPO, env={"PATH": os.environ["PATH"]}).stdout.split()
    for name in names:
        if name.lower().replace("-", "").startswith("en_us.utf8"):
            return name
    return None


def test_a_letter_outside_ascii_is_not_a_hex_digit_in_any_locale(server):
    # why: a range like [0-9a-f] is unspecified outside the POSIX locale (XBD 9.3.5); macOS's sh
    # once accepted `of_é000…` under en_US.UTF-8 and wrote it to the list.
    name = english_utf8_locale()
    if name is None:
        pytest.skip("no en_US.UTF-8 locale on this machine, so the test would prove nothing")
    env = {"PATH": os.environ["PATH"], "LC_ALL": name}
    for bad in ("of_é" + "0" * 31, "é" + "0" * 31, "0" * 31 + "ï"):
        assert len(bad.removeprefix("of_")) == 32
        done = script(server, bad, env=env)
        assert done.returncode == 2 and "not a Plaud ID" in done.stderr, bad
    assert server.list.read_text(encoding="utf-8") == SEED


def test_each_failure_names_its_step(server):
    # why: the Mac must tell "the server was unreachable" (nothing added), "adding failed" (the
    # list is unchanged) and "reading back failed after a good add" (the ID is on the list).
    unreachable = consent_add(server, PLAUD_A, env=server.env | {"FAKE_SSH_EXIT": "255"})
    assert unreachable.returncode != 0 and "could not be reached" in unreachable.stderr
    assert "connection dropped" in unreachable.stderr
    assert "rc exec worker recordings plaud consent" in unreachable.stderr
    assert server.list.read_text(encoding="utf-8") == SEED

    server.list.unlink()
    adding = consent_add(server, PLAUD_A)
    assert adding.returncode != 0 and "step 2 failed" in adding.stderr
    assert "adding the ID" in adding.stderr and "the list is unchanged" in adding.stderr
    assert "within a few seconds" not in adding.stdout

    server.list.write_text(SEED, encoding="utf-8")
    server.readback_fails.write_text("", encoding="utf-8")
    readback = consent_add(server, PLAUD_A)
    assert readback.returncode != 0 and "step 3 failed" in readback.stderr
    assert "reading the list back" in readback.stderr and "is on the list" in readback.stderr
    assert f"`{HEX_A}`" in server.list.read_text(encoding="utf-8")

    # a readback that worked but still shows a stop (the command exits 78): not "failed"
    server.readback_fails.write_text("78", encoding="utf-8")
    stopped = consent_add(server, HEX_B)
    assert (
        stopped.returncode != 0 and "the ID was added, but the list still stops" in stopped.stderr
    )
    assert "reading the list back" not in stopped.stderr


def test_the_documented_line_takes_a_pasted_copy_id_through_a_real_shell(server):
    # why: the Copy ID form holds backticks. Pasted unquoted or in double quotes, the shell runs
    # them as a command and make gets a broken ID; single-quoted, as the README, the runbook and
    # `make help` write it, it arrives whole.
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    runbook = (REPO / "docs" / "runbooks" / "first-run.md").read_text(encoding="utf-8")
    assert DOCUMENTED in readme and DOCUMENTED in runbook
    assert DOCUMENTED in (REPO / "Makefile").read_text(encoding="utf-8")
    line = DOCUMENTED.replace("<id>", f"- `{PLAUD_A}` —")
    done = run(
        ["sh", "-c", f"{line} SERVER=homelab-test REPO_DIR='{server.checkout}'"],
        cwd=REPO,
        env=server.env,
    )
    assert done.returncode == 0, done.stderr
    assert re.fullmatch(
        rf"{re.escape(SEED)}- `{HEX_A}` — {ADDED}\n", server.list.read_text(encoding="utf-8")
    )


def test_consent_add_expands_server_and_repo_dir_on_the_mac(server, tmp_path):
    # why: the server's shell has no SERVER or REPO_DIR. make expands both on the Mac, so the
    # command ssh carries is literal.
    assert consent_add(server, PLAUD_A).returncode == 0
    host, command = server.log.read_text().splitlines()[:2]
    assert host == "host homelab-test"
    assert command.startswith(
        f"command cd '{server.checkout}' && sh docker/consent-add.sh '{HEX_A}'"
    )
    assert "$" not in command.replace("$?", "")  # the one `$?` is the server's own
    # local.mk, -include'd, is read from the folder make runs in
    mac = tmp_path / "mac"
    mac.mkdir()
    (mac / "local.mk").write_text(
        "SERVER = homelab-from-local-mk\nREPO_DIR = /srv/example/repo\n", encoding="utf-8"
    )
    makefile = str(REPO / "Makefile")
    dry = make(server, "-n", "-C", str(mac), "-f", makefile, "consent-add", f"ID={PLAUD_A}")
    assert dry.returncode == 0, dry.stderr
    assert 'ssh "homelab-from-local-mk" "cd \'/srv/example/repo\' && ' in dry.stdout
    empty = tmp_path / "no-local-mk"
    empty.mkdir()
    refused = make(server, "-n", "-C", str(empty), "-f", makefile, "consent-add", f"ID={PLAUD_A}")
    assert refused.returncode != 0 and "local.mk" in refused.stderr
