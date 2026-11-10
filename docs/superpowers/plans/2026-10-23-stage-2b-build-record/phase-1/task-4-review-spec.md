# Task 4 review: Spec axis (5e51267..79f79d2, Tier 1)

The committed files match the brief's code byte for byte. 80 tests pass, with proxies set too. The
probes ran against the fake on 127.0.0.1 only. Scope creep: none.

## Findings

1. **Important.** `client.py:452-461`, the download loop. The automated flag is **real**. When
   `Content-Length` is missing or invalid, or the body is chunked (http.client then ignores the
   length), nothing caps the bytes or the time. Probe: chunked with `Content-Length: 10`, and a body
   with no length, each wrote about 5 MB to `.part` before being refused. This breaks §10.1 ("longer
   than a ceiling scaled from `Content-Length`"). §11.5 step 5's disk guard (2 × `Content-Length`)
   assumes the length bounds the bytes, and the `.part` sits on the archive disk. The docstring at
   line 27 claims the rate rule gives the ceiling, which is false here. Test line 321 passes with
   the stream unbounded. **Smallest fix:** refuse before the loop when `expected` is None or 0, and
   raise `IncompleteDownload` as soon as `written > expected`. That makes the docstring true. Add
   chunked and no-length cases.
   `_get`'s unbounded `response.read()` (line 333) is also real, but **Minor**. §10.1 asks for no
   body cap, and the hosts are Plaud's over verified TLS (probe: CERT_REQUIRED, check_hostname on).
   Fix: `read(_MAX + 1)` with a generous fixed cap, and `short()` when it's over. Or put it on the
   phase-end list for S8.
2. **Important.** `client.py:262`. `'²'.isdigit()` is True, so `float('²')` raises `ValueError`
   inside `_get`'s and `download`'s `except HTTPError` handler. Probe: `list_page`, `fetch_link`
   and `download` each let a bare `ValueError` escape. Its unsuppressed `__context__` is the
   `HTTPError` that holds the full link, `X-Amz` signature included, in `.url`. `envelope()` then
   fails whole instead of recording `_data_link_error`. This breaks §10.1's "every error the client
   raises is its own class … no chained exception carries a URL". It isn't Critical: no printed
   traceback shows the URL (probed). Fix: accept ASCII digits only (`v.isascii() and v.isdigit()`),
   and do the same for `declared`. Test a 429 with `Retry-After: ²` through `_no_url_in`.
3. **Important.** `client.py:450,463,470`. Local disk errors become `PlaudOffline`. Probe: a
   read-only folder and a full disk mid-write both raised "offline". §11.5 treats a full disk as an
   archive-level error and §10.2 keeps "offline" for connection failures, so Status would say the
   wrong thing. Fix: catch the file side's `OSError` separately and let it go up unchanged. It names
   a path, never a URL.
4. **Minor.** `client.py:359` raises inside its handler, so `__context__` is the `JSONDecodeError`
   whose `.doc` holds the whole body (probed). It is suppressed in tracebacks, but this contradicts
   the module docstring. Raise it after the handler, and run `_no_url_in` on the broken-JSON cases.
5. **Minor.** No test pins the 10 s and 60 s timeouts: removing line 206 or 212 passes every test.
   The assert at test line 242 proves nothing, because that request never carried `Authorization`.
6. **Minor.** `conftest.py:43` is function-scoped, so a module- or session-scoped fixture runs
   without the guard. That meets decision 30 as written.

**C7.02:** proved by `test_a_link_with_a_control_character_or_an_unknown_scheme_raises_without_its_url`.
It runs 11 links, checks the fixed message and `_no_url_in`, and shows nothing was requested or
written.

**Docs:** the `plaud/__init__` docstring is updated, no other doc says "no network call", and the
CLAUDE.md bullet is accurate. The Scenarios rows stay "planned", as Task 2's does.

## Security checklist

1. Secrets: tokens and signatures are synthetic, the request log keeps header names only, and
   errors are fixed. One finding: #2, a URL held in an escaping exception's context. The token
   store is not applicable here (Phase 2).
2. Permissions: not applicable. No modes, folders or mounts are added; `.part` uses the umask
   (Phase 2's folder).
3. Network exposure: fine. Nothing listens. The fake is on loopback, the `plaud.ai` and proxy
   guards are verified, and `recordings_ui` loads no client at import (the runtime half is Tasks 7
   and 19's). See #6.
4. Failing closed: fine. A wrong shape is `PlaudBadResponse`, never an empty account; rows without
   an ID are counted; short, empty and stalled downloads never land. Consent and the gate are not
   applicable.
5. Injection: fine. No SQL or shell. A control character or an unknown scheme gives
   `PlaudLinkRefused` without the URL, and the ID is quoted with `safe=""`.
6. Logging: fine. The client logs nothing, `_data_link_error` holds the class name only, and the
   fake's `log_message` is silenced.

Verdict: changes needed (0 Critical, 3 Important)
