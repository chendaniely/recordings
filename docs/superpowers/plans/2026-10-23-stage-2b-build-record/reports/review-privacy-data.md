# Privacy and fail-open review: stage-2b at 694b6a9 (against 8deb039)

Scope: whether a consent-listed, kept-out or review-held recording can leak (audio, transcript,
note, title) into the archive, `plaud-cache.db`, a snapshot, a log, a reply, an alert, a backup or
a screen; whether `private` always lands first (apart from the documented rename exception) and
is never removed without Dan; and fail-open defaults. Decision 16's known gap (a new ID listed
beside a still-listed excluded one) is left out.

Count: Critical 0 · High 1 · Medium 1 · Low 5.

Checked and found sound (no finding): the consent grammar and its stops (`keepout.py`); the
importer's three consent and keep-out checks (before any byte, on the envelope, at a hash merge);
private landing first in `_create`, `_merge` and `reconcile_plaud`; nothing that removes
`private`; `title_allowed`'s fail-closed inputs (None consent, a stop, unreadable words); the
cache purge at each tick; the hold's Accept refusal; gate reads; replies, logs, events,
manifests and K_COMPARE carrying no titles; `never_backed_up` refusing the cache.

---

## 1. High: Release's preview stores and shows the title of a possible re-keyed consent-listed recording, and ignores an IDs-changed hold

**Where:** `packages/core/src/recordings/worker.py:1447-1484` (`_release_preview`, the
`put_preview` at 1472-1476); `packages/core/src/recordings/plaud/release.py:63-71` (`kept_by`
returns `"rekeyed"`) and `:80-101` (`may_preview` accepts it, and never reads `K_HOLD`). UI side:
`packages/ui/src/recordings_ui/plaud.py:258` (`RELEASE_BY` includes `"rekeyed"`) and
`packages/ui/frontend/src/components/SyncPage.tsx:286-288` (the Release button on a re-keyed row
sends `release-preview`; `gated()` at :92 checks only the gate).

**What the spec says:** §11.4 step 6: a re-keyed match is "never fetched or imported, and its
title is never stored or shown … if it is a different recording, [Dan] presses **Release**, whose
question then shows no title". §12.1: "No title of a consent-listed recording not in the archive
is ever stored or shown: not in the cache, the Sync screen…". `ReleaseDialog.tsx:10` itself says
"A re-keyed match's preview has none". §11.4 step 3 adds that while a listing is held with a
census change or under 80% overlap, "no title of a recording not here is stored or shown".

**Sequence:**
1. Consent-listed recording A is listed, then Plaud lists it under a new ID B (same form, A
   gone). `compare.rekey_matches` matches B to A by start time and duration: B is `excluded`
   with `excluded_by = "rekeyed"`, `plaud_listing.rekey_of = A`, flagged "may be a consent-listed
   … recording back under a new ID: confirm".
2. The Sync screen's Kept out row for B has `release: true` (`_kept_out`, plaud.py:1287), so its
   **Release** button shows. Dan presses it to look (the button is the only way the screen offers
   to decide).
3. `_release_preview`: `may_preview` checks consent (B isn't on it), a stop and "here"; `kept_by`
   returns `"rekeyed"`, so it passes. `worker.listed_now(B)` reads B's current title; no keep-out
   word; `Cache.put_preview(…, Preview(B, listed.title, …))` writes it to `plaud-cache.db`.
4. The web app's `release_preview` (plaud.py:1419-1461) takes it and sends it to the session;
   `ReleaseDialog` renders "Let '<title>' (…) in?".

**Leak:** the title of what the app itself suspects is a consent-listed recording reaches
`plaud-cache.db` (up to 10 minutes or until taken) and the screen. `purge_titles` doesn't catch
it: B isn't on the consent list and the title carries no keep-out word. Same path for a review
row's **Show title** (SyncPage.tsx:131-134) and for any Release preview while a held listing shows
Plaud's IDs changed: nothing in `may_preview`/`_release_preview` reads `K_HOLD`, so the very
titles §11.4 step 3 hides ("a consent-listed or kept-out recording may be among them under a new
ID") come back through the preview.

**Fix direction:** refuse a preview with no title for `kept_by == "rekeyed"` (reply carries date
and duration only, as the dialog already expects), and refuse every preview while `K_HOLD` has
`ids_changed`.

**Verdict:** CONFIRMED (every step traced; no test covers a re-keyed preview:
`test_plaud_release.py` only releases one).

---

## 2. Medium: after a lost `state.db`, any CLI listing before the worker's first start disables "needs your review" for good

**Where:** `packages/core/src/recordings/plaud/gate.py:105-119` (`detect_rebuild`), called only
from `worker.py:516` inside `_step`, which swallows any exception and never retries;
`packages/core/src/recordings/plaud/commands.py:1124-1146` (`cmd_sync`: `--dry-run` and
`--catch-up` never call it); `packages/core/src/recordings/plaud/compare.py:719-720`
(`review_marks` skips every ID already in `plaud_listing`); `compare.py:503-506`
(`record_compare` writes a `plaud_listing` row for every listed ID, a Compare's included).

**What the spec says:** §11.6: on a rebuilt `state.db`, every listed ID not here and made before
`rebuilt_at` waits for Dan's review, "whenever it is first listed … Why the protection holds: only
a recording made before the rebuild could have been kept out before it, and every one of them
waits for Dan". §12.2: "After a lost state.db, the record of what was kept out … is gone. Such a
recording waits in the rebuild's review list".

**Sequence (A, the dry run):** `state.db` is lost and rebuilt (the archive holds 2b snapshots).
Before the worker service has started on it (the restore steps stop `worker web` first), Dan runs
`recordings plaud sync --dry-run` to see where things stand. It lists and `record_compare` writes a
`plaud_listing` row for every listed ID, `needs_review = 0`, and no mark row (a Compare writes
none). The worker then starts: `detect_rebuild` finds no mark, records `rebuilt_at` and opens the
gate. At the next listing `review_marks` skips every ID already in `plaud_listing`, so none is
marked, and the first scheduled sync imports every recording that was kept out by an earlier title
(the word since gone from the title), every re-keyed match and every un-released ID: their
records went with the old `state.db`.

**Sequence (B, the catch-up):** same start, and Status reads "Scheduled sync waits for the first
catch-up (runbook)" (`gate_now`: no `catch_up_done_at`, no mark). Dan runs `--catch-up`: the gate
is closed, so it runs, with no `K_REBUILT`, imports the same recordings, and its imports write mark
rows, so the worker never detects the rebuild afterwards. The same follows if `detect_rebuild`
raises once at start: `_step` logs it, the gate stays closed, and nothing tries again.

**Leak:** audio, transcripts and notes of recordings Dan had kept out by title (or that matched a
consent-listed recording by time) enter the archive and the next hourly backup, untagged unless a
pattern matches.

**Fix direction:** run `detect_rebuild` (under the same lock) at the start of every CLI command that
lists or imports, and have it, or `review`, mark existing `plaud_listing` rows not here as
`cmd_review` does; retry it at ticks while it fails.

**Verdict:** CONFIRMED for the code path; the operator sequence (a CLI listing on the new
`state.db` before the worker's first start) is the precondition.

---

## 3. Low: a consent-listed or kept-out recording that is here never gets its date in the re-key cache

**Where:** `packages/core/src/recordings/plaud/compare.py:523-535` (`remember_keyed_out` takes
`bucket == EXCLUDED` rows only; a late consent listing or late keep-out is `HERE`, which wins in
`classify`, compare.py:351-360).

**Sequence:** recording R is here and becomes consent-listed (late listing, `private`, flag) or
kept out late. Before Dan takes it out by hand, Plaud lists it under a new ID B and stops listing
the old one. `rekey_matches` (compare.py:270-275) has no `keyed_out` row for R's ID, so B is not
matched and is `waiting`, its title stored in the cache (B isn't on the list). The import's only
remaining guard is the exact audio hash (`importer._flagged`, importer.py:561-575): identical
bytes are discarded; different bytes (a trim, a re-encode) are imported as a new, untagged
recording.

**Leak:** a second copy of a consent-listed recording in the archive and backups, without
`private`. §9.5 says the cache keeps "a consent-listed or kept-out ID's date, time and duration"
with no exception for one that is here.

**Verdict:** PLAUSIBLE (unverified: whether Plaud ever re-keys with different audio bytes).

---

## 4. Low: the run's own consent read is unguarded, and decides the re-key match and the cache's titles for the whole run

**Where:** `packages/core/src/recordings/plaud/sync.py:482` (`read_consent` once, at the start of
`run_sync`), used by `take_listing` (sync.py:602-616) for `classify`, `rekey_matches` and
`title_shown`; `compare.after_imports` (compare.py:576-629) writes those title decisions back to
the cache after the imports. The worker's `_watch_consent` (worker.py:797-827) and
`ConsentGuard` (importer.py:212-253) both refuse a read caught mid-write; this one doesn't.

**Sequence:** Dan saves the list in place (nano, an editor without atomic save) and a run starts in
the window, reading a readable but truncated list that lacks consent-listed ID A. If that listing
is the one where Plaud first shows A under new ID B, A is not in `excluded`, so B isn't matched and
is `waiting`. The importer checks B against the full list (fresh `ConsentGuard`), but B isn't on
it, and B's `rekey_of` is unset (`importer._before_any_byte` relies on `plaud_listing.rekey_of`), so
B is imported; from then on B is here and never matched again. Lesser effect, with no re-key: the
compare stores titles of consent-listed IDs the torn read missed, and `after_imports` rewrites any
title the tick purged mid-run (a consent edit or a stop during the run) until the next tick purges
again.

**Leak:** in the rare case, a consent-listed recording imported untagged; otherwise titles of
consent-listed recordings in `plaud-cache.db` for about one tick (the screen's `shown_title`
re-check catches IDs the cache already knows as consent).

**Verdict:** CONFIRMED (code path); the race window is small.

---

## 5. Low: Sync everything keeps checking after a hold recorded mid-run

**Where:** `packages/core/src/recordings/plaud/full.py:354-386` (`one()` runs `run.between()`,
which is `long_between` and can run a light turn that records a hold, then checks on regardless);
`packages/core/src/recordings/plaud/editsync.py:387-417` (`_may_check` never reads `K_HOLD`).
Re-sync refuses under a hold for exactly this reason (`resync.py:269-270`: "the newest listing is
suspect while one is on"); §14.2: "The sweep pauses under any hold".

**Sequence:** Sync everything is running; a due light turn inside `long_between` lists, finds
Plaud's IDs changed (census change with something absent, or overlap under 80%) and records the
hold. Sync everything goes on fetching envelopes by the IDs of its own (pre-hold) listing and
writing snapshots. The envelope's `id` check (editsync.py:441-442) can't tell if Plaud has handed
an old ID to a different recording.

**Leak:** only if Plaud reuses an ID for another recording: that recording's transcript written as
a snapshot of the archive's one, the case the hold exists for.

**Verdict:** PLAUSIBLE (unverified: Plaud reusing an ID).

---

## 6. Low: an invalid `keep_out_word` idles only the scheduled syncs; requests still import, with no word enforced

**Where:** `worker.py:1200` and `:972` check `keep_out_word_ok`; the request handlers
`_sync_request`/`_import`/`_resync`/`_full`/`_release` (worker.py:1554-1626, 1716, 1760, 1487)
and `recordings_ui/plaud.py:check_request` (104-164) don't. `writer.py:338` sets
`keep_out_word = None` for an invalid word, so `keep_out_words()` is only the words enforced at
earlier starts. The UI disables Sync now and the imports (plaud.py:1192-1211), but Compare stays
enabled.

**Sequence:** a config whose word is invalid from the first deploy (nothing ever enforced), or
Dan's page loaded before the word broke: a Sync now or Import request from it is served, with
`words == ()`. A recording titled with the word Dan meant ("noarchive …", the default the README
promises) is imported; a Compare stores and shows such titles in the cache.

**Verdict:** CONFIRMED (path); needs a request the UI's disabled buttons would normally prevent.
§12.2: "An invalid word leaves the worker idle".

---

## 7. Low: a new keep-out word never re-checks the titles of recordings already here

**Where:** `packages/core/src/recordings/plaud/listing_privacy.py:103` (a title whose hash is the
marker's is skipped); `packages/core/src/recordings/plaud/privacy.py:184` (case 5 re-runs patterns
only, `words_too=False`); `compare.py:357` computes `Classified.word` for every here row, and
nothing consumes it (the module notes at compare.py:46-47 say the import area would).

**Sequence:** recording R here is titled "Secret: budget", judged long ago, not private. Dan sets
`keep_out_word = "secret"`; the worker enforces it at start. Each listing computes R's `word`, but
`privacy_from_listing` skips R (marker equals the title's hash) and no pass re-checks words, so R
gets neither `private` nor the late keep-out flag, and edit-sync keeps fetching it.

**Verdict:** PLAUSIBLE: the code path is traced; whether §12.2's "checked on every listed title at
every run" and §13.1 case 3 cover a word added after the title was judged is a reading of the spec
for Dan to decide.

---

Also noted, below Low: `release_preview` in the web app (plaud.py:1419-1461) doesn't apply
`title_allowed` again before the title leaves, as CLAUDE.md's cache bullet asks of a reader; the
worker checks before writing, and the tick purges, so the gap is one poll.
