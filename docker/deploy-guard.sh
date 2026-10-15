#!/bin/sh
# `make deploy` runs this first. A deploy recreates the backup container, which stops the job it
# runs: restic and rsync stop cleanly, but the job starts again from the beginning, so a deploy
# during a restore test or a check costs hours. This asks the running backup container whether any
# process holds the ops lock (docker/ops-busy.py), as Status does. FORCE=1 deploys anyway. It reads
# no secret.
set -eu
cd "$(dirname "$0")/.."
[ "${FORCE:-}" = 1 ] && exit 0
ids=$(docker ps -q --filter label=com.docker.compose.project=recordings \
  --filter label=com.docker.compose.service=backup --filter label=com.docker.compose.oneoff=False) \
  || { echo "deploy-guard: docker failed (is the Docker daemon running?)" >&2; exit 1; }
id=$(printf '%s\n' "$ids" | head -n 1)
[ -n "$id" ] || exit 0  # no backup container running: no job to stop
status=0
held=$(docker exec -i "$id" python - /state/locks < docker/ops-busy.py) || status=$?
case "$status" in
  0) exit 0 ;;
  3) echo "make deploy: a job is running${held:+ ($held)}. A deploy now would stop it, and it" \
       "would start again from the beginning. Wait for it (Status shows it), or deploy" \
       "anyway: make deploy FORCE=1" >&2 ;;
  *) echo "make deploy: could not ask the backup container whether a job is running (exit" \
       "$status). make deploy FORCE=1 deploys anyway." >&2 ;;
esac
exit 1
