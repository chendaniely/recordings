import importlib.util
import os
import re
import shutil
import subprocess
import sys
from contextlib import contextmanager
from pathlib import Path

import pytest
import yaml
from recordings import config

REPO = Path(__file__).resolve().parents[3]
COMPOSE = REPO / "docker" / "compose.yml"
DOCKERFILE = REPO / "docker" / "Dockerfile"
TEMPLATE = REPO / "docker" / "deploy.example.conf"
UNIT = REPO / "docker" / "recordings.service"
SET_BY_MAKE = {"RECORDINGS_IMAGE_TAG"}


def compose() -> dict:
    return yaml.safe_load(COMPOSE.read_text(encoding="utf-8"))


def make_dry_run(*args: str) -> subprocess.CompletedProcess:
    """`make -n` prints a recipe without running it; $(error ...) still stops it."""
    if shutil.which("make") is None:
        pytest.skip("make is not installed")
    return subprocess.run(["make", "-n", *args], cwd=REPO, capture_output=True, text=True)


def test_every_compose_variable_is_documented_in_the_template_or_set_by_make():
    used = set(re.findall(r"\$\{(\w+)", COMPOSE.read_text(encoding="utf-8")))
    documented = set(re.findall(r"^(\w+)=", TEMPLATE.read_text(encoding="utf-8"), re.MULTILINE))
    assert used, "compose.yml uses no variables?"
    assert used <= documented | SET_BY_MAKE, (
        f"undocumented: {sorted(used - documented - SET_BY_MAKE)}"
    )


def test_the_web_service_takes_its_trusted_proxy_from_the_deploy_variable():
    web = compose()["services"]["web"]["environment"]
    assert web["FORWARDED_ALLOW_IPS"] == "${RECORDINGS_FORWARDED_ALLOW_IPS:-127.0.0.1,::1}"
    assert "*" not in web["FORWARDED_ALLOW_IPS"]
    assert "FORWARDED_ALLOW_IPS" not in compose()["services"]["backup"]["environment"]


def test_the_template_holds_no_secret_names():
    template = TEMPLATE.read_text(encoding="utf-8")
    for name in ("TOKEN", "API_KEY", "PASSWORD", "SECRET="):
        assert name not in template
    for spec in config.SECRETS.values():
        assert spec.env not in template, spec.env


def test_the_template_is_not_named_like_an_env_file():
    # why: §5. Dan's tool hook blocks reading .env names, and the template must stay readable.
    assert not (REPO / "docker" / "deploy.example.env").exists()
    for doc in ("README.md", "docs/runbooks/first-run.md", "Makefile"):
        assert "deploy.example.env" not in (REPO / doc).read_text(encoding="utf-8"), doc


def test_every_bind_is_long_syntax_and_never_creates_its_host_path():
    # why: Compose's long syntax defaults create_host_path to true; a folder made in place of an
    # unmounted disk would look like an empty archive.
    for name, service in compose()["services"].items():
        for volume in service.get("volumes", []):
            assert isinstance(volume, dict), f"{name}: short syntax {volume!r}"
            if volume["type"] == "bind":
                assert volume["bind"]["create_host_path"] is False, (name, volume["target"])


def test_the_state_and_the_index_have_mounts_of_their_own():
    doc = compose()
    web = {v["target"]: v for v in doc["services"]["web"]["volumes"]}
    assert web["/state"]["type"] == "bind" and web["/index"]["type"] == "volume"
    assert "index" in doc["volumes"]
    # why: Status opens state.db mode=ro in `web` while `backup` writes it in WAL mode. A reader
    # needs state.db-wal and state.db-shm, which exist only while a writer has the database open,
    # so otherwise it must be able to create them: this mount stays writable (Task 15).
    assert not web["/state"].get("read_only"), "web's /state must stay writable for WAL readers"
    assert doc["services"]["web"]["environment"]["RECORDINGS_STATE"] == "/state"
    assert doc["services"]["web"]["environment"]["RECORDINGS_INDEX"] == "/index/index.db"


def test_web_reads_the_state_folder_the_backup_service_writes():
    # why: Status (web) reads the state.db and the ops lock that the loop (backup) writes. Two
    # folders would show, for ever, the last runs Status saw before they split.
    doc = compose()
    mounts = {
        name: {v["target"]: v for v in service["volumes"]}
        for name, service in doc["services"].items()
    }
    assert mounts["web"]["/state"]["source"] == mounts["backup"]["/state"]["source"]
    for name in ("web", "backup"):
        assert doc["services"][name]["environment"]["RECORDINGS_STATE"] == "/state", name


def test_every_service_rotates_its_logs_is_hardened_and_has_a_hostname():
    for name, service in compose()["services"].items():
        assert service["logging"]["driver"] == "json-file", name
        assert {"max-size", "max-file"} <= set(service["logging"]["options"]), name
        assert service["read_only"] is True and service["cap_drop"] == ["ALL"], name
        assert "no-new-privileges:true" in service["security_opt"], name
        assert service["hostname"].startswith("recordings-"), name
        assert service["user"] == "${RECORDINGS_UID:?}:${RECORDINGS_GID:?}", name


def test_the_image_has_a_passwd_entry_for_the_deploy_uid():
    # why: ssh exits 255 ("No user exists for uid") before it reads any option when its uid has
    # no passwd entry, and the containers run as deploy.env's UID. compose_smoke.py runs
    # `ssh -G nas` in the backup service to prove it for real.
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert "ARG APP_UID=10001\n" in dockerfile and "ARG APP_GID=10001\n" in dockerfile
    assert 'useradd --uid "$APP_UID" --gid "$APP_GID"' in dockerfile
    assert "USER ${APP_UID}:${APP_GID}\n" in dockerfile
    assert compose()["services"]["web"]["build"]["args"] == {
        "APP_UID": "${RECORDINGS_UID:?}",
        "APP_GID": "${RECORDINGS_GID:?}",
    }


def test_each_service_names_its_role_and_backup_has_time_to_stop():
    services = compose()["services"]
    assert services["web"]["environment"]["RECORDINGS_ROLE"] == "web"
    assert services["backup"]["environment"]["RECORDINGS_ROLE"] == "backup"
    assert services["backup"]["stop_grace_period"] == "2m"  # restic stops cleanly on SIGTERM
    assert services["backup"]["init"] is True  # reaps the ssh a killed rsync leaves behind


def test_backup_yields_to_web_and_no_tmpfs_is_unsized():
    # why: a restore test or a first backup can run for hours on a small homelab box; web must stay
    # responsive and survive memory pressure. An unsized tmpfs may take half the host's memory.
    # (ops council, Task 14; the cgroup weights and the oom_score_adj checked in a container)
    services = compose()["services"]
    assert services["backup"]["cpu_shares"] < services["web"].get("cpu_shares", 1024)
    assert services["backup"]["oom_score_adj"] > services["web"].get("oom_score_adj", 0)
    assert "mem_limit" not in services["backup"]  # a cap would kill restic mid-backup
    for name, service in services.items():
        for mount in service["tmpfs"]:
            assert re.fullmatch(r"/tmp:size=\d+[mg]", mount), (name, mount)


def test_images_are_tagged_by_commit_and_never_pulled():
    for name, service in compose()["services"].items():
        assert service["image"].startswith("recordings:${RECORDINGS_IMAGE_TAG:?"), name
        assert service["pull_policy"] == "never", name
    makefile = (REPO / "Makefile").read_text(encoding="utf-8")
    assert "git rev-parse --short=12 HEAD" in makefile and "git status --porcelain" in makefile
    assert "RECORDINGS_IMAGE_TAG=$(IMAGE_TAG)" in makefile


def test_a_dirty_tree_is_never_deployed():
    # why: a -dirty image has no commit to roll back to (ops review).
    dirty = make_dry_run("deploy", "IMAGE_TAG=0123456789ab-dirty")
    assert dirty.returncode != 0 and "commit first" in dirty.stderr
    clean = make_dry_run("deploy", "IMAGE_TAG=0123456789ab")
    assert clean.returncode == 0, clean.stderr
    assert "RECORDINGS_IMAGE_TAG=0123456789ab docker compose" in clean.stdout
    assert "up -d --build" in clean.stdout


def test_a_rollback_starts_an_old_image_and_never_builds_one():
    # why: with build: in compose.yml, `up` builds a missing tag from today's code; --no-build
    # makes a missing tag an error instead (checked with Compose v5.5.1, 2026-10-08).
    assert make_dry_run("rollback").returncode != 0
    back = make_dry_run("rollback", "TAG=0123456789ab")
    assert back.returncode == 0, back.stderr
    assert "RECORDINGS_IMAGE_TAG=0123456789ab docker compose" in back.stdout
    assert "up -d --no-build" in back.stdout


def test_the_smoke_script_parses_and_creates_every_compose_secret():
    # why: nothing else without Docker reads scripts/compose_smoke.py; a stray line once made it a
    # SyntaxError that only `make deploy-smoke` would have found. ntfy_token is the optional one:
    # its source is /dev/null, so the script makes no file for it.
    path = REPO / "scripts" / "compose_smoke.py"
    spec = importlib.util.spec_from_file_location("compose_smoke", path)
    smoke = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(smoke)
    assert set(smoke.SECRETS) | {"ntfy_token"} == set(compose()["secrets"])
    assert not os.access(path, os.X_OK), "no shebang, so no exec bit"


def test_the_boot_unit_waits_for_docker_the_disk_and_tailscale():
    unit = UNIT.read_text(encoding="utf-8")
    for line in (
        "Requires=docker.service",
        "After=docker.service tailscaled.service",
        "RequiresMountsFor=/srv/recordings",
        "Type=oneshot",
        "RemainAfterExit=yes",
        "WorkingDirectory=/srv/recordings/repo",
        "WantedBy=multi-user.target",
        "ExecStart=/bin/sh docker/up-deployed.sh up -d --no-build",
    ):
        assert f"\n{line}\n" in unit, line
    (pre,) = [line for line in unit.splitlines() if line.startswith("ExecStartPre=")]
    assert "tailscale0" in pre and "-ge 120 ]" in pre


def test_at_boot_the_containers_start_on_the_tag_they_last_ran(tmp_path):
    # why: compose.yml requires RECORDINGS_IMAGE_TAG, and pull_policy: never can't fetch one. The
    # tag the web container runs is the deployed one, however it was started.
    fake = tmp_path / "docker"
    # `ps -q` lists container IDs; `inspect` prints the image reference the container was created from
    fake.write_text(
        '#!/bin/sh\nif [ "$FAKE_DOWN" ]; then echo "Cannot connect to the Docker daemon" >&2; exit 1; fi\n'
        'if [ "$1" = ps ]; then [ "$FAKE_IMAGE" ] && echo abc123; exit 0; fi\n'
        'if [ "$1" = inspect ]; then echo "$FAKE_IMAGE"; exit 0; fi\n'
        'echo "RECORDINGS_IMAGE_TAG=$RECORDINGS_IMAGE_TAG $*"\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin", "FAKE_IMAGE": "recordings:0123456789ab"}
    script = str(REPO / "docker" / "up-deployed.sh")
    up = subprocess.run(
        ["sh", script, "up", "-d", "--no-build"], env=env, capture_output=True, text=True
    )
    assert up.returncode == 0, up.stderr
    assert up.stdout == (
        "RECORDINGS_IMAGE_TAG=0123456789ab compose --env-file docker/deploy.env "
        "-f docker/compose.yml up -d --no-build\n"
    )
    none = subprocess.run(
        ["sh", script, "up", "-d", "--no-build"],
        env={**env, "FAKE_IMAGE": ""},
        capture_output=True,
        text=True,
    )
    assert none.returncode != 0 and "make deploy" in none.stderr and none.stdout == ""
    down = subprocess.run(
        ["sh", script, "up", "-d", "--no-build"],
        env={**env, "FAKE_DOWN": "1"},
        capture_output=True,
        text=True,
    )
    assert down.returncode != 0 and "make deploy" not in down.stderr and down.stdout == ""
    assert "docker" in down.stderr and "daemon" in down.stderr


@contextmanager
def ops_lock_held(folder: Path, announced: str):
    """Another process holds the ops lock, as the loop's run_job does, with `announced` in the lock
    file, as Task 15's announce writes it."""
    holder = subprocess.Popen(
        [
            sys.executable,
            "-c",
            (
                "import pathlib, sys\n"
                "from recordings.locks import OPS_LOCK, Locks\n"
                "folder = pathlib.Path(sys.argv[1])\n"
                "with Locks(folder, timeout=0).hold(OPS_LOCK):\n"
                "    (folder / f'{OPS_LOCK}.lock').write_text(sys.argv[2], encoding='utf-8')\n"
                "    print('held', flush=True)\n"
                "    sys.stdin.read()\n"
            ),
            str(folder),
            announced,
        ],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        text=True,
    )
    try:
        assert holder.stdout.readline() == "held\n"
        yield
    finally:
        holder.communicate("")


def test_the_ops_probe_sees_a_held_lock_and_what_it_announced(tmp_path):
    # why: deploy-guard.sh asks the backup container with this file. flock is per host, so a hold
    # by the loop, or by a one-off `rc run` in another container, shows the same.
    probe = [sys.executable, str(REPO / "docker" / "ops-busy.py"), str(tmp_path / "locks")]
    assert subprocess.run(probe).returncode == 0  # never taken: no lock file yet
    with ops_lock_held(tmp_path / "locks", '{"job": "restore-test"}'):
        busy = subprocess.run(probe, capture_output=True, text=True)
        assert busy.returncode == 3 and busy.stdout == '{"job": "restore-test"}\n'
    assert subprocess.run(probe).returncode == 0  # let go: free again


def test_a_deploy_waits_while_a_job_runs_unless_forced(tmp_path):
    # why: recreating backup stops the job it runs (restic and rsync stop cleanly), and the job
    # starts again from the beginning: hours for a restore test or a check (ops council, Task 14).
    fake = tmp_path / "docker"
    # `ps -q` lists the running backup container. The guard pipes docker/ops-busy.py into that
    # container's python; the fake runs it here instead, on the test's own lock folder.
    fake.write_text(
        '#!/bin/sh\nif [ "$FAKE_DOWN" ]; then echo "Cannot connect to the Docker daemon" >&2; exit 1; fi\n'
        'if [ "$1" = ps ]; then [ "$FAKE_ID" ] && echo "$FAKE_ID"; exit 0; fi\n'
        'if [ "$*" = "exec -i $FAKE_ID python - /state/locks" ]; then\n'
        '  exec "$FAKE_PYTHON" - "$FAKE_LOCKS"\nfi\nexit 99\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "FAKE_ID": "abc123",
        "FAKE_PYTHON": sys.executable,
        "FAKE_LOCKS": str(tmp_path / "locks"),
    }

    def guard(**extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["sh", str(REPO / "docker" / "deploy-guard.sh")],
            env={**env, **extra},
            capture_output=True,
            text=True,
        )

    assert guard().returncode == 0  # idle
    assert guard(FAKE_ID="").returncode == 0  # no backup container: no job to stop
    down = guard(FAKE_DOWN="1")
    assert down.returncode != 0 and "daemon" in down.stderr
    with ops_lock_held(tmp_path / "locks", '{"job": "restore-test"}'):
        busy = guard()
        assert busy.returncode != 0 and "restore-test" in busy.stderr and "FORCE=1" in busy.stderr
        assert guard(FORCE="1").returncode == 0
    broken = guard(FAKE_PYTHON="/nonexistent")  # the question itself failed: say so, don't deploy
    assert broken.returncode != 0 and "could not ask" in broken.stderr
    deploy = make_dry_run("deploy", "IMAGE_TAG=0123456789ab").stdout.splitlines()
    (asked,) = [i for i, line in enumerate(deploy) if "sh docker/deploy-guard.sh" in line]
    (up,) = [i for i, line in enumerate(deploy) if " up -d --build" in line]
    assert asked < up
    # and it fails, rather than passing silently, when web never passes its health check, which the
    # smoke test proves passes in the hardened container
    assert deploy[up].endswith(" up -d --build --wait --wait-timeout 180")
    smoke = (REPO / "scripts" / "compose_smoke.py").read_text(encoding="utf-8")
    assert '"up", "-d", "--wait", "--wait-timeout", "120", "web"' in smoke
    forced = make_dry_run("deploy", "IMAGE_TAG=0123456789ab", "FORCE=1").stdout
    assert "FORCE=1 sh docker/deploy-guard.sh" in forced


def test_a_deploy_asks_a_one_off_backup_container_while_the_service_is_stopped(tmp_path):
    # why: the final review's minor 1. The runbook stops backup (steps 2.6 and 4), then runs its
    # jobs and the import in one-off containers: a hold matters most then, and the guard looked
    # only at a running backup container, so `up -d` started the loop in their way.
    fake = tmp_path / "docker"
    # `ps -q` lists running containers (none), `ps -a -q` stopped ones too, and `inspect` the image
    # one was made from. The one-off `compose run` gets docker/ops-busy.py on stdin; the fake runs
    # it here instead, on the test's own lock folder.
    fake.write_text(
        '#!/bin/sh\nif [ "$FAKE_DOWN" ]; then echo "Cannot connect to the Docker daemon" >&2; exit 1; fi\n'
        'if [ "$1 $2" = "ps -a" ]; then [ "$FAKE_STOPPED" ] && echo "$FAKE_STOPPED"; exit 0; fi\n'
        'if [ "$1" = ps ]; then exit 0; fi\n'
        'if [ "$1" = inspect ]; then echo "$FAKE_IMAGE"; exit 0; fi\n'
        'if [ "$RECORDINGS_IMAGE_TAG $*" = "0123456789ab compose --env-file docker/deploy.env '
        '-f docker/compose.yml run --rm --no-deps -T -i backup python - /state/locks" ]; then\n'
        '  exec "$FAKE_PYTHON" - "$FAKE_LOCKS"\nfi\nexit 99\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = {
        "PATH": f"{tmp_path}:/usr/bin:/bin",
        "FAKE_STOPPED": "def456",
        "FAKE_IMAGE": "recordings:0123456789ab",
        "FAKE_PYTHON": sys.executable,
        "FAKE_LOCKS": str(tmp_path / "locks"),
    }

    def guard(**extra: str) -> subprocess.CompletedProcess:
        return subprocess.run(
            ["sh", str(REPO / "docker" / "deploy-guard.sh")],
            env={**env, **extra},
            capture_output=True,
            text=True,
        )

    assert guard().returncode == 0  # stopped, and nothing holds the lock
    with ops_lock_held(tmp_path / "locks", '{"job": "import"}'):
        busy = guard()
        assert busy.returncode != 0 and "import" in busy.stderr and "FORCE=1" in busy.stderr
        assert guard(FORCE="1").returncode == 0
    assert guard(FAKE_STOPPED="").returncode == 0  # no backup container at all: nothing deployed
    for extra in ({"FAKE_IMAGE": "postgres:16"}, {"FAKE_PYTHON": "/nonexistent"}):
        broken = guard(**extra)  # nothing to ask with, or the question failed: say so, don't deploy
        assert broken.returncode != 0 and "could not ask" in broken.stderr
    down = guard(FAKE_DOWN="1")
    assert down.returncode != 0 and "daemon" in down.stderr


def test_old_images_are_pruned_but_rollback_targets_and_used_ones_stay(tmp_path):
    # why: each make deploy leaves an image, with its own venv, on the system disk, and
    # `docker image prune -a` would take every rollback target with it (ops council, Task 14).
    listed = (
        "2026-10-01 09:00:00 +0000 UTC|00000000000a",
        "2026-10-12 09:00:00 +0000 UTC|00000000000d",
        "2026-10-05 09:00:00 +0000 UTC|00000000000b",
        "2026-10-09 09:00:00 +0000 UTC|00000000000c",
        "2026-10-13 09:00:00 +0000 UTC|smoke",
        "2026-10-02 09:00:00 +0000 UTC|0123456789ab-dirty",
    )
    fake = tmp_path / "docker"
    fake.write_text(
        '#!/bin/sh\nif [ "$FAKE_DOWN" ]; then echo "Cannot connect to the Docker daemon" >&2; exit 1; fi\n'
        'if [ "$1 $2" = "image ls" ]; then printf "%s\\n" '
        + " ".join(f"'{line}'" for line in listed)
        + "; exit 0; fi\n"
        'if [ "$1" = ps ]; then echo recordings:00000000000a; exit 0; fi\n'
        'if [ "$1 $2" = "image rm" ]; then echo "removed $3"; exit 0; fi\nexit 99\n',
        encoding="utf-8",
    )
    fake.chmod(0o755)
    env = {"PATH": f"{tmp_path}:/usr/bin:/bin"}
    script = str(REPO / "docker" / "prune-images.sh")
    run = subprocess.run(["sh", script, "2"], env=env, capture_output=True, text=True)
    assert run.returncode == 0, run.stderr
    # d and c are the newest two; a is older, but a container was made from it; smoke and the
    # -dirty tag aren't commit tags
    assert [line for line in run.stdout.splitlines() if line.startswith("removed")] == [
        "removed recordings:00000000000b"
    ]
    assert subprocess.run(["sh", script, "five"], env=env, capture_output=True).returncode == 2
    down = subprocess.run(
        ["sh", script], env={**env, "FAKE_DOWN": "1"}, capture_output=True, text=True
    )
    assert down.returncode != 0 and "daemon" in down.stderr
    assert "sh docker/prune-images.sh 5" in make_dry_run("prune-images").stdout
    assert "sh docker/prune-images.sh 1" in make_dry_run("prune-images", "KEEP=1").stdout
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    assert "make prune-images" in readme and "docker image prune -a` deletes" not in readme


def test_every_base_image_is_pinned_by_digest():
    # why: a tag can be moved to other bytes; a digest can't. Dependabot moves the digests.
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    froms = re.findall(r"^FROM\s+(\S+)\s+AS\s+(\S+)", dockerfile, re.MULTILINE)
    assert len(froms) == len(re.findall(r"^FROM\s", dockerfile, re.MULTILINE)), "a FROM without AS"
    for image, _ in froms:
        assert re.search(r":[\w.-]+@sha256:[0-9a-f]{64}$", image), image
    stages = {stage for _, stage in froms}
    for source in re.findall(r"--from=(\S+)", dockerfile):
        assert source in stages, f"COPY --from={source} takes an image no pinned FROM names"
    updates = yaml.safe_load((REPO / ".github" / "dependabot.yml").read_text(encoding="utf-8"))
    docker = [u["directory"] for u in updates["updates"] if u["package-ecosystem"] == "docker"]
    assert docker == ["/docker"]


def test_the_images_restic_is_the_one_fetch_restic_pins():
    # why: one restic, checked against one release's SHA256SUMS, in CI, on the Mac, in the image
    # and in the runbook's spike (security review).
    spec = importlib.util.spec_from_file_location(
        "fetch_restic", REPO / "scripts" / "fetch_restic.py"
    )
    fetch_restic = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(fetch_restic)
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    assert f"ARG RESTIC_VERSION={fetch_restic.VERSION}\n" in dockerfile
    found = {
        arch: (sum_, bin_)
        for arch, sum_, bin_ in re.findall(
            r"^\s+(amd64|arm64)\) sum=([0-9a-f]{64}); bin=([0-9a-f]{64}) ;;",
            dockerfile,
            re.MULTILINE,
        )
    }
    assert found == {
        "amd64": (
            fetch_restic.PINNED[("linux", "x86_64")][1],
            fetch_restic.BINARY_SHA256["linux_amd64"],
        ),
        "arm64": (
            fetch_restic.PINNED[("linux", "aarch64")][1],
            fetch_restic.BINARY_SHA256["linux_arm64"],
        ),
    }
    runbook = (REPO / "docs" / "runbooks" / "first-run.md").read_text(encoding="utf-8")
    for arch, (sum_, _) in found.items():
        # each sum sits on the case arm of its own architecture: x86_64 is amd64, aarch64 arm64
        arm = {"amd64": "x86_64)  ARCH=linux_amd64", "arm64": "aarch64) ARCH=linux_arm64"}[arch]
        assert f"{arm} SUM={sum_} ;;" in runbook, f"the runbook's {arch} sum differs"
    assert set(re.findall(r"restic_(\d+\.\d+\.\d+)_", runbook)) == {fetch_restic.VERSION}


def test_the_runtime_image_has_the_time_zone_database():
    # why: zoneinfo reads the system's database and uv.lock has no tzdata wheel. Without it every
    # ZoneInfo(...) fails: the import places no row, and Status shows no local time.
    dockerfile = DOCKERFILE.read_text(encoding="utf-8")
    runtime = dockerfile[dockerfile.index("AS runtime") :]
    install = re.search(r"apt-get install[^;]*;", runtime)
    assert install and "tzdata" in install.group(0).split()


def test_the_backup_service_reads_the_archive_and_takes_secrets_only_from_files():
    service = compose()["services"]["backup"]
    archive = next(v for v in service["volumes"] if v["target"] == "/archive")
    assert archive["read_only"] is True
    env = service["environment"]
    for key, value in env.items():
        if key.endswith("_FILE"):
            # the optional ntfy token's path appears only when deploy.env sets its host file
            assert value.removeprefix("${RECORDINGS_NTFY_AUTH_HOST:+").startswith(
                "/run/secrets/"
            ), key
    for leaked in (
        "RESTIC_PASSWORD",
        "RECORDINGS_NTFY_TOPIC",
        "RECORDINGS_DEADMAN_URL",
        "RECORDINGS_NTFY_TOKEN",
    ):
        assert leaked not in env
    assert "secrets" not in compose()["services"]["web"], "a secret mounted in web"
    assert service["command"] == ["recordings", "ops", "--loop"]
    assert set(service["secrets"]) <= set(compose()["secrets"])
    assert "nas_cacert" in service["secrets"]  # rest-server's certificate, for [backup] cacert
    template = TEMPLATE.read_text(encoding="utf-8")
    assert all(name in template for name in compose()["secrets"]), "a secret file is undocumented"


def test_the_writer_identity_comes_from_config_never_the_host_name():
    config_text = (REPO / "config.example.toml").read_text(encoding="utf-8")
    assert 'writer_id = "homelab"' in config_text and "writer_host" not in config_text
    assert "WRITER" not in COMPOSE.read_text(encoding="utf-8")


def test_no_test_sees_this_machines_settings():
    # why: the server's checkout holds a real config.toml, and a shell can export RECORDINGS_* or
    # RESTIC_* variables; the root conftest.py hides both from every test (security review).
    leaked = [
        name
        for name in os.environ
        if name.startswith(("RECORDINGS_", "RESTIC_"))
        and name not in ("RECORDINGS_REQUIRE_RESTIC", "RECORDINGS_TEST_RESTIC")
    ]
    assert leaked == []
    assert config.DEFAULT_PATH.is_absolute() and not config.DEFAULT_PATH.exists()


def test_the_ci_switches_survive_the_fixture(no_settings_from_this_machine):
    # why: CI sets RECORDINGS_REQUIRE_RESTIC=1 (Task 11), so a missing restic fails the
    # real-restic tests instead of skipping them. The fixture must never hide it.
    hide = no_settings_from_this_machine
    environ = {
        "RECORDINGS_REQUIRE_RESTIC": "1",
        "RECORDINGS_TEST_RESTIC": "/opt/restic",
        "RECORDINGS_ARCHIVE": "/srv/archive",
        "RESTIC_PASSWORD_FILE": "/run/pw",
        "PATH": "/usr/bin",
    }
    assert hide(environ) == ["RECORDINGS_ARCHIVE", "RESTIC_PASSWORD_FILE"]


def test_the_readme_names_every_secret_and_environment_setting():
    # why: a setting only the code knows about is one nobody sets. Every secret's variable, later
    # stages' included, and RECORDINGS_ALLOWED_HOSTS have a row in the README's settings table.
    readme = (REPO / "README.md").read_text(encoding="utf-8")
    table = readme[readme.index("## Configuration and secrets") :]
    for spec in config.SECRETS.values():
        assert f"`{spec.env}`" in table, spec.env
    assert "`RECORDINGS_ALLOWED_HOSTS`" in table


def test_the_ntfy_token_is_an_optional_secret_in_backup_only():
    # why: a token-protected ntfy server needs it (Task 13), but the app reads an EMPTY token file
    # as an error, which _alert_config logs and treats as "alerts are not configured". So the
    # secret is mounted and named only when deploy.env sets RECORDINGS_NTFY_AUTH_HOST.
    doc = compose()
    env = doc["services"]["backup"]["environment"]
    assert (
        env["RECORDINGS_NTFY_TOKEN_FILE"] == "${RECORDINGS_NTFY_AUTH_HOST:+/run/secrets/ntfy_token}"
    )
    assert "ntfy_token" in doc["services"]["backup"]["secrets"]
    assert doc["secrets"]["ntfy_token"]["file"] == "${RECORDINGS_NTFY_AUTH_HOST:-/dev/null}"
    template = TEMPLATE.read_text(encoding="utf-8")
    assert re.search(r"^RECORDINGS_NTFY_AUTH_HOST=$", template, re.MULTILINE), "unset by default"


def test_the_example_config_backs_up_the_deploy_file_where_compose_mounts_it():
    # why: [backup] extra_paths defaults to [], so a config.toml written without this line would
    # silently leave deploy.env (the UID, the paths: what a restore needs) out of every backup.
    import tomllib

    example = tomllib.loads((REPO / "config.example.toml").read_text(encoding="utf-8"))
    mounts = {v["source"]: v["target"] for v in compose()["services"]["backup"]["volumes"]}
    target = mounts[
        "${RECORDINGS_DEPLOY_ENV_HOST:?set RECORDINGS_DEPLOY_ENV_HOST in docker/deploy.env}"
    ]
    assert target in example["backup"]["extra_paths"]
