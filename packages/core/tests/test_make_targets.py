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
ADDED = r"added \d{4}-\d{2}-\d{2}"
DOCUMENTED = "make consent-add ID='<id>'"  # the README's, the runbook's and `make help`'s form


def mac_environment(extra_path: Path) -> dict[str, str]:
    """This machine's environment, less what make would read as one of the target's variables."""
    env = {k: v for k, v in os.environ.items() if k not in ("ID", "SERVER", "REPO_DIR")}
    return env | {"PATH": f"{extra_path}{os.pathsep}{os.environ['PATH']}"}


@pytest.fixture
def server(tmp_path):
    """The stand-in: a checkout with docker/consent-add.sh, a deploy.env naming a consent folder,
    an up-deployed.sh that only records its arguments, and an ssh that logs the host and the
    command, then runs the command there."""
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
    log = tmp_path / "ssh.log"
    (checkout / "docker" / "up-deployed.sh").write_text(
        f'printf "up-deployed %s\\n" "$*" >> "{log}"\n', encoding="utf-8"
    )
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()
    (bin_dir / "ssh").write_text(
        "#!/bin/sh\n"
        f'printf "host %s\\n" "$1" >> "{log}"\n'
        "shift\n"
        f'printf "command %s\\n" "$*" >> "{log}"\n'
        'exec sh -c "$*"\n',
        encoding="utf-8",
    )
    (bin_dir / "ssh").chmod(0o755)
    return SimpleNamespace(
        checkout=checkout, list=consent / "consent-list.md", log=log, env=mac_environment(bin_dir)
    )


def consent_add(server, value: str) -> subprocess.CompletedProcess:
    return subprocess.run(
        [
            "make",
            "--no-print-directory",
            "consent-add",
            f"ID={value}",
            "SERVER=homelab-test",
            f"REPO_DIR={server.checkout}",
        ],
        cwd=REPO,
        env=server.env,
        capture_output=True,
        text=True,
        check=False,
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
    assert not server.list.exists()
    usage = subprocess.run(
        ["make", "--no-print-directory", "consent-add"],
        cwd=REPO,
        env=server.env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert usage.returncode != 0 and "usage: make consent-add ID='<" in usage.stderr
    # the server's own check refuses too, before the list is touched
    direct = subprocess.run(
        ["sh", "docker/consent-add.sh", f"{PLAUD_A}; touch hacked"],
        cwd=server.checkout,
        capture_output=True,
        text=True,
        check=False,
    )
    assert direct.returncode == 2 and "not a Plaud ID" in direct.stderr
    assert not server.list.exists() and not (server.checkout / "hacked").exists()


def test_consent_add_writes_by_rename_and_keeps_the_mode(server):
    # why: the worker watches the folder; a list written in place could be read half-written.
    kept = f"{9:032x}"
    server.list.write_text(f"# Consent list (synthetic)\n- `{kept}` — asked", encoding="utf-8")
    server.list.chmod(0o400)  # the runbook makes it read-only
    before = server.list.stat().st_ino
    done = consent_add(server, PLAUD_A)
    assert done.returncode == 0, done.stderr
    after = server.list.stat()
    assert after.st_ino != before  # a new file, renamed over the old one
    assert stat.S_IMODE(after.st_mode) == 0o400
    lines = server.list.read_text(encoding="utf-8").splitlines()
    assert lines[:2] == ["# Consent list (synthetic)", f"- `{kept}` — asked"]
    assert re.fullmatch(rf"- `{HEX_A}` — {ADDED}", lines[2]) and len(lines) == 3
    assert [p.name for p in server.list.parent.iterdir()] == ["consent-list.md"]  # no temp left
    assert "up-deployed exec -T worker recordings plaud consent" in server.log.read_text()


def test_consent_add_accepts_the_copy_id_form(server):
    # Review Focus 4: the Sync screen's Copy ID copies the list's own form; pasted into
    # `make consent-add ID='…'`, it adds the ID, never a typo line that stops every import. Every
    # form is written as the canonical ID, which the list matches whatever form Plaud lists.
    done = consent_add(server, f"- `{PLAUD_A}` —")
    assert done.returncode == 0, done.stderr
    assert re.fullmatch(rf"- `{HEX_A}` — {ADDED}\n", server.list.read_text(encoding="utf-8"))
    assert stat.S_IMODE(server.list.stat().st_mode) == 0o600  # a new list
    assert consent_add(server, HEX_B.upper()).returncode == 0  # bare, upper case
    assert consent_add(server, f"OF_{HEX_C}").returncode == 0  # the prefix in any case
    lines = server.list.read_text(encoding="utf-8").splitlines()
    assert re.fullmatch(rf"- `{HEX_B}` — {ADDED}", lines[1])
    assert re.fullmatch(rf"- `{HEX_C}` — {ADDED}", lines[2])


def test_the_documented_line_takes_a_pasted_copy_id_through_a_real_shell(server):
    # why: the Copy ID form holds backticks. Pasted unquoted or in double quotes, the shell runs
    # them as a command and make gets a broken ID; single-quoted, as the README, the runbook and
    # `make help` write it, it arrives whole.
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    runbook = (REPO / "docs" / "runbooks" / "first-run.md").read_text(encoding="utf-8")
    assert DOCUMENTED in readme and DOCUMENTED in runbook
    assert DOCUMENTED in (REPO / "Makefile").read_text(encoding="utf-8")
    line = DOCUMENTED.replace("<id>", f"- `{PLAUD_A}` —")
    done = subprocess.run(
        ["sh", "-c", f"{line} SERVER=homelab-test REPO_DIR='{server.checkout}'"],
        cwd=REPO,
        env=server.env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert done.returncode == 0, done.stderr
    assert re.fullmatch(rf"- `{HEX_A}` — {ADDED}\n", server.list.read_text(encoding="utf-8"))


def test_consent_add_expands_server_and_repo_dir_on_the_mac(server, tmp_path):
    # why: the server's shell has no SERVER or REPO_DIR. make expands both on the Mac, so the
    # command ssh carries is literal.
    assert consent_add(server, PLAUD_A).returncode == 0
    host, command = server.log.read_text().splitlines()[:2]
    assert host == "host homelab-test"
    assert command.startswith(
        f"command cd '{server.checkout}' && sh docker/consent-add.sh '{HEX_A}'"
    )
    assert "$" not in command
    # local.mk, -include'd, is read from the folder make runs in
    mac = tmp_path / "mac"
    mac.mkdir()
    (mac / "local.mk").write_text(
        "SERVER = homelab-from-local-mk\nREPO_DIR = /srv/example/repo\n", encoding="utf-8"
    )
    makefile = str(REPO / "Makefile")
    dry = subprocess.run(
        ["make", "-n", "-C", str(mac), "-f", makefile, "consent-add", f"ID={PLAUD_A}"],
        env=server.env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert dry.returncode == 0, dry.stderr
    assert 'ssh "homelab-from-local-mk" "cd \'/srv/example/repo\' && ' in dry.stdout
    empty = tmp_path / "no-local-mk"
    empty.mkdir()
    refused = subprocess.run(
        ["make", "-n", "-C", str(empty), "-f", makefile, "consent-add", f"ID={PLAUD_A}"],
        env=server.env,
        capture_output=True,
        text=True,
        check=False,
    )
    assert refused.returncode != 0 and "local.mk" in refused.stderr
