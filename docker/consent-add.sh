#!/bin/sh
# `make consent-add ID='<id>'` runs this on the homelab server, over ssh (stage 2b spec §12.1): it
# adds one Plaud ID to the consent list, as the line "- `<id>` — added <date>". It writes the old
# list and the new line to a new file in the same folder, at the old file's mode, then renames it
# over the list, so the worker, which watches the folder, reads the old list or the new one, never
# half of one. The ID must be 32 hex digits, bare or after `of_`: anything else is refused before
# the list is touched. It reads RECORDINGS_CONSENT_HOST from docker/deploy.env, and no secret.
set -eu
cd "$(dirname "$0")/.."
refuse() { echo "consent-add: $1" >&2; exit 2; }
id=${1:-}
case "$id" in
  of_*) hex=${id#of_} ;;
  *) hex=$id ;;
esac
case "$hex" in
  "" | *[!0-9a-fA-F]*) refuse "not a Plaud ID: 32 hex digits, bare or after of_" ;;
esac
[ "${#hex}" -eq 32 ] || refuse "not a Plaud ID: 32 hex digits, bare or after of_"
[ -f docker/deploy.env ] || refuse "docker/deploy.env is missing"
folder=$(sed -n 's/^RECORDINGS_CONSENT_HOST=//p' docker/deploy.env | tail -n 1)
folder=${folder#\"}
folder=${folder%\"}
[ -n "$folder" ] || refuse "RECORDINGS_CONSENT_HOST is not set in docker/deploy.env"
case "$folder" in
  /*) ;;
  *) folder="docker/$folder" ;;  # relative to docker/, as Compose reads it
esac
[ -d "$folder" ] || refuse "$folder (RECORDINGS_CONSENT_HOST) is not a folder"
list="$folder/consent-list.md"
tmp=$(mktemp "$folder/.consent-list.md.XXXXXX")
trap 'rm -f "$tmp"' EXIT
perms=rw-------  # a new list: 0600
if [ -f "$list" ]; then
  cat "$list" > "$tmp"
  if [ -s "$tmp" ] && [ -n "$(tail -c 1 "$tmp")" ]; then
    printf '\n' >> "$tmp"  # the old list's last line had no newline
  fi
  perms=$(ls -ln "$list" | cut -c2-10)
fi
printf '%s\n' "- \`$id\` — added $(date +%Y-%m-%d)" >> "$tmp"
u=$(printf '%s' "$perms" | cut -c1-3 | tr -d -)
g=$(printf '%s' "$perms" | cut -c4-6 | tr -d -)
o=$(printf '%s' "$perms" | cut -c7-9 | tr -d -)
chmod "u=$u,g=$g,o=$o" "$tmp"  # the old file's mode, set once the new file is written
mv -f "$tmp" "$list"
echo "consent-add: added $id to $list"
