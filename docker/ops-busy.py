"""Does a job hold a lock now? `ops-busy.py <locks dir> <lock name> [job ...]`: exit 3, printing
what the holder announced, or 0 if the lock is free (2 for a usage error).

docker/deploy-guard.sh pipes this file into the backup container's python
(`python - /state/locks ops`), so it works with whatever image is deployed: standard library only.
A shared, non-blocking flock that fails means another process holds the lock. The probe takes
nothing and lets go at once (as Status does). flock is per host, so a hold in any container counts:
the worker's `plaud` lock is seen from the backup container through the shared state/locks.

With jobs given, only a holder that announced one of them counts (stage 2b spec §17.3: the guard
waits for Sync everything, the catch-up and the baseline, and lets a routine sync be interrupted).
A held lock whose announcement can't be read still counts: it might be any of them.
"""

import errno
import fcntl
import json
import os
import sys


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print("usage: ops-busy.py <locks dir> <lock name> [job ...]", file=sys.stderr)
        return 2
    folder, name, jobs = argv[0], argv[1], argv[2:]
    try:
        fd = os.open(os.path.join(folder, f"{name}.lock"), os.O_RDONLY)
    except FileNotFoundError:
        return 0  # never taken yet
    try:
        try:
            fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
        except OSError as exc:
            if exc.errno not in (errno.EAGAIN, errno.EACCES):
                raise
        else:
            return 0  # free (closing the descriptor lets go of the probe)
        text = os.pread(fd, 4096, 0).decode("utf-8", "replace").strip()
        if jobs:
            try:
                job = json.loads(text)["job"]
            except (ValueError, KeyError, TypeError):
                job = None  # held, but not announced (yet): it might be one of them
            if job is not None and job not in jobs:
                return 0
        print(text or f"{name} is held")
        return 3
    finally:
        os.close(fd)


sys.exit(main(sys.argv[1:]))
