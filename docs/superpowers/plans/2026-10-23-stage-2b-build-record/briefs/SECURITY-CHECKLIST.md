# The security checklist (Tier 1 tasks; add to the Spec axis reviewer's report)

One line per item, each: **fine** (and why, briefly), **a finding** (file:line, severity, fix), or
**not applicable** (and why).

1. **Secrets:** no token, refresh token, signed URL (`X-Amz-` query), or secret value in code,
   fixtures, logs, alerts, exceptions, `ops_runs` rows, request results, Status views or commit text.
   Token files are written 0600 in their own folder, write-before-use, under their lock (spec §10.3).
2. **Permissions:** files and folders created with the modes the spec names; the token folder is
   mounted only in `worker`; `deadman_sync_url` only in `worker`, `deadman_url` only in `backup`.
3. **Network exposure:** nothing new listens; the UI imports no client, token or sync module, at
   import or at runtime (plan decision 31); only the worker reaches Plaud; tests never reach the
   network (the fake server or fakes only).
4. **Failing closed:** an unreadable consent list, a bad keep-out word, an unknown ID form, a partial
   listing or a crash between steps never imports, never drops `private`, never shows a title of a
   recording not here; the privacy gate holds (`recordings.tagops`); no title in any alert.
5. **Injection:** SQL is parameterised; no shell built from data; a link with a control character or
   an unknown scheme raises the client's own error, with no URL in it; paths built from IDs are
   validated.
6. **Logging:** logs carry fixed phrases, IDs, counts and exception class names, never a title, a tag
   name, `str(exc)`, a URL with a query, or a transcript line. No `log.exception` or `exc_info=True`
   where the spec forbids it.
