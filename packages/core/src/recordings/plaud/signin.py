"""The Plaud sign-in's state (stage 2b spec §10.3, §16): the pause, the `sign-in` blocker, and what
Status and doctor say about the sign-in. Network-free and token-free: it reads the token file's
signature and modes, never its contents, so the worker's tick, doctor and the views can import it
(the token store itself, `tokens.py`, is the worker's alone).

`K_SIGN_IN` (`sign_in` in plaud_state) holds:
- `refused_at`, absent while the sign-in works: when Plaud refused it (a refused refresh, a 401
  that the current-user read confirmed, or no token file at a run's sign-in step). With it, `file`:
  the token file's signature then (`[inode, size, mtime_ns]`, or null for no file). **The pause:**
  while the file is unchanged, scheduled runs make no Plaud call. A new file (`make plaud-sign-in`)
  lifts it, and the next run tries; a **Sync now** or `recordings plaud check` tries once anyway.
- `ok_at`: the last time a signed-in call worked.
- `expires_at`: the access token's expiry (UTC ISO 8601), or null when the sign-in doesn't say.
- `refresh_token`: whether the sign-in holds a refresh token.
- `refresh_expires_at`: the refresh token's own expiry, when known, or null. Needs attention
  warns 7 days ahead of it (§15.2).

Never a token, a hash of one, or the account. The `sign-in` blocker opens with `refused_at` and
clears with it, at the first signed-in call that works after the token file changed.
"""

from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any

from recordings.plaud.checks import K_SIGN_IN, UNCHANGED, PlaudDB
from recordings.plaud.health import clear_blocker, open_blocker
from recordings.plaud.when import parse_stamp, utc_iso

SIGN_IN_BLOCKER = "sign-in"
NO_SIGN_IN = "no-sign-in"  # the pause's `why`: no token file yet
REFUSED = "refused"  # the pause's `why`: Plaud refused this token file
# What to do, wherever the sign-in is needed: the CLI, doctor and the runbook say it alike.
SIGN_IN_FIX = "at the Mac: `make plaud-sign-in` (runbook, Plaud sign-in)"


@dataclass(frozen=True)
class TokenFacts:
    """What may be said about a sign-in: its expiries and whether it can refresh. No token."""

    expires_at: datetime | None
    refresh_token: bool
    refresh_expires_at: datetime | None = None


def token_signature(path: Path) -> list[int] | None:
    """The token file as the pause compares it (§10.3: inode, size and time); None when it can't be
    stat'ed (no file)."""
    try:
        found = path.stat()
    except OSError:
        return None
    return [found.st_ino, found.st_size, found.st_mtime_ns]


def _owner() -> int:
    return os.geteuid()


def unsafe_modes(token_store: Path) -> list[str]:
    """The sign-in's folder wider than 0700, or its token file wider than 0600, or either not this
    user's (§10.3): one line each, naming the path and the fix, never the contents. A missing one
    is no line: that is the sign-in problem, said elsewhere."""
    lines = []
    for path, widest, fix in (
        (token_store.parent, 0o700, "chmod 700"),
        (token_store, 0o600, "chmod 600"),
    ):
        try:
            found = path.stat()
        except OSError:
            continue
        mode = found.st_mode & 0o777
        if mode & ~widest:
            lines.append(f"{path} is open to more than its owner (mode {mode:o}): {fix} it")
        if found.st_uid != _owner():
            lines.append(
                f"{path} belongs to uid {found.st_uid}, not this user ({_owner()}): the "
                "worker must own the Plaud sign-in"
            )
    return lines


def _state(db: PlaudDB) -> dict[str, Any]:
    value = db.get(K_SIGN_IN)
    return value if isinstance(value, dict) else {}


def refused_since(db: PlaudDB) -> datetime | None:
    """When Plaud refused the sign-in, while that stands; None otherwise."""
    return parse_stamp(_state(db).get("refused_at"))


def paused(db: PlaudDB, token_store: Path) -> str | None:
    """Why a scheduled run makes no Plaud call (§9.7 case 2, §10.3): NO_SIGN_IN with no token
    file; REFUSED while Plaud's refusal stands and the file is the one it refused. None: try."""
    signature = token_signature(token_store)
    if signature is None:
        return NO_SIGN_IN
    state = _state(db)
    if parse_stamp(state.get("refused_at")) is not None and state.get("file") == signature:
        return REFUSED
    return None


def record_refused(db: PlaudDB, token_store: Path, *, now: datetime) -> None:
    """Plaud refused the sign-in: the pause, against the token file as it is now, and the
    `sign-in` blocker. A refusal of the same file keeps its first time."""
    signature = token_signature(token_store)

    def change(old: Any) -> Any:
        old = old if isinstance(old, dict) else {}
        same = old.get("file") == signature and parse_stamp(old.get("refused_at")) is not None
        if same:
            return UNCHANGED
        return {**old, "refused_at": utc_iso(now), "file": signature}

    db.update(K_SIGN_IN, change, now=now)
    open_blocker(db, SIGN_IN_BLOCKER, now=now)


def record_good(db: PlaudDB, facts: TokenFacts, *, now: datetime) -> None:
    """A signed-in call worked: the pause and the `sign-in` blocker end, and the sign-in's facts
    are kept for Status (its expiries, and whether it can refresh)."""
    db.put(
        K_SIGN_IN,
        {
            "ok_at": utc_iso(now),
            "expires_at": utc_iso(facts.expires_at) if facts.expires_at else None,
            "refresh_token": facts.refresh_token,
            "refresh_expires_at": (
                utc_iso(facts.refresh_expires_at) if facts.refresh_expires_at else None
            ),
        },
        now=now,
    )
    clear_blocker(db, SIGN_IN_BLOCKER)
