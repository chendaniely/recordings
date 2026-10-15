#!/bin/sh
# Removes old recordings:<commit> images, keeping the newest KEEP (the argument, default 5) as
# rollback targets. Each `make deploy` leaves one on the system disk, with its own copy of the venv.
# It never removes an image a container (running or stopped) was made from: the boot starts the
# containers again from it. It touches only commit tags (12 hex digits), never smoke, dev or demo.
# `docker image prune -a` would remove every rollback target at once. It reads no secret.
set -eu
keep=${1:-5}
case "$keep" in
  ''|*[!0-9]*) echo "prune-images: KEEP must be a whole number" >&2; exit 2 ;;
esac
listed=$(docker image ls recordings --format '{{.CreatedAt}}|{{.Tag}}') \
  || { echo "prune-images: docker failed (is the Docker daemon running?)" >&2; exit 1; }
used=$(docker ps -a --format '{{.Image}}') \
  || { echo "prune-images: docker failed (is the Docker daemon running?)" >&2; exit 1; }
n=0
printf '%s\n' "$listed" | sort -r | while IFS='|' read -r _ tag; do
  case "$tag" in
    [0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f][0-9a-f]) ;;
    *) continue ;;
  esac
  n=$((n + 1))
  if [ "$n" -le "$keep" ]; then
    echo "keeping recordings:$tag"
  elif printf '%s\n' "$used" | grep -qx "recordings:$tag"; then
    echo "keeping recordings:$tag (a container was made from it)"
  else
    docker image rm "recordings:$tag"
  fi
done
