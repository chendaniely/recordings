#!/bin/sh
# Runs `docker compose <args>` on the image tag the web container already runs, however it was
# started (make deploy, make rollback, or the runbook's `rc up -d`). recordings.service calls it at
# boot, so a restart never builds, pulls or switches code. It reads no secret.
set -eu
cd "$(dirname "$0")/.."
image=$(docker ps -a --filter label=com.docker.compose.project=recordings \
  --filter label=com.docker.compose.service=web --filter label=com.docker.compose.oneoff=False \
  --format '{{.Image}}' | head -n 1)
case "$image" in
  recordings:?*) ;;
  *) echo "up-deployed: no recordings web container to start again; run make deploy first" >&2
     exit 1 ;;
esac
RECORDINGS_IMAGE_TAG=${image#recordings:} exec docker compose --env-file docker/deploy.env \
  -f docker/compose.yml "$@"
