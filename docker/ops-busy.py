"""Does a job hold the ops lock now? Exit 3, printing what its holder announced, or 0 if it's free.

docker/deploy-guard.sh pipes this file into the backup container's python
(`python - /state/locks`), so it works with whatever image is deployed: standard library only.
A shared, non-blocking flock that fails means another process holds the lock. The probe takes
nothing and lets go at once (as Status does). flock is per host, so a hold in any container counts.
"""

import errno
import fcntl
import os
import sys

try:
    fd = os.open(os.path.join(sys.argv[1], "ops.lock"), os.O_RDONLY)
except FileNotFoundError:
    sys.exit(0)  # never taken yet
try:
    fcntl.flock(fd, fcntl.LOCK_SH | fcntl.LOCK_NB)
except OSError as exc:
    if exc.errno not in (errno.EAGAIN, errno.EACCES):
        raise
    print(os.pread(fd, 4096, 0).decode("utf-8", "replace").strip())
    sys.exit(3)
sys.exit(0)
