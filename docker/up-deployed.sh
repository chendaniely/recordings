#!/bin/sh
# Runs `docker compose <args>` on the image tag the web container already runs, however it was
# started (make deploy, make rollback, or the runbook's `rc up -d`). recordings.service calls it at
# boot, so a restart never builds, pulls or switches code. It reads no secret.
set -eu
cd "$(dirname "$0")/.."
# docker ps lists the newest container first. The image is read back with inspect, which keeps the
# reference the container was created from (ps prints an image ID once its tag has moved).
ids=$(docker ps -a -q --filter label=com.docker.compose.project=recordings \
  --filter label=com.docker.compose.service=web --filter label=com.docker.compose.oneoff=False) \
  || { echo "up-deployed: docker failed (is the Docker daemon running?)" >&2; exit 1; }
id=$(printf '%s\n' "$ids" | head -n 1)
image=
if [ -n "$id" ]; then
  image=$(docker inspect --format '{{.Config.Image}}' "$id") \
    || { echo "up-deployed: docker inspect failed (is the Docker daemon running?)" >&2; exit 1; }
fi
case "$image" in
  recordings:?*) ;;
  *) echo "up-deployed: no recordings web container to start again; run make deploy first" >&2
     exit 1 ;;
esac
RECORDINGS_IMAGE_TAG=${image#recordings:} exec docker compose --env-file docker/deploy.env \
  -f docker/compose.yml "$@"
