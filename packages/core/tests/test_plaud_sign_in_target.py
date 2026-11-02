"""`make plaud-sign-in` (stage 2b spec §10.5, §21's shell targets), run for real against a fake
vendor CLI and a fake `ssh` on PATH: the real ones are never run. The ssh command runs in a
temporary folder that stands for the server's checkout. The token is invented."""

import os
import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[3]
FAKE_TOKEN = '{"access_token": "fake-access-do-not-pass", "refresh_token": "fake-refresh-x"}'
IMPORT = "sh docker/up-deployed.sh exec -T worker recordings plaud token-import -"


def run(command: list[str], *, cwd: Path, env: dict[str, str]) -> subprocess.CompletedProcess:
    return subprocess.run(command, cwd=cwd, env=env, capture_output=True, text=True, check=False)


@pytest.fixture
def sign_in(tmp_path):
    """A stand-in server checkout whose up-deployed.sh records its arguments and its stdin (and
    exits with $FAKE_IMPORT_EXIT); an ssh that logs the host and the command and runs it there,
    stdin passed on (or exits with $FAKE_SSH_EXIT); and a vendor CLI whose `login` logs its HOME and
    that folder's mode, then writes the token file there (unless $FAKE_LOGIN_FAILS or
    $FAKE_LOGIN_NO_FILE)."""
    if shutil.which("make") is None:
        pytest.skip("make is not installed")
    checkout = tmp_path / "server" / "repo"
    (checkout / "docker").mkdir(parents=True)
    log, received = tmp_path / "ssh.log", tmp_path / "received.json"
    (checkout / "docker" / "up-deployed.sh").write_text(
        f'printf "up-deployed %s\\n" "$*" >> "{log}"\n'
        f'cat > "{received}"\n'
        'exit "${FAKE_IMPORT_EXIT:-0}"\n',
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
    home_log = tmp_path / "plaud-home.log"
    store = "$HOME/.plaud"
    (bin_dir / "plaud").write_text(
        "#!/bin/sh\n"
        f'printf "%s %s %s\\n" "$1" "$HOME" "$(ls -ld "$HOME" | cut -c1-10)" >> "{home_log}"\n'
        '[ -z "${FAKE_LOGIN_FAILS:-}" ] || exit 1\n'
        '[ -z "${FAKE_LOGIN_NO_FILE:-}" ] || exit 0\n'
        f'mkdir -p "{store}"\n'
        f"printf '%s' '{FAKE_TOKEN}' > \"{store}/tokens.json\"\n",
        encoding="utf-8",
    )
    for fake in ("ssh", "plaud"):
        (bin_dir / fake).chmod(0o755)
    temp = tmp_path / "mac-tmp"
    temp.mkdir()
    hidden = ("SERVER", "REPO_DIR", "PLAUD_CLI", "ID")  # make would read them as the target's
    env = {k: v for k, v in os.environ.items() if k not in hidden}
    env |= {"PATH": f"{bin_dir}{os.pathsep}{os.environ['PATH']}", "TMPDIR": f"{temp}/"}
    return SimpleNamespace(
        checkout=checkout, log=log, received=received, home_log=home_log, temp=temp, env=env
    )


def plaud_sign_in(sign_in, **extra: str) -> subprocess.CompletedProcess:
    return run(
        [
            "make",
            "--no-print-directory",
            "plaud-sign-in",
            "SERVER=homelab-test",
            f"REPO_DIR={sign_in.checkout}",
        ],
        cwd=REPO,
        env=sign_in.env | extra,
    )


def logged_home(sign_in) -> tuple[str, str, str]:
    (line,) = sign_in.home_log.read_text(encoding="utf-8").splitlines()
    command, home, mode = line.split(" ")
    return command, home, mode


def test_plaud_sign_in_pipes_the_token_file_to_token_import_and_removes_its_home(sign_in):
    # §10.5: SERVER and REPO_DIR expanded on the Mac; the vendor CLI's login under a temporary
    # HOME in $TMPDIR at 0700; the token file over ssh's stdin, never in an argument.
    done = plaud_sign_in(sign_in)
    assert done.returncode == 0, done.stderr
    assert "rc exec worker recordings plaud check" in done.stdout
    command, home, mode = logged_home(sign_in)
    assert command == "login" and mode == "drwx------"
    assert Path(home).parent == sign_in.temp and Path(home).name.startswith("plaud-sign-in.")
    assert not Path(home).exists()  # removed by the trap
    assert list(sign_in.temp.iterdir()) == []
    host, ssh_command, up = sign_in.log.read_text(encoding="utf-8").splitlines()
    assert host == "host homelab-test"
    assert ssh_command == f"command cd '{sign_in.checkout}' && {IMPORT}"
    assert "$" not in ssh_command  # expanded on the Mac: the server's shell has neither variable
    assert up == "up-deployed exec -T worker recordings plaud token-import -"
    assert sign_in.received.read_text(encoding="utf-8") == FAKE_TOKEN
    for text in (sign_in.log.read_text(encoding="utf-8"), done.stdout, done.stderr):
        assert "fake-access-do-not-pass" not in text


def test_a_failed_login_sends_nothing_and_removes_its_home(sign_in):
    for fault in ("FAKE_LOGIN_FAILS", "FAKE_LOGIN_NO_FILE"):
        sign_in.home_log.unlink(missing_ok=True)
        done = plaud_sign_in(sign_in, **{fault: "1"})
        assert done.returncode != 0 and "step 2 failed" in done.stderr, fault
        assert "nothing was sent" in done.stderr
        _, home, _ = logged_home(sign_in)
        assert not Path(home).exists() and list(sign_in.temp.iterdir()) == []
    assert not sign_in.log.exists()  # ssh never ran


@pytest.mark.parametrize(
    ("extra", "words"),
    [
        ({"FAKE_SSH_EXIT": "255"}, ("step 3 failed", "could not be reached")),
        ({"FAKE_IMPORT_EXIT": "77"}, ("step 4 failed", "nothing was installed")),
        ({"FAKE_IMPORT_EXIT": "69"}, ("step 4 failed", "Plaud could not be reached")),
        ({"FAKE_IMPORT_EXIT": "78"}, ("step 4 failed", "folder or the settings")),
        ({"FAKE_IMPORT_EXIT": "1"}, ("step 4 failed", "token-import exited 1")),
    ],
)
def test_each_failure_on_the_way_names_its_step_and_removes_the_home(sign_in, extra, words):
    done = plaud_sign_in(sign_in, **extra)
    assert done.returncode != 0
    assert all(w in done.stderr for w in words), done.stderr
    assert "done." not in done.stdout
    _, home, _ = logged_home(sign_in)
    assert not Path(home).exists() and list(sign_in.temp.iterdir()) == []


def test_plaud_sign_in_needs_server_and_repo_dir_from_local_mk(sign_in, tmp_path):
    makefile = str(REPO / "Makefile")
    empty = tmp_path / "no-local-mk"
    empty.mkdir()
    refused = run(
        ["make", "-n", "-C", str(empty), "-f", makefile, "plaud-sign-in"],
        cwd=REPO,
        env=sign_in.env,
    )
    assert refused.returncode != 0 and "local.mk" in refused.stderr
    mac = tmp_path / "mac"
    mac.mkdir()
    (mac / "local.mk").write_text(
        "SERVER = homelab-from-local-mk\nREPO_DIR = /srv/example/repo\n", encoding="utf-8"
    )
    dry = run(
        ["make", "-n", "-C", str(mac), "-f", makefile, "plaud-sign-in"], cwd=REPO, env=sign_in.env
    )
    assert dry.returncode == 0, dry.stderr
    assert f'ssh "homelab-from-local-mk" "cd \'/srv/example/repo\' && {IMPORT}"' in dry.stdout
    assert not sign_in.home_log.exists()  # -n ran nothing
