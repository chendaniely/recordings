# Task 5 review: Spec axis (Tier 1)

The code is the brief's, verbatim, and 35 tests pass. I probed S7 and S9 against the fake on 127.0.0.1. No Scenarios are named. The `test_the_guard_refuses_*` tests prove §18's guard test. No doc was made untrue.

## Critical
1. `scripts/plaud_spike.py:743-750`: S7's ID-form step fetches `files/{id}` for `named[0]` by both forms, even right after `_checked` refused that envelope's title. In the probe, the title "Private: synthetic" printed "refused by the guard", then "of_ form: ok". This breaks §18 ("never fetches … a private recording"). Fix: use the first row `_checked` passed, and add an S7 test.

## Important
2. `:842`, `test_plaud_spike.py:313`: a kept-out recording was never imported, so the `here` check never catches it. In the probe, `s9 --id <its name> --own-clip` on an old "NoArchive" row fetched its envelope and transcript block (§18: "a throwaway recording made for it"). Fix: also require a recent row, and correct the comment.
3. `:876`, `:888`: S9 prints the names of changed parts, which include note tab names (the probe printed `notes:plaud-counselling`). S8 masks these (`:795`). Fix: mask them as S8 does.
4. `:492-513`, `:551-562`: S2 and S4 send urllib's default User-Agent, which `client.py` says gets a flat 403 from Plaud's API. A 403 with no OAuth code, or no status at all, prints "stop and ask Dan" (§18's design-fails outcome), and S4 records that 403 as the refusal. Fix: send `client.USER_AGENT`, say "unreachable" when there is no status, and say "compare first" for every refusal.
5. `:246-254`, runbook `:551`: the consent and private checks match by ID only. They match nothing if S7 finds the hex differs, or if `[archive] path` names a wrong but valid archive (`demo/archive`, or a restore copy). S8 and S11 then run on titles alone. Fix: refuse `files/{id}` while the listing and the archive share no ID, and have the runbook stop session B when S7's "in the archive" count is 0.
6. `:930-938`: S11 always prints "Content-Length: present", but a missing one reads as "IncompleteDownload", the same as a stall (§18 asks). Fix: report it from the Range probe's header names, which the script discards.

## Minor
- `:768-772`: S8 and S9 check the title on one fetch, then fetch the blocks on later fetches without checking.
- `:257-268`: the backup paths come from the session config's `[backup]`, which runbook 1c never mentions.
- `:203-207`: an unparsable `keep_out_words` row counts as no words, not as a refusal.
- Runbook `:555`, `:583`: session C omits the setup and a fresh sign-in, and nothing says what to do on a mid-session `PlaudAuthError`. `$cmphome` and `$otherhome` exist only in the shell that made them.

## Security checklist
1. Secrets: fine. Nothing printed is a token. S2 saves tokens before using them, at 0600 (mkstemp) in the 0700 folder. The runbook copies under `umask 077`.
2. Permissions: fine. The folder is made at 0700, refused when wider, and kept outside the archive, the state folder and `/plaud`. The token file must sit inside it. Mounts don't apply.
3. Network: fine. Nothing listens, and the UI imports nothing new. The tests use the fake, and S2's POST is replaced.
4. Failing closed: findings 1, 2 and 5. The other stops refuse before any call, and the tests cover them.
5. Injection: fine. The SQL is parameterised, nothing runs a shell, and file names come from `short()`. The token URL must be https, and links go through `check_link`.
6. Logging: finding 3. Otherwise errors print by class name only, and `str(exc)` is used only for `SpikeRefused` and `ConfigError`.

Verdict: changes needed (1 Critical, 5 Important)
