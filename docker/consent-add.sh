#!/bin/sh
# `make consent-add ID='<id>'` runs this on the homelab server, over ssh (stage 2b spec §12.1): it
# appends one Plaud ID to the consent list, as the line "- `<id>` — added <date>". It writes the old
# list and the new line to a new file in the same folder, at the old file's mode, then renames it
# over the list, so the worker, which watches the folder, reads the old list or the new one, never
# half of one. The ID must be 32 hex digits, bare or after `of_`: anything else is refused before
# the list is touched. It never makes a list and never extends an empty one: a missing list, an
# empty one and one of only blank lines and comments (`#` after optional spaces, as the parser
# reads them) are a consent stop that fails closed (§12.1), and one exclusion added to it would
# lift the stop while the real list's other recordings were still unlisted. A list with kept or
# text lines but no exclusion is also a stop, but one Dan wrote: his first exclusion is allowed.
# It reads RECORDINGS_CONSENT_HOST from docker/deploy.env, and no secret.
set -eu
cd "$(dirname "$0")/.."
refuse() { echo "consent-add: $1" >&2; exit 2; }
id=${1:-}
case "$id" in
  of_*) hex=${id#of_} ;;
  *) hex=$id ;;
esac
# Listed digit by digit: a range like 0-9a-f is unspecified outside the POSIX locale (XBD 9.3.5).
case "$hex" in
  "" | *[!0123456789abcdefABCDEF]*) refuse "not a Plaud ID: 32 hex digits, bare or after of_" ;;
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
gone="the consent list is missing, empty or only comments, which stops every import on purpose. Put the real list"
gone="$gone back first (the runbook's section 6 step 1, or Restoring for real), then add the ID"
[ -f "$list" ] || refuse "$gone"
# a list of blank lines and comments only is empty too: drop the comments, then the blanks, and
# grep -q . finds any character left. A list with kept or text lines but no exclusion is allowed.
grep -v '^[[:space:]]*#' "$list" | tr -d ' \t\r\n' | grep -q . || refuse "$gone"
tmp=$(mktemp "$folder/.consent-list.md.XXXXXX")
trap 'rm -f "$tmp"' EXIT
cat "$list" > "$tmp"
if [ -n "$(tail -c 1 "$tmp")" ]; then
  printf '\n' >> "$tmp"  # the old list's last line had no newline
fi
printf '%s\n' "- \`$id\` — added $(date +%Y-%m-%d)" >> "$tmp"
perms=$(ls -ln "$list" | cut -c2-10)
u=$(printf '%s' "$perms" | cut -c1-3 | tr -d -)
g=$(printf '%s' "$perms" | cut -c4-6 | tr -d -)
o=$(printf '%s' "$perms" | cut -c7-9 | tr -d -)
chmod "u=$u,g=$g,o=$o" "$tmp"  # the old file's mode, set once the new file is written
mv -f "$tmp" "$list"
echo "consent-add: added $id to $list"
