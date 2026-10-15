#!/bin/sh
# `make deploy` runs this first. A deploy recreates the backup container, which stops the job it
# runs: restic and rsync stop cleanly, but the job starts again from the beginning, so a deploy
# during a restore test or a check costs hours. While the backup service is stopped (the runbook
# stops it, then runs its jobs and the import in one-off containers), `up` would start the loop in
# their way. This asks whether any process holds the ops lock (docker/ops-busy.py), as Status
# does: in the running backup container, or else in a one-off backup container on the image the
# stopped one was made from (this deploy's image may not be built yet). flock is per host, so a
# hold in any container counts. FORCE=1 deploys anyway. It reads no secret.
set -eu
cd "$(dirname "$0")/.."
[ "${FORCE:-}" = 1 ] && exit 0
ids=$(docker ps -q --filter label=com.docker.compose.project=recordings \
  --filter label=com.docker.compose.service=backup --filter label=com.docker.compose.oneoff=False) \
  || { echo "deploy-guard: docker failed (is the Docker daemon running?)" >&2; exit 1; }
id=$(printf '%s\n' "$ids" | head -n 1)
status=0
if [ -n "$id" ]; then
  held=$(docker exec -i "$id" python - /state/locks < docker/ops-busy.py) || status=$?
else
  # docker ps lists the newest first. The image is read back with inspect, which keeps the
  # reference the container was created from (as docker/up-deployed.sh reads it).
  ids=$(docker ps -a -q --filter label=com.docker.compose.project=recordings \
    --filter label=com.docker.compose.service=backup \
    --filter label=com.docker.compose.oneoff=False) \
    || { echo "deploy-guard: docker failed (is the Docker daemon running?)" >&2; exit 1; }
  id=$(printf '%s\n' "$ids" | head -n 1)
  [ -n "$id" ] || exit 0  # no backup container at all: nothing deployed to stop or start
  image=$(docker inspect --format '{{.Config.Image}}' "$id") \
    || { echo "deploy-guard: docker inspect failed (is the Docker daemon running?)" >&2; exit 1; }
  case "$image" in
    recordings:?*)
      held=$(RECORDINGS_IMAGE_TAG=${image#recordings:} docker compose --env-file docker/deploy.env \
        -f docker/compose.yml run --rm --no-deps -T -i backup python - /state/locks \
        < docker/ops-busy.py) || status=$? ;;
    *) status=1 ;;  # not an image make deploy tagged: nothing to ask with
  esac
fi
case "$status" in
  0) exit 0 ;;
  3) echo "make deploy: a job is running${held:+ ($held)}. A deploy now would stop it (it would" \
       "start again from the beginning), or start the backup service in its way. Wait for it" \
       "(Status shows it), or deploy anyway: make deploy FORCE=1" >&2 ;;
  *) echo "make deploy: could not ask the backup container whether a job is running (exit" \
       "$status). make deploy FORCE=1 deploys anyway." >&2 ;;
esac
exit 1
