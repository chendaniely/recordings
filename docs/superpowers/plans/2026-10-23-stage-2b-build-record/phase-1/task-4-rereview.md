# Task 4 re-review: fix round 79f79d2..b365ddc

63 tests pass (Python 3.14.3). I probed only the fake on 127.0.0.1.

## Findings

1. **Addressed.** `client.py:494-519`: a missing, odd or zero length is refused before `mkdir`, no chunk is written past it, and `finally` removes `.part`.
2. **Partly addressed.** `client.py:267-278`: `²`, `³` and `¹` are refused, but long numbers still escape (A).
3. **Addressed.** `client.py:498-519`, test:563.
4. **Addressed.** `client.py:305`, `504-506`, test:578.
5. **Addressed** (`client.py:379-381`), but it caused B.
6. **Addressed.** `client.py:397-402`, test:602.
7. **Addressed.** `_guarded` is at `client.py:319`. `alerts.py` is untouched.
8. **Addressed.** `client.py:21-26`. A makes line 21 false again.
9. **Addressed.** `client.py:141`.
10. **Addressed.** No `short=` is left.
11. **Addressed.** `CLAUDE.md:145-146`.
12. **Partly addressed. Minor.** test:610 pins only `client.py:213`. Deleting `_HTTPSConnection`'s `settimeout` (`client.py:219`, the old 212) still passes every test, and so does deleting `download`'s connect timeout (`:488`). **Fix:** stub `http.client.HTTPSConnection.connect` to set a plain socket, call `_HTTPSConnection.connect`, and check `gettimeout() == 60`.
13. **Addressed.** test:236.
14. **Addressed.** `conftest.py:40` runs at import. test:490 proves module-scoped fixtures are covered, and test:476 shows other hosts still resolve.

## New problems

**A. Important.** `client.py:273` and `277-278`: `float(int(text))` overflows above 308 digits, and `int()` refuses more than 4300. Probe: a 429 whose `Retry-After` has 400 digits raised `OverflowError`; one with 5000 digits raised `ValueError`. This happened through `list_page`, `fetch_link` and `download`, and each time `__context__` was an `HTTPError` holding the URL, which for a link is the signed one. The old `float(str)` returned `inf`, so this is a regression. A `Content-Length` of 5000 digits makes `download` raise a bare `ValueError` (`:494`). **Fix:** have `_ascii_number` return None past about 15 digits, and test 400 and 5000 digits through `_no_url_in`.

**B. Important.** `client.py:379`: `read(amt)` never raises `IncompleteRead`. CPython says so in a comment ("might break compatibility"), and our comment at `:377` is wrong. Probe: `fetch_link` returned a 20-byte fragment of a body declared as 52 bytes, so a truncated transcript block would be stored as complete. And `[]` declared as 500 bytes gave `list_page` an empty page. Before the fix, both cases raised. **Fix:** raise `on_short_body` when `len(body) > _MAX_BODY or response.length` (http.client leaves `length > 0` after an early end), and test a short `fetch_link` and a short `get_json`.

**C. Minor.** test:479-487: if the guard regresses, this fixture does a real DNS lookup of a Plaud host. The RED run did exactly that. **Fix:** check `socket.getaddrinfo.__qualname__` instead.

`hold` is fine: its daemon handler thread never delays teardown, and the stall test takes 0.30 s.

## Out of scope (both were there before)

- `conftest.py:30`: `endswith("plaud.ai")` also refuses names like `notplaud.ai`, which is harmless.
- `client.py:399`: a deeply nested body raises `RecursionError` from `json.loads`.

Verdict: changes needed (0 Critical, 2 Important)
