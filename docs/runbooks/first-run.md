# The first real run (stage 2a)

Steps 1–4 of the spec's "order of the first real run" (§19), and how to restore for real. Steps
5–7 belong to stage 2b. Each block says which machine it runs on. Nothing here prints a secret: a
command that needs one reads it from a file, and secret files are written with an editor, never
typed on a command line, where shell history would keep them.

Set these in the shell you use on the homelab server, and paste the block again at the start of
every new session there (a new ssh login, a tmux window, after a reboot): `rc` and the variables
exist only in the shell that defined them. They are examples; use your own values.
The blocks work in bash and in zsh: lists of options are kept in arrays, which both shells split
the same way.

```bash
export RECORDINGS_HOME=/srv/recordings          # on the server's own data disk (spec §3)
export SERVER_TS="$(tailscale ip -4)"           # this server's tailnet address
export NAS_HOST=nas                             # the NAS's DNS or tailnet name: never an ssh alias
export NAS_MIRROR_USER=recordings-mirror        # the NAS account the mirror uses (1b.8)
export NAS_MIRROR=/volume1/recordings-mirror    # the mirror's shared folder, as rsync over ssh sees it
export NAS_MIRROR_SFTP=/recordings-mirror       # the same folder, as SFTP sees it
export REPO_DIR="$RECORDINGS_HOME/repo"         # this repo's checkout on the server (the boot unit expects it here)
rc() { docker compose --env-file "$REPO_DIR/docker/deploy.env" -f "$REPO_DIR/docker/compose.yml" "$@"; }
```

`NAS_HOST` must resolve inside a container, where only DNS works: a name from `~/.ssh/config`
would work for you and fail for the backup service.

Blocks marked "on the NAS" run there, as a DSM administrator over SSH (Control Panel → Terminal &
SNMP → Enable SSH service), in the NAS's own shell. The variables above aren't set there.

## 1. Spikes

### 1a. Server facts

About 15 minutes, on the homelab server. Write each answer in the checklist at the end.

1. **The archive's folder sits on the data disk.**
   ```bash
   sudo mkdir -p "$RECORDINGS_HOME"
   findmnt -no TARGET,SOURCE -T "$RECORDINGS_HOME"
   ```
   Expected: a `TARGET` that is `$RECORDINGS_HOME` or a parent made for the data disk, never
   `/`. If it shows `/`, stop: remove the folder you just made (`sudo rmdir "$RECORDINGS_HOME"`),
   mount the data disk, and start again. The archive must not sit on the system disk.

2. **The filesystem is local.**
   ```bash
   findmnt -no FSTYPE -T "$RECORDINGS_HOME"
   ```
   Expected: `ext4`, `xfs` or `btrfs`. Not `nfs` or `cifs`: `flock`, hard links and SQLite's
   write-ahead log must work locally (spec §3, §6.4, §6.8). `state.db` lives here too, in
   `state/`: `[state] path` must never point at NFS or SMB.

3. **Free space.**
   ```bash
   df -h "$RECORDINGS_HOME"
   ```
   It needs room for the `audio-router` archive three times over, with at least 20% of the disk
   still free afterwards. In step 4 the import copy (`import/`), the archive and the restore
   test's copy (`restore-test/`) all exist at once, and the restore test refuses to take the disk
   below 20% free. A real restore ("Restoring for real") needs the archive's size again.

4. **The folders, their owner, and who can read them.** `archive/` stays empty until
   `recordings init` (step 2), and it must live *inside* the mount, so an unmounted disk means a
   missing folder, which Docker then refuses to start without. `state.db` keeps whole revisions
   of every `recording.json`, titles included, so `state/` is as private as the archive.
   ```bash
   sudo mkdir -p "$RECORDINGS_HOME"/archive "$RECORDINGS_HOME"/state "$RECORDINGS_HOME"/restore-test \
     "$RECORDINGS_HOME"/secrets "$RECORDINGS_HOME"/import "$RECORDINGS_HOME"/bin
   sudo chown -R "$(id -u):$(id -g)" "$RECORDINGS_HOME"
   chmod 700 "$RECORDINGS_HOME" "$RECORDINGS_HOME/state" "$RECORDINGS_HOME/secrets"
   id -u
   id -g
   ```
   The two numbers go in `docker/deploy.env` as `RECORDINGS_UID` and `RECORDINGS_GID`. With
   `$RECORDINGS_HOME` at 700, no other local user reaches anything under it.

5. **Hard links and `flock` work on this disk.**
   ```bash
   cd "$RECORDINGS_HOME/restore-test" && touch probe && ln probe probe.link \
     && flock -n probe true && echo "hard links and flock: ok"; rm -f probe probe.link
   cd -
   ```
   Expected: `hard links and flock: ok`.

6. **Tailscale's address at boot.** The app publishes on the server's tailnet address. At boot,
   `tailscaled` reports ready before that address exists, and Docker never retries a container
   whose port couldn't bind. Step 2.5 installs a unit that waits for the address and the data
   disk; here, record the facts it relies on.
   ```bash
   systemctl is-enabled tailscaled
   systemctl show -p After docker.service | tr ' ' '\n' | grep -c tailscaled
   ip -4 -o addr show tailscale0
   ```
   Expected: `enabled`; `0` or `1` (whether Docker already starts after Tailscale: either is
   fine, the unit covers both); and one `inet` line on `tailscale0`. No `tailscale0` means
   Tailscale runs in userspace mode, where the unit's wait can't work: say so in the checklist
   before going on.

### 1b. NAS transport

On the NAS and the homelab server. The NAS runs DSM 7.3 on ext4, which has no Btrfs snapshots,
so nothing on the NAS could protect an SFTP repository from a compromised server. Backups go to
**rest-server with `--append-only`** instead (spec §15.1): the homelab server can add snapshots
but never delete or change one. Retention runs on the NAS (1b.7). The mirror uses its own
non-admin NAS account over SSH (1b.8). SFTP is only the fallback (1b.9).

1. **What the NAS has, and whether its name resolves.**
   ```bash
   # on the NAS
   uname -m
   sudo docker version --format '{{.Server.Version}}'
   ```
   Expected: `x86_64` or `aarch64`; then a version if Container Manager is installed and
   running, or "command not found" if not (1b.5 then uses the static binary). On the server:
   ```bash
   getent hosts "$NAS_HOST"
   ```
   Expected: one address. Nothing means `NAS_HOST` is a name only ssh knows: pick one that DNS
   or the tailnet resolves.

2. **restic for the spike, on the server.** Its own copy, checked against the release's
   `SHA256SUMS` (the same pin as Task 11's `scripts/fetch_restic.py` and the image).
   ```bash
   cd "$RECORDINGS_HOME/bin"
   case "$(uname -m)" in
     x86_64)  ARCH=linux_amd64 SUM=f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c ;;
     aarch64) ARCH=linux_arm64 SUM=a5f64aaab53d51e311fa3829124c5b703f2d14cf187d8640b6be3b2b49376465 ;;
     *)       ARCH=unpinned SUM=none ;;
   esac
   curl -fsSLO "https://github.com/restic/restic/releases/download/v0.19.1/restic_0.19.1_$ARCH.bz2"
   echo "$SUM  restic_0.19.1_$ARCH.bz2" | sha256sum -c - && bunzip2 -c "restic_0.19.1_$ARCH.bz2" > restic && chmod 755 restic
   rm -f "restic_0.19.1_$ARCH.bz2"
   ./restic version
   cd -
   ```
   Expected: `restic_0.19.1_linux_amd64.bz2: OK` (or `arm64`), then `restic 0.19.1 compiled
   with …`. A sum that doesn't match: stop. Another machine type: take its line from restic's
   v0.19.1 `SHA256SUMS` and check it the same way.

3. **The two passwords.** Generate both in your password manager, which keeps the copies you
   need to read the backups. Paste each into its file with an editor.
   ```bash
   cd "$RECORDINGS_HOME/secrets"
   install -m 600 /dev/null restic_password && ${EDITOR:-vi} restic_password
   install -m 600 /dev/null rest_password && ${EDITOR:-vi} rest_password
   cd -
   ```
   `restic_password` encrypts the repository: without it the backups can't be read.
   `rest_password` is rest-server's login for its user `recordings`.

4. **The NAS's folders, and TLS or the tailnet.** In DSM (Control Panel → Shared Folder), create
   two shared folders on `volume1`:
   - `backups`: read and write for administrators only.
   - `recordings-mirror`: read and write for the mirror account (1b.8), read-only for your own DSM
     account, no access for anyone else. Turn its recycle bin off and leave it out of indexing:
     the mirror refuses a target that holds anything but its own archive.

   Then the folders the rest of 1b uses:
   ```bash
   # on the NAS
   sudo mkdir -p /volume1/backups/rest /volume1/backups/tls /volume1/backups/bin /volume1/backups/retention
   sudo chmod 700 /volume1/backups/tls /volume1/backups/retention
   ```
   **Choose how rest-server's password travels:**
   - **TLS,** which works over the LAN or the tailnet. A self-signed certificate for `NAS_HOST`:
     ```bash
     # on the NAS. NAS_HOST: the same name the server uses.
     NAS_HOST=nas
     sudo openssl req -newkey rsa:2048 -nodes -x509 -days 3650 -subj "/CN=$NAS_HOST" \
       -addext "subjectAltName = DNS:$NAS_HOST" \
       -keyout /volume1/backups/tls/private_key -out /volume1/backups/tls/public_key
     sudo chmod 600 /volume1/backups/tls/private_key
     sudo cat /volume1/backups/tls/public_key
     ```
     Then, on the server, paste the printed certificate into `nas_cacert`. It isn't secret, but it
     lives with the secret files, so the backup service gets it as one:
     ```bash
     install -m 600 /dev/null "$RECORDINGS_HOME/secrets/nas_cacert" && ${EDITOR:-vi} "$RECORDINGS_HOME/secrets/nas_cacert"
     ```
     The repository is then `rest:https://<NAS_HOST>:8000/recordings/`, with
     `[backup] cacert = "/run/secrets/nas_cacert"`.
   - **The tailnet,** only if the NAS runs Tailscale and `NAS_HOST` is its tailnet name. Its
     WireGuard tunnel then carries the password, and rest-server needs no TLS. The repository is
     `rest:http://<NAS_HOST>:8000/recordings/`, with no `cacert`. Plain HTTP to a LAN name would
     send the password in clear, so never use `http://` with one. Compose still needs the file:
     ```bash
     install -m 600 /dev/null "$RECORDINGS_HOME/secrets/nas_cacert"
     ```

5. **rest-server, append-only.** One of the two ways below, whichever 1b.1 allows. Both keep the
   repositories in `/volume1/backups/rest` and run as root, like the retention task (1b.7), so
   each can read what the other writes. Over the tailnet, leave out the three `--tls` options.

   **The login file comes first, for either way:** rest-server refuses to start with
   `--private-repos` and no `.htpasswd`, so a container started without it would restart in a
   loop. DSM has no `htpasswd`, so make the login line on the homelab server, with rest-server's
   own image. It asks for the password (paste `rest_password`) and prints a bcrypt hash, never the
   password:
   ```bash
   docker run --rm -it --entrypoint htpasswd restic/rest-server:0.14.0@sha256:d2aff06f47eb38637dff580c3e6bce4af98f386c396a25d32eb6727ec96214a5 -nB recordings
   ```
   On the NAS, paste the printed `recordings:$2y$…` line into the password file with an editor:
   ```bash
   # on the NAS
   sudo vi /volume1/backups/rest/.htpasswd
   sudo chmod 600 /volume1/backups/rest/.htpasswd
   ```

   **In Container Manager:**
   ```bash
   # on the NAS
   sudo docker run -d --name rest-server --restart unless-stopped -p 8000:8000 \
     -v /volume1/backups/rest:/data -v /volume1/backups/tls:/tls:ro \
     -e OPTIONS="--append-only --private-repos --tls --tls-cert /tls/public_key --tls-key /tls/private_key" \
     restic/rest-server:0.14.0@sha256:d2aff06f47eb38637dff580c3e6bce4af98f386c396a25d32eb6727ec96214a5
   sudo docker logs --tail 5 rest-server
   ```
   Expected: the log shows rest-server listening, not an error about `.htpasswd`.

   **Or as the static binary, started at boot by Task Scheduler:**
   ```bash
   # on the NAS
   cd /tmp
   case "$(uname -m)" in
     x86_64)  ARCH=linux_amd64 SUM=4c9c95bc079a0334e81fad379b19dc5c3353c71c2c88d652cafce2081c2b1c66 ;;
     aarch64) ARCH=linux_arm64 SUM=cef139cbe8b27b16bda731d17f093b0aa466b8c60b136c12d78b6f2bff3daf22 ;;
     *)       ARCH=unpinned SUM=none ;;
   esac
   curl -fsSLO "https://github.com/restic/rest-server/releases/download/v0.14.0/rest-server_0.14.0_$ARCH.tar.gz"
   echo "$SUM  rest-server_0.14.0_$ARCH.tar.gz" | sha256sum -c - && tar xzf "rest-server_0.14.0_$ARCH.tar.gz"
   sudo install -m 755 "rest-server_0.14.0_$ARCH/rest-server" /volume1/backups/bin/rest-server
   rm -rf "rest-server_0.14.0_$ARCH" "rest-server_0.14.0_$ARCH.tar.gz"
   ```
   The sums are from rest-server v0.14.0's `SHA256SUMS`, checked 2026-10-09. For another
   machine type, take its line from there.

   In DSM: Control Panel → Task Scheduler → Create → Triggered Task → User-defined script.
   User `root`, event Boot-up, and this script:
   ```bash
   /volume1/backups/bin/rest-server --path /volume1/backups/rest --listen :8000 --append-only --private-repos --tls --tls-cert /volume1/backups/tls/public_key --tls-key /volume1/backups/tls/private_key >>/volume1/backups/rest-server.log 2>&1
   ```
   Run the task once now (select it, then Run; once only, because a second run finds the port
   busy). 1b.6 checks it from the server; reboot the NAS once afterwards and check again, which
   proves the boot start.

6. **Prove it from the server, against a spike repository.**
   ```bash
   RESTIC="$RECORDINGS_HOME/bin/restic"
   export RESTIC_PASSWORD_FILE="$RECORDINGS_HOME/secrets/restic_password"
   export RESTIC_REST_USERNAME=recordings
   export RESTIC_REST_PASSWORD="$(cat "$RECORDINGS_HOME/secrets/rest_password")"
   CACERT=(--cacert "$RECORDINGS_HOME/secrets/nas_cacert")     # over the tailnet: CACERT=()
   SPIKE="rest:https://$NAS_HOST:8000/recordings/spike/"        # over the tailnet: rest:http://
   PROBE="$RECORDINGS_HOME/restore-test/probe.bin"             # a real file: /etc/os-release is often a symlink
   head -c 5000000 /dev/urandom > "$PROBE"
   "$RESTIC" -r "$SPIKE" "${CACERT[@]}" init
   "$RESTIC" -r "$SPIKE" "${CACERT[@]}" backup "$PROBE"
   "$RESTIC" -r "$SPIKE" "${CACERT[@]}" backup "$PROBE" --force
   "$RESTIC" -r "$SPIKE" "${CACERT[@]}" check
   "$RESTIC" -r "$SPIKE" "${CACERT[@]}" restore latest --target "$RECORDINGS_HOME/restore-test/spike" --verify
   cmp "$PROBE" "$RECORDINGS_HOME/restore-test/spike$PROBE" && echo "restored file matches"
   "$RESTIC" -r "$SPIKE" "${CACERT[@]}" unlock
   "$RESTIC" -r "$SPIKE" "${CACERT[@]}" forget --keep-last 1 --prune
   "$RESTIC" -r "$SPIKE" "${CACERT[@]}" snapshots
   rm -rf "$RECORDINGS_HOME/restore-test/spike"
   rm -f "$PROBE"
   ```
   Expected: `init`, both `backup`s, `check`, `restore` and `unlock` exit 0; `cmp` prints
   `restored file matches`. `unlock` works through append-only. `forget` fails with HTTP 403
   (Forbidden), possibly after retrying for a minute or two: the server can't delete a snapshot,
   which is the point. The proof is the last restic command: `snapshots` still lists **two**
   snapshots. Anything else (`forget` succeeding, one snapshot left): stop, append-only isn't on.
   If any command sits silent for over a minute with no output, the NAS or port 8000 is
   unreachable: Ctrl-C and check the NAS's firewall and rest-server's log
   (`/volume1/backups/rest-server.log`, for the static binary; `sudo docker logs rest-server`,
   for the container).

   The spike can't be deleted through rest-server either, so delete it on the NAS:
   ```bash
   # on the NAS
   sudo rm -rf /volume1/backups/rest/recordings/spike
   ```

7. **Retention on the NAS.** The server can't forget old snapshots through append-only, so a
   weekly task on the NAS does, with restic run directly on the repository's folder and its own
   copy of the password, readable only by root. First, the same pinned restic on the NAS:
   ```bash
   # on the NAS
   cd /tmp
   case "$(uname -m)" in
     x86_64)  ARCH=linux_amd64 SUM=f415415624dcc452f2a02b8c33641791a8c6d6d3b65bbb3543fcf9a25151585c ;;
     aarch64) ARCH=linux_arm64 SUM=a5f64aaab53d51e311fa3829124c5b703f2d14cf187d8640b6be3b2b49376465 ;;
     *)       ARCH=unpinned SUM=none ;;
   esac
   curl -fsSLO "https://github.com/restic/restic/releases/download/v0.19.1/restic_0.19.1_$ARCH.bz2"
   echo "$SUM  restic_0.19.1_$ARCH.bz2" | sha256sum -c - && bunzip2 -c "restic_0.19.1_$ARCH.bz2" > restic
   sudo install -m 755 restic /volume1/backups/bin/restic && rm -f restic "restic_0.19.1_$ARCH.bz2"
   sudo install -m 600 /dev/null /volume1/backups/retention/restic_password
   sudo vi /volume1/backups/retention/restic_password
   ```
   Paste the same `restic_password` from your password manager. If the NAS lacks `bunzip2`, run
   the download and the check on the server instead, and copy the checked `restic` over.

   Then, in DSM: Control Panel → Task Scheduler → Create → Scheduled Task → User-defined script.
   User `root`, weekly (Sunday 04:30, say), with run details sent by email when the script ends
   abnormally, and "Save output results" ticked in Task Settings (a folder on `backups`), because
   without it the history shows only a pass or fail and 2.6 can't show that the NAS's restic opened
   the repository. This is the script. The cache and temporary files stay on the volume, because
   DSM's system partition is small.
   ```bash
   export RESTIC_PASSWORD_FILE=/volume1/backups/retention/restic_password
   export RESTIC_CACHE_DIR=/volume1/backups/retention/cache TMPDIR=/volume1/backups/retention/tmp
   mkdir -p "$RESTIC_CACHE_DIR" "$TMPDIR"
   R=/volume1/backups/rest/recordings
   /volume1/backups/bin/restic -r "$R" unlock
   /volume1/backups/bin/restic -r "$R" forget --retry-lock 30m --keep-hourly 24 --keep-daily 14 --keep-weekly 8 --keep-monthly 12 --keep-tag pre-import --keep-tag post-import --prune
   ```
   The keep rules are spec §15.1's. The `pre-import` and `post-import` snapshots are kept for
   good (§19). Step 2.6 runs the task once, when the repository has its first snapshot.

8. **The mirror's account** (spec §19: whether the mirror's account can run rsync into its
   share).
   - In DSM, create the user `recordings-mirror`. It is not an administrator. It can read and
     write `recordings-mirror` and nothing else, and its application permissions allow only
     rsync and SFTP.
   - Turn on Control Panel → File Services → rsync → Enable rsync service, and File Services →
     FTP → SFTP. Turn on the user home service (User & Group → Advanced), because the account's
     key lives in its home.

   The key and the NAS's host key, on the server:
   ```bash
   K="$RECORDINGS_HOME/secrets"
   ssh-keygen -t ed25519 -N "" -C recordings-mirror -f "$K/nas_ssh_key"
   ssh-keyscan -t ed25519 "$NAS_HOST" > "$K/nas_known_hosts"
   ssh-keygen -lf "$K/nas_known_hosts"
   cat "$K/nas_ssh_key.pub"
   ```
   Compare that fingerprint with the NAS's own before trusting it
   (`ssh-keygen -lf /etc/ssh/ssh_host_ed25519_key.pub`, on the NAS).

   On the NAS, the account's `authorized_keys` gets one line: `restrict,from="<the server's
   address, as the NAS sees it>" `, then the whole `.pub` line. The address is the server's
   tailnet address if it reaches the NAS over the tailnet, or its LAN address. `restrict` turns
   off forwarding and terminals. There is no forced command, so rsync and sftp's `df` both work.
   ```bash
   # on the NAS
   sudo mkdir -p /volume1/homes/recordings-mirror/.ssh
   sudo vi /volume1/homes/recordings-mirror/.ssh/authorized_keys
   sudo chown -R recordings-mirror:users /volume1/homes/recordings-mirror/.ssh
   sudo chmod 711 /volume1/homes/recordings-mirror
   sudo chmod 700 /volume1/homes/recordings-mirror/.ssh
   sudo chmod 600 /volume1/homes/recordings-mirror/.ssh/authorized_keys
   ```

   Then prove it from the server: a dry run, a real copy, and sftp's `df`, which also removes the
   copy again.
   ```bash
   K="$RECORDINGS_HOME/secrets"
   SSH_OPTS=(-i "$K/nas_ssh_key" -o "UserKnownHostsFile=$K/nas_known_hosts" -o StrictHostKeyChecking=yes -o IdentitiesOnly=yes)
   PROBE="$RECORDINGS_HOME/restore-test/probe.bin"             # a real file, not a symlink
   [ -f "$PROBE" ] || head -c 5000000 /dev/urandom > "$PROBE"
   rsync -an -e "ssh ${SSH_OPTS[*]}" "$PROBE" "$NAS_MIRROR_USER@$NAS_HOST:$NAS_MIRROR/"
   rsync -a -e "ssh ${SSH_OPTS[*]}" "$PROBE" "$NAS_MIRROR_USER@$NAS_HOST:$NAS_MIRROR/"
   printf -- '-df %s\nrm %s/probe.bin\nls %s\n' "$NAS_MIRROR_SFTP" "$NAS_MIRROR_SFTP" "$NAS_MIRROR_SFTP" | sftp -b - "${SSH_OPTS[@]}" "$NAS_MIRROR_USER@$NAS_HOST"
   rm -f "$PROBE"
   ```
   Expected:
   - If ssh or rsync is refused outright ("Permission denied", "This account is currently not
     available", or a connection that closes at once while sftp below works): DSM 7 lets only
     administrators open an ssh session, and a non-admin account with no shell may get SFTP and the
     rsync daemon (port 873) only. **Stop.** The mirror as specced (rsync over ssh) can't run with
     this account. Write it in the checklist and decide with the spec's author (an rsync daemon
     module, or SFTP, changes `[mirror]`); don't make the mirror account an administrator.
   - Both `rsync` runs exit 0. If rsync says the folder doesn't exist, this DSM shows rsync the
     share's SFTP-style path: set `NAS_MIRROR` to `$NAS_MIRROR_SFTP` and try again.
   - If the real copy fails with "failed to set permissions" (exit 23), the share's ACLs refuse
     rsync's permission changes. Run it again with `--no-perms` added, and write "`--no-perms`:
     yes" in the checklist, and set `[mirror] no_perms = true` in step 2.1.
   - sftp prints a `df` table with an `Avail` column, removes the file, and lists nothing. The
     share is empty again, as the mirror requires. Both shares sit on `volume1`, so this `df`
     measures the backups' space too. If sftp says the server doesn't support
     `statvfs@openssh.com`, the free-space check will say "unknown": write that in the checklist.

9. **Only if rest-server can't run at all: restic over SFTP.** This accepts the risk that
   1b avoids: the server's key can delete the repository, and ext4 has no snapshots to fall back
   on. Hyper Backup of the `backups` share to an off-site target is then the mitigation.
   - Create a second non-admin account, `recordings-backup`, with SFTP only, read and write on
     `backups` only. Give it the same public key, restricted the same way as in 1b.8.
   - DSM's SFTP root lists the shared folders, so the repository path starts at the share:
     `/backups/recordings`, not `/volume1/backups/recordings`.
   ```bash
   # SSH_OPTS is from 1b.8; in a new shell, define it again first.
   RESTIC="$RECORDINGS_HOME/bin/restic"
   export RESTIC_PASSWORD_FILE="$RECORDINGS_HOME/secrets/restic_password"
   PROBE="$RECORDINGS_HOME/restore-test/probe.bin"
   [ -f "$PROBE" ] || head -c 5000000 /dev/urandom > "$PROBE"
   SPIKE="sftp:recordings-backup@$NAS_HOST:/backups/recordings-spike"
   "$RESTIC" -r "$SPIKE" -o sftp.args="${SSH_OPTS[*]}" init
   "$RESTIC" -r "$SPIKE" -o sftp.args="${SSH_OPTS[*]}" backup "$PROBE"
   "$RESTIC" -r "$SPIKE" -o sftp.args="${SSH_OPTS[*]}" check
   "$RESTIC" -r "$SPIKE" -o sftp.args="${SSH_OPTS[*]}" forget --keep-last 1 --prune
   rm -f "$PROBE"
   ```
   Expected: each exits 0. Then delete `/volume1/backups/recordings-spike` on the NAS. The
   repository is `sftp:recordings-backup@<NAS_HOST>:/backups/recordings`, with
   `[backup] prune = true`, and no retention task on the NAS (skip 1b.7's task).

10. **Record the decision** for step 2's `config.toml`:
    - `[backup] repository`: `rest:https://<NAS_HOST>:8000/recordings/` (or `http://` over the
      tailnet), with `rest_username = "recordings"`, `cacert = "/run/secrets/nas_cacert"` with
      TLS, and `prune = false`.
    - `[nas] ssh = "recordings-mirror@<NAS_HOST>"`, for sftp's `df`, and
      `[backup] nas_repo_path = "/recordings-mirror"`: any folder on the backups' volume, as SFTP
      sees it.
    - `[mirror] target = "recordings-mirror@<NAS_HOST>:/volume1/recordings-mirror/"`, or the form
      1b.8 found.
    - `[mirror] no_perms = true`, only if 1b.8 needed `--no-perms`.

### 1c. Before stage 2b (not needed for 2a)

The token spike and the ID-form spike (§9.1, §19) both call Plaud. Stage 2a makes no Plaud
calls, so they run before stage 2b's deploy, not now.

## 2. Deploy 2a against an empty, initialised archive

On the homelab server, in the repo checkout. It must be at `$REPO_DIR` (`$RECORDINGS_HOME/repo`),
where the boot unit in 2.5 expects it: clone it there if it isn't.

1. **Settings.** Copy both templates, then edit them:
   - `config.toml`: `[archive] writer_id`; `[server] base_url` (`http://<$SERVER_TS>:8000`) and
     `allowed_hosts` (every name you reach the app by: the tailnet address and the MagicDNS name,
     bare, with no port or scheme. Any other Host gets a 400, which `/healthz` in 2.5 and the
     Library in 4 would hit); `[state] path`; `[backup]`, `[nas]` and `[mirror]` as
     1b.10 recorded; `[alerts] ntfy_server = "https://ntfy.sh"`; and
     `[plaud] consent_list = "/consent/consent-list.md"` (step 3 brings the list over).
   - `docker/deploy.env`: the UID and GID from 1a.4, this server's tailnet address (`$SERVER_TS`)
     as `RECORDINGS_BIND`, and the host folders under `$RECORDINGS_HOME`.
   - **`state.db` must be on a local disk.** In the containers `[state] path` is `/state`, bound
     to `RECORDINGS_STATE_HOST`. Keep that on the disk 1a.2 checked, never on NFS or SMB.
   ```bash
   cp config.example.toml config.toml
   cp docker/deploy.example.conf docker/deploy.env
   ```

2. **Alerts, and the last secret files.**
   - **ntfy.sh:** generate a topic in your password manager: 20 or more random letters and
     digits (only `A–Z`, `a–z`, `0–9`, `-` and `_`, at most 64). Subscribe to it in the ntfy app
     on your phone. The topic is the only lock on your alerts.
   - **Healthchecks.io:** create a check with a period of 1 hour and a grace of 3 hours. The
     grace is long because the loop runs the mirror, the check and the restore test between
     backups, and a long one must not look like an outage. Add an integration that reaches your
     phone: email, or ntfy with the same topic. Copy the check's ping URL
     (`https://hc-ping.com/…`) without `/start` or `/fail`; the app adds those.
   - **Keep both in your password manager,** the topic and the ping URL. Whoever holds them can
     read your alerts or keep an outage quiet.
   ```bash
   cd "$RECORDINGS_HOME/secrets"
   install -m 600 /dev/null ntfy_topic && ${EDITOR:-vi} ntfy_topic
   install -m 600 /dev/null deadman_url && ${EDITOR:-vi} deadman_url
   ls -l
   cd -
   ```
   Expected: `deadman_url`, `nas_cacert` (empty over the tailnet), `nas_known_hosts`,
   `nas_ssh_key`, `nas_ssh_key.pub`, `ntfy_topic`, `rest_password` and `restic_password`. Each is
   `-rw-------`, except the `.pub`.

3. **The tailnet ACL, before anything listens** (spec §15: the app's port is for your devices
   only). First save the policy in Tailscale's admin console, under Access controls: Tailscale
   rejects a tag that `tagOwners` doesn't list. Grants for `tag:recordings` match nothing until
   the server carries the tag, so saving them first is safe. A sketch to merge into your own
   policy:
   ```jsonc
   {
     "tagOwners": { "tag:recordings": ["autogroup:admin"] },
     "grants": [
       // Only your own devices reach the app's port (RECORDINGS_PORT), and ssh (step 3's rsync).
       { "src": ["you@example.com"], "dst": ["tag:recordings"], "ip": ["tcp:8000", "tcp:22"] }
       // If the NAS is on the tailnet, the tagged server also needs to reach it (rest-server and
       // ssh): add a grant from "tag:recordings" to the NAS for ["tcp:8000", "tcp:22"].
     ]
   }
   ```
   A tagged node loses its owner's access, so the grants must name what you still need: the
   `tcp:22` grant keeps your own ssh (and step 3's rsync) working, and if the NAS is on the
   tailnet, the server's traffic to it needs its own grant. No broader rule, such as the default
   allow-all, may still cover the server.

   Then tag the server (`sudo tailscale set --advertise-tags=tag:recordings`, and approve it in
   the admin console if asked). Last, check from a
   device that should be refused (a tagged device, a shared node, or the NAS if it is on the
   tailnet), with `SERVER_TS` set there to the server's tailnet address:
   ```bash
   nc -vz -w 5 "$SERVER_TS" 8000
   ```
   Expected: a timeout. "Connection refused" means its packets reached the server: fix the ACL
   before going on. From one of your own devices, the same line says "refused" for now, because
   nothing listens yet.

4. **Build, initialise once, start.** `rc` is the function from the top of this runbook, the
   project's compose command; the image is tagged by the commit (as `make deploy` tags it). Every
   `rc` line after this needs `RECORDINGS_IMAGE_TAG` exported in the same shell, and it must name
   an image you built: after a `git pull`, run `rc build` again first.
   ```bash
   cd "$REPO_DIR"
   git status --porcelain
   export RECORDINGS_IMAGE_TAG="$(git rev-parse --short=12 HEAD)"
   rc build
   rc run --rm web recordings init
   rc run --rm backup recordings backup init
   rc up -d
   rc exec web recordings doctor --role web
   rc exec backup recordings doctor --role backup
   ```
   Expected:
   - `git status --porcelain` prints nothing, so the tag names the code exactly.
   - `init` prints the archive's UUID, and `backup init` reports the repository initialised.
   - Neither `doctor` reports a problem. `--role web` confirms that the web service holds no
     backup or mirror secrets.
   - Both warn that retention is off here (`[backup] prune = false`). That is right for
     rest-server: retention runs on the NAS (1b.7).
   - From your own device, `nc -vz -w 5 "$SERVER_TS" 8000` now connects. From the refused
     device it still times out.
   - If `backup init` or any later `backup` command sits silent for over a minute, the NAS or its
     port 8000 is unreachable (restic retries for up to about 15 minutes before giving up). Ctrl-C,
     and run 1b.6's `snapshots` line by hand to see whether the NAS answers.

   `rc up -d` also starts the backup service's loop, which runs every due job at once: the first
   backup, mirror, check and restore test run now, on the empty archive.

5. **Start at boot.** `docker/recordings.service` waits, for up to 2 minutes, for an IPv4 address
   on `tailscale0` and for the data disk, then runs `docker compose … up -d`. It is its own unit,
   not a change to `docker.service`, so other containers on this server never wait for it. It
   reads the deployed image tag from the existing web container (`docker/up-deployed.sh`), so the
   `rc build` / `rc up -d` flow above needs no tag file. Install it:
   ```bash
   sudo install -m 644 docker/recordings.service /etc/systemd/system/recordings.service
   ```
   If the paths at its top don't match your checkout and `$RECORDINGS_HOME`, change the installed
   copy with `sudo systemctl edit --full recordings.service`, never the checkout's file, which
   would make the next deploy `-dirty`. Before the reboot, wait until the Status page shows the
   first backup and mirror finished (2.4): a reboot in the middle leaves a stale restic lock and a
   half-copied mirror. Then:
   ```bash
   sudo systemctl daemon-reload
   sudo systemctl enable recordings.service
   sudo systemctl reboot
   ```
   After the reboot, in a new shell: paste the top block, `cd "$REPO_DIR"`, and export
   `RECORDINGS_IMAGE_TAG` as in step 4:
   ```bash
   systemctl is-active recordings.service
   rc ps
   curl -fsS "http://$SERVER_TS:8000/healthz"
   ```
   Expected: `active`, both `web` and `backup` running, and an answer from `/healthz`.

6. **Prove backup, restore and the mirror.** Stop the loop first, so the manual runs don't
   compete with it for restic's lock, then start it again.
   ```bash
   rc logs --tail 20 backup      # the loop's first jobs should be finished
   rc stop backup
   rc run --rm backup recordings backup run
   rc run --rm backup recordings backup restore-test
   rc run --rm backup recordings mirror
   rc run --rm backup recordings alert-test
   rc start backup
   ```
   Expected:
   - `backup run`, `restore-test` and `mirror` each print `ok: True`.
   - The phone gets the test push. Healthchecks shows the check up, with a log entry from
     `alert-test`.
   - The Status page shows the backup, the restore test and the mirror.

   `rc stop` gives the loop 10 seconds, then kills it. A job it was running leaves a stale restic
   lock; a lock warning on the next run is expected and clears itself.

   Then, on the NAS, run the retention task once (Task Scheduler: select it, then Run). Its saved
   output shows `forget` listing the snapshots it keeps, and no error.

7. **Prove that a killed backup raises an alert** (§20: "a killed backup raises an alert").
   ```bash
   rc kill backup
   rc ps backup
   ```
   `kill`, not `stop`, because §20 asks for a killed backup. `rc ps` must not show `backup` as
   `Up`. If Docker restarted it, the loop keeps pinging and no alert can arrive: stop here,
   `rc stop backup`, and note it in the checklist. Otherwise wait the period plus the grace
   (1 + 3 hours) from the last ping. The checker's alert arrives on the phone. Then start it
   again:
   ```bash
   rc start backup
   ```

## 3. Bring the source over

**On the Mac**, from `audio-router`'s own copy of its archive: never the NAS's Drive copy, which
may be mid-sync. `AR_MEDIA` is the folder that holds `plaud/`, `recorder/`, `pocket/` and
`catalog/`. `CONSENT_LIST` is `audio-router`'s consent list ("never mirror"). `HOMELAB` is your
ssh name for the homelab server.
```bash
export AR_MEDIA="$HOME/path/to/audio-router/media"
export CONSENT_LIST="$HOME/path/to/consent-list.md"
export HOMELAB=homelab
export SRV_HOME=/srv/recordings          # the server's RECORDINGS_HOME
cd "$AR_MEDIA" && find . -type f ! -path '*/.*' -print0 | sort -z | xargs -0 shasum -a 256 > "$TMPDIR/audio-router.sha256"
caffeinate -i rsync -a --exclude='.*' "$AR_MEDIA"/ "$HOMELAB":"$SRV_HOME"/import/
rsync -a "$TMPDIR/audio-router.sha256" "$HOMELAB":"$SRV_HOME"/import.sha256
rsync -a "$CONSENT_LIST" "$HOMELAB":"$SRV_HOME"/consent-list.md
```
If the first rsync is interrupted, run the same line again: it carries on. Hidden files
(`.DS_Store`) are left out of the checksums and the copy alike, so the checksums cover everything
that is copied. `caffeinate -i` keeps the Mac awake for the copy.

**On the homelab server:** check the copy, make it read-only, and run the dry run. Work inside
`tmux` (or `screen`), so a dropped ssh session doesn't kill an hours-long backup or import. Paste
the top block, `cd "$REPO_DIR"`, and export `RECORDINGS_IMAGE_TAG` as in 2.4.
```bash
cd "$RECORDINGS_HOME/import" && sha256sum --check --quiet ../import.sha256 && echo "copy verified"
cd -
chmod -R a-w "$RECORDINGS_HOME/import"
chmod 400 "$RECORDINGS_HOME/consent-list.md"
IMPORT_MOUNTS=(-v "$RECORDINGS_HOME/import:/import:ro" -v "$RECORDINGS_HOME/consent-list.md:/consent/consent-list.md:ro")
rc run --rm "${IMPORT_MOUNTS[@]}" web recordings import-audio-router /import --dry-run
```
Expected: `copy verified`, then a report in which every catalog row has one disposition and
`unplaced` is 0.
- IDs on the consent list appear only as a `consent-excluded` count. If the list can't be read,
  the dry run refuses: it fails closed.
- A Plaud row with audio but no snapshot and no time is `deferred-to-2b`, with its Plaud ID, for
  2b's sync.
- Every `audio-router` tag comes over. Privacy labels (`private`, `therapy` and the others in
  Task 8's `PRIVATE_TAGS`) become private tags, one per meaning. Those rows, rows with an `access` tier, and
  the private tier are `new-private`: check the report's private count against
  `audio-router`'s private rows.
- If anything is unplaced, stop: the report gives each row's `uri` and reason.

## 4. Import

In the server's shell from step 3 (the top block, `RECORDINGS_IMAGE_TAG` and `IMPORT_MOUNTS`),
still inside `tmux` or `screen`. Stop the loop first, so no scheduled
backup lands in the middle of the import. Pause the Healthchecks check first (its Pause button):
no backup pings while the loop is stopped, and the import can outlast the grace.
```bash
rc stop backup
rc run --rm backup recordings backup run --tag pre-import
rc run --rm "${IMPORT_MOUNTS[@]}" web recordings import-audio-router /import
rc run --rm web recordings validate /archive --deep
rc run --rm backup recordings backup run --tag post-import
rc run --rm backup recordings backup restore-test
rc start backup
```
Expected: the import reports no failures, `validate` reports no problems, and both backups and the
restore test print `ok: True`. The restore test now proves the imported data, not just an empty
archive. Resume the Healthchecks check. The first full mirror of the imported archive runs next
and can take hours; if Healthchecks alerts during it, the next backup's ping clears it.

Once the restore test has passed, and only then, delete the import copy. The archive and its
backups hold everything, and `import/` is a plaintext copy nobody needs. Keep `consent-list.md`:
2b's sync reads it.
```bash
chmod -R u+w "$RECORDINGS_HOME/import"
rm -rf "$RECORDINGS_HOME/import" "$RECORDINGS_HOME/import.sha256"
```

Then open the Library on one of your devices. It lists the imported recordings, with
`audio-router`'s tags, and its first load takes under 2 seconds (your browser's network panel shows the
time). If it takes longer, stop: the Library moves onto `index.db` before stage 2b (stage-2a
plan, decision 6).

**Rolling back, until stage 3's first tag** (§19): the deployment is disposable.
```bash
rc down
mv "$RECORDINGS_HOME/archive" "$RECORDINGS_HOME/archive.failed-$(date +%Y%m%d)"
mv "$RECORDINGS_HOME/state" "$RECORDINGS_HOME/state.failed-$(date +%Y%m%d)"
mkdir "$RECORDINGS_HOME/archive" && mkdir -m 700 "$RECORDINGS_HOME/state"
```
Then:
- Run step 2.4 from `rc run --rm web recordings init` on, but skip `backup init`: the repository
  is still there, with the old snapshots.
- Run step 3 again if `import/` is already gone, then step 4.
- `recordings init` gives the new archive a new UUID, so the mirror refuses its old copy. Once
  you're sure, empty the `recordings-mirror` share on the NAS, and the next run fills it. Your
  DSM account is read-only on that share (1b.4), so File Station can't do it. Do it in the NAS's
  shell as an administrator:
  ```bash
  # on the NAS
  sudo find /volume1/recordings-mirror -mindepth 1 -maxdepth 1 -exec rm -rf {} +
  ```
- When the new import has passed step 4, delete the `.failed-` folders: they are plaintext
  copies too.

## Restoring for real

When the archive, `state.db`, or the whole server is lost. Each snapshot holds the container
paths `/archive`, `/config/config.toml`, `/backup-extra/deploy.env` and `/state/backup/state.db`
(a consistent SQLite copy). `index.db` is derived, so it isn't there; `reindex` rebuilds it.

On the homelab server, with the top block of this runbook pasted (it defines the variables and
`rc`), inside `tmux` or `screen` because the restore takes hours. A new server first goes
through 1a, then:
- clone the repo to `$RECORDINGS_HOME/repo`
- get restic as in 1b.2
- get its secret files from your password manager as in 1b.3, 1b.4 and 2.2
- make a new mirror key and replace its `authorized_keys` line on the NAS (1b.8), because the
  `from=` address may change
- set the tailnet ACL for it (2.3)

1. **Find the snapshot.**
   ```bash
   cd "$REPO_DIR"
   export RECORDINGS_IMAGE_TAG="$(git rev-parse --short=12 HEAD)"
   rc down        # skip on a new server: docker/deploy.env doesn't exist until step 3 restores it
   RESTIC="$RECORDINGS_HOME/bin/restic"
   export RESTIC_PASSWORD_FILE="$RECORDINGS_HOME/secrets/restic_password"
   export RESTIC_REST_USERNAME=recordings
   export RESTIC_REST_PASSWORD="$(cat "$RECORDINGS_HOME/secrets/rest_password")"
   CACERT=(--cacert "$RECORDINGS_HOME/secrets/nas_cacert")     # over the tailnet: CACERT=()
   REPO="rest:https://$NAS_HOST:8000/recordings/"               # [backup] repository
   WRITER_ID=homelab                                            # [archive] writer_id
   "$RESTIC" -r "$REPO" "${CACERT[@]}" snapshots --host "$WRITER_ID" --tag recordings
   ```
   `rc down` only matters if anything still runs. `snapshots` lists the newest last. **Pick the
   snapshot by ID, not `latest`:** the repository is append-only, so a snapshot of an empty or
   broken archive may sit at the end (an unmounted disk, a fresh `init`). Check the candidate:
   ```bash
   SNAP=1a2b3c4d        # the ID of the snapshot you want
   "$RESTIC" -r "$REPO" "${CACERT[@]}" stats "$SNAP" --mode restore-size
   ```
   The size should be about your archive's. Steps 2 and 5 restore that ID.

2. **Restore it into a scratch folder.** It needs as much free space as the archive.
   ```bash
   "$RESTIC" -r "$REPO" "${CACERT[@]}" restore "$SNAP" --target "$RECORDINGS_HOME/restore-real" --verify
   ```

3. **Move each lost piece into place.** Move what is there aside first, and delete it only once
   the restored copy works. Moving the whole old `state/` aside also takes its stale
   write-ahead log with it, which must never meet the restored `state.db`.
   ```bash
   R="$RECORDINGS_HOME/restore-real"
   STAMP="$(date +%Y%m%d)"
   mv "$RECORDINGS_HOME/archive" "$RECORDINGS_HOME/archive.lost-$STAMP"
   mv "$R/archive" "$RECORDINGS_HOME/archive"
   mv "$RECORDINGS_HOME/state" "$RECORDINGS_HOME/state.lost-$STAMP"
   mkdir -m 700 "$RECORDINGS_HOME/state"
   mv "$R/state/backup/state.db" "$RECORDINGS_HOME/state/state.db"
   cp "$R/config/config.toml" config.toml
   cp "$R/backup-extra/deploy.env" docker/deploy.env
   ```
   Run only the lines for what you lost. If your own `config.toml` or `deploy.env` survived,
   compare it with the restored one (`diff`) rather than overwrite it.

4. **Rebuild what's derived, check, start.** `reindex` adopts every `recording.json` as it now
   stands, so files written after the snapshot's `state.db` copy aren't refused as edited
   outside the app, and it rebuilds `index.db`.
   ```bash
   rc build   # a new server: once deploy.env is back, because its UID and GID are build args
   rc run --rm web recordings reindex
   rc run --rm web recordings validate /archive --deep
   rc up -d
   rc exec web recordings doctor --role web
   rc exec backup recordings doctor --role backup
   ```

5. **One recording, not the whole archive.** Restore just its folder with `--include`, move the
   damaged copy aside (if there is one), move the restored one in, and run `reindex`. In a new
   shell, paste the top block, `cd "$REPO_DIR"`, export `RECORDINGS_IMAGE_TAG`, and set `RESTIC`,
   `REPO`, `CACERT` and `SNAP` as in step 1.
   ```bash
   ID=20261008T143512+0000_0123abcd                             # the recording's ID
   D="recordings/${ID:0:4}/${ID:4:2}/$ID"
   "$RESTIC" -r "$REPO" "${CACERT[@]}" restore "$SNAP" \
     --include "/archive/$D" --target "$RECORDINGS_HOME/restore-real" --verify
   mv "$RECORDINGS_HOME/archive/$D" "$RECORDINGS_HOME/$ID.lost-$(date +%Y%m%d)"
   mv "$RECORDINGS_HOME/restore-real/archive/$D" "$RECORDINGS_HOME/archive/$D"
   rc run --rm web recordings reindex
   ```

6. **`state.db` lost, and its backup copy unusable.** Move the whole damaged `state/` aside,
   never `state.db` alone (its `-wal` file holds the latest commits). In 2a the folder holds only
   `state.db*`, `locks/` and `backup/`. Then recreate `state.db` from the archive's sentinel. Each
   file's merge base is adopted on its next write.
   ```bash
   rc down
   mv "$RECORDINGS_HOME/state" "$RECORDINGS_HOME/state.lost-$(date +%Y%m%d)"
   mkdir -m 700 "$RECORDINGS_HOME/state"
   rc run --rm web recordings init --adopt
   rc run --rm web recordings reindex
   rc up -d
   ```

7. **Clean up** once `validate` is clean and the next backup has passed: delete
   `$RECORDINGS_HOME/restore-real` and the `.lost-` folders. A new server also installs the boot
   unit (2.5).

## Checklist

| Fact | Answer |
|---|---|
| The archive's mount (1a.1) | |
| Filesystem, for the archive and `state/` (1a.2) | local: ext4 / xfs / btrfs |
| Free space (1a.3) | |
| UID and GID (1a.4) | |
| `$RECORDINGS_HOME`, `state/` and `secrets/` are 700 (1a.4) | yes / no |
| Hard links and `flock` (1a.5) | |
| Docker starts after Tailscale (1a.6); `tailscale0` exists | 0 / 1; yes / no |
| The NAS's machine type; Container Manager (1b.1) | x86_64 / aarch64; yes / no |
| `NAS_HOST` resolves by DNS (1b.1) | yes / no |
| rest-server runs in (1b.5) | Container Manager / Task Scheduler |
| rest-server's password travels by (1b.4) | TLS with `[backup] cacert` / the tailnet |
| Append-only proven: `forget` refused with 403 (1b.6) | yes / no |
| rest-server is back after a NAS reboot (1b.5) | yes / no |
| The NAS's weekly retention task, as root (1b.7) | set up; first run ended normally (2.6) |
| The mirror account can rsync into its share (1b.8) | yes / no |
| The mirror account can run rsync over ssh (1b.8) | yes / no: stop and decide |
| The mirror needs `--no-perms` (1b.8) | yes / no |
| The NAS's free space over sftp's `df` (1b.8) | works / unknown |
| NAS transport (1b) | rest-server / SFTP fallback, risk accepted |
| The tailnet ACL refuses another device (2.3) | yes / no |
| Dead-man's switch: Healthchecks.io, period 1 h, grace 3 h (2.2) | |
| ntfy topic and ping URL in the password manager (2.2) | yes / no |
| `recordings.service` brings the app up after a reboot (2.5) | yes / no |
| The killed backup raised the alert (2.7) | yes / no |
| The restore test passed on the imported data; `import/` deleted (4) | yes / no |
| The Library's first load (4) | under 2 s / slower |
