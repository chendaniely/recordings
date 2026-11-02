"""The first catch-up's make targets (stage 2b spec §11.6, §17.5; area A5): `make plaud-dry-run`
and `make plaud-catch-up`, run on the server in its checkout. Run here for real against a stand-in
`docker/up-deployed.sh` that only records its arguments and answers each step's exit code: never
against the server, Docker, Plaud or an archive."""

import shutil
import subprocess
from pathlib import Path
from types import SimpleNamespace

import pytest

REPO = Path(__file__).resolve().parents[3]
STEPS = {
    "stop": "stop backup",
    "pre": "run --rm backup recordings backup run --tag pre-import",
    "baseline": "exec -T worker recordings plaud baseline",
    "catch_up": "exec -T worker recordings plaud sync --catch-up",
    "validate": "run --rm web recordings validate /archive --deep",
    "post": "run --rm backup recordings backup run --tag post-import",
    "start": "start backup",
    "dry_run": "exec -T worker recordings plaud sync --dry-run --list",
    "consent": "exec -T worker recordings plaud consent",
}


@pytest.fixture
def server(tmp_path):
    """A checkout standing for the server's: its up-deployed.sh logs each call and exits with
    the code a test sets for that step (a file named after the step)."""
    if shutil.which("make") is None:
        pytest.skip("make is not installed")
    checkout = tmp_path / "repo"
    (checkout / "docker").mkdir(parents=True)
    log, codes = tmp_path / "calls.log", tmp_path / "codes"
    codes.mkdir()
    cases = "\n".join(f'  "{args}") step={name} ;;' for name, args in STEPS.items())
    (checkout / "docker" / "up-deployed.sh").write_text(
        f'printf "%s\\n" "$*" >> "{log}"\n'
        f'case "$*" in\n{cases}\n  *) step=other ;;\nesac\n'
        f'[ -f "{codes}/$step" ] && exit "$(cat "{codes}/$step")"\nexit 0\n',
        encoding="utf-8",
    )

    def make(target: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["make", "--no-print-directory", "-f", str(REPO / "Makefile"), target],
            cwd=checkout,
            capture_output=True,
            text=True,
            check=False,
        )

    def exits(step: str, code: int) -> None:
        (codes / step).write_text(str(code), encoding="utf-8")

    def calls() -> list[str]:
        return log.read_text(encoding="utf-8").splitlines() if log.exists() else []

    return SimpleNamespace(make=make, exits=exits, calls=calls)


def test_the_catch_up_runs_the_specs_order_and_starts_the_backup_service_again(server):
    done = server.make("plaud-catch-up")
    assert done.returncode == 0, done.stderr
    assert server.calls() == [
        STEPS[s] for s in ("stop", "pre", "baseline", "catch_up", "validate", "post", "start")
    ]
    assert "You may start tagging now." in done.stdout


def test_a_catch_up_with_some_failed_recordings_goes_on(server):
    server.exits("catch_up", 2)
    done = server.make("plaud-catch-up")
    assert done.returncode == 0
    assert STEPS["post"] in server.calls()
    assert "some recordings failed" in done.stderr


@pytest.mark.parametrize(
    ("step", "named", "after"),
    [
        ("pre", "step 2", "baseline"),
        ("baseline", "step 3", "catch_up"),
        ("catch_up", "step 4", "validate"),
        ("validate", "step 5", "post"),
    ],
)
def test_each_failure_names_its_step_stops_and_still_starts_the_backup_service(
    server, step, named, after
):
    server.exits(step, 69 if step == "catch_up" else 1)
    done = server.make("plaud-catch-up")
    assert done.returncode != 0
    assert f"make plaud-catch-up: {named} failed" in done.stderr
    assert STEPS[after] not in server.calls()
    assert server.calls()[-1] == STEPS["start"]
    assert "You may start tagging now." not in done.stdout


def test_the_dry_run_lists_then_shows_the_consent_list(server):
    done = server.make("plaud-dry-run")
    assert done.returncode == 0
    assert server.calls() == [STEPS["dry_run"], STEPS["consent"]]


def test_a_held_dry_run_says_so_and_fails(server):
    server.exits("dry_run", 1)
    done = server.make("plaud-dry-run")
    assert done.returncode != 0
    assert "held" in done.stderr
    assert server.calls() == [STEPS["dry_run"], STEPS["consent"]]


def test_make_help_lists_both(server):
    done = subprocess.run(
        ["make", "--no-print-directory", "help"],
        cwd=REPO,
        capture_output=True,
        text=True,
        check=True,
    )
    assert "plaud-dry-run" in done.stdout and "plaud-catch-up" in done.stdout
