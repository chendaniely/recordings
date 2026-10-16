import { useEffect, useRef, type FocusEvent } from "react";
import { toast } from "sonner";

import { TOAST_MS, pausableTimer } from "@/lib/dismiss";
import { textOf, type Text } from "@/lib/toasts";

/** Text with tag names as <code>, always as text nodes. */
export function Rich({ text }: { text: Text }) {
  return <>{text.map((part, i) => (typeof part === "string" ? <span key={i}>{part}</span> : <code key={i}>{part.code}</code>))}</>;
}

const leaving = (e: FocusEvent<HTMLElement>) => !e.currentTarget.contains(e.relatedTarget as Node | null);

/** An action's toast: about 8 s, paused while hovered or focused (spec §6; plan decision 9). One
 * timer per toast: a toast updated in place (the grid's running "4 changes") gets a fresh 8 s
 * that keeps any pause, so it never goes from under the pointer. */
export function ActionToast({ id, text, undo }: { id: string; text: Text; undo: { label: string; run: () => void } | null }) {
  const timer = useRef<ReturnType<typeof pausableTimer> | null>(null);
  useEffect(() => {
    timer.current = pausableTimer(TOAST_MS, () => toast.dismiss(id));
    return () => timer.current?.cancel();
  }, [id]);
  const said = textOf(text);
  useEffect(() => {
    timer.current?.restart(TOAST_MS);
  }, [said]);
  return (
    <div
      className="action-toast"
      data-testid="action-toast"
      onMouseEnter={() => timer.current?.pause()}
      onMouseLeave={() => timer.current?.resume()}
      onFocus={(e) => leaving(e) && timer.current?.pause()}
      onBlur={(e) => leaving(e) && timer.current?.resume()}
    >
      <span className="action-toast-text"><Rich text={text} /></span>
      {undo && (
        <button type="button" className="toast-undo" onClick={() => { toast.dismiss(id); undo.run(); }}>
          {undo.label}
        </button>
      )}
    </div>
  );
}

/** Partial failures and stops: role="alert", and they stay until dismissed (spec §6). */
export function AlertToast({ id, lines }: { id: string; lines: string[] }) {
  return (
    <div role="alert" className="alert-toast" data-testid="alert-toast">
      {lines.map((line) => <p key={line}>{line}</p>)}
      <button type="button" className="toast-undo" onClick={() => toast.dismiss(id)}>Dismiss</button>
    </div>
  );
}

/** A batch's progress: announced at the start (and, by the result, at the end) only; the moving
 * count is hidden from the live region (spec §6). */
export function ProgressToast({ announced, shown }: { announced: string; shown: string }) {
  return (
    <div className="progress-toast" data-testid="progress-toast">
      <span className="sr-only">{announced}</span>
      <span aria-hidden="true">{shown}</span>
    </div>
  );
}
