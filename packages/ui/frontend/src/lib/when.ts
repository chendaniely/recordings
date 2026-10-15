/** The recording header's time (spec §12, §12.1): the recording's own local date and 24-hour time
 * with its UTC offset, as `2026-10-06 14:05 UTC+09:00`. It is read from the stored ISO string and
 * never converted to the browser's time zone, because the archive spans several. */
export function headerWhen(recordedAt: string): string {
  const m = /^(\d{4}-\d{2}-\d{2})T(\d{2}:\d{2})(?::\d{2}(?:\.\d+)?)?(Z|[+-]\d{2}:\d{2})$/.exec(recordedAt);
  if (!m) return recordedAt;
  return `${m[1]} ${m[2]} UTC${m[3] === "Z" ? "+00:00" : m[3]}`;
}
