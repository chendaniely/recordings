#!/bin/sh
# `make deploy` runs this first. A deploy recreates the backup container, which stops the job it
# runs: restic and rsync stop cleanly, but the job starts again from the beginning, so a deploy
# during a restore test or a check costs hours. While the backup service is stopped (the runbook
# stops it, then runs its jobs and the import in one-off containers), `up` would start the loop in
# their way. It also recreates the worker, which stops a Plaud run: a routine sync is safe to
# interrupt, but Sync everything, the first catch-up and the baseline are long (stage 2b spec
# §17.3). So it asks docker/ops-busy.py, as Status does, whether any process holds the ops lock
# (any job), or the plaud lock for one of those three: in the running backup container, or else in
# a one-off backup container on the image the stopped one was made from (this deploy's image may
# not be built yet). flock is per host, so a hold in any container counts, the worker's included,
# through the shared state/locks. FORCE=1 deploys anyway. It reads no secret.
set -eu
cd "$(dirname "$0")/.."
cannot_ask() {
  echo "make deploy: could not ask the backup container whether a job is running ($1)." \
    "make deploy FORCE=1 deploys anyway." >&2
  exit 1
}
[ "${FORCE:-}" = 1 ] && exit 0
ids=$(docker ps -q --filter label=com.docker.compose.project=recordings \
  --filter label=com.docker.compose.service=backup --filter label=com.docker.compose.oneoff=False) \
  || { echo "deploy-guard: docker failed (is the Docker daemon running?)" >&2; exit 1; }
id=$(printf '%s\n' "$ids" | head -n 1)
tag=
if [ -z "$id" ]; then
  # docker ps lists the newest first. The image is read back with inspect, which keeps the
  # reference the container was created from (as docker/up-deployed.sh reads it).
  ids=$(docker ps -a -q --filter label=com.docker.compose.project=recordings \
    --filter label=com.docker.compose.service=backup \
    --filter label=com.docker.compose.oneoff=False) \
    || { echo "deploy-guard: docker failed (is the Docker daemon running?)" >&2; exit 1; }
  stopped=$(printf '%s\n' "$ids" | head -n 1)
  [ -n "$stopped" ] || exit 0  # no backup container at all: nothing deployed to stop or start
  image=$(docker inspect --format '{{.Config.Image}}' "$stopped") \
    || { echo "deploy-guard: docker inspect failed (is the Docker daemon running?)" >&2; exit 1; }
  case "$image" in
    recordings:?*) tag=${image#recordings:} ;;
    *) cannot_ask "it runs no image make deploy tagged" ;;
  esac
fi

# ask <lock name> [job ...]: ops-busy.py's answer (0 free, 3 held), its announcement on stdout.
ask() {
  if [ -n "$id" ]; then
    docker exec -i "$id" python - /state/locks "$@" < docker/ops-busy.py
  else
    RECORDINGS_IMAGE_TAG=$tag docker compose --env-file docker/deploy.env -f docker/compose.yml \
      run --rm --no-deps -T -i backup python - /state/locks "$@" < docker/ops-busy.py
  fi
}

status=0
held=$(ask ops) || status=$?
if [ "$status" = 0 ]; then
  held=$(ask plaud plaud-full plaud-catch-up plaud-baseline) || status=$?
fi
case "$status" in
  0) exit 0 ;;
  3) echo "make deploy: a job is running${held:+ ($held)}. A deploy now would stop it, or start" \
       "the backup service in its way. What a stop costs: a backup, restore test or check starts" \
       "again from the beginning (hours); Sync everything carries on with the recordings not yet" \
       "checked, but a stop loses the one in progress; the first catch-up or the baseline stops" \
       "part-way, and you run it again by hand (the runbook, section 8). Wait for it (Status shows" \
       "it), or deploy anyway: make deploy FORCE=1" >&2 ;;
  *) cannot_ask "exit $status" ;;
esac
exit 1
