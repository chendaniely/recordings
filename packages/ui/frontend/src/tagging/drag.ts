/** A drag that started in this page (spec §6): the recordings it carries, set at dragstart, and its
 * own marker, a custom type also set on its dataTransfer. The IDs never come from dataTransfer. */
export interface InPageDrag { ids: string[]; mark: string }
export type DragRef = { current: InPageDrag | null };

/** Sidebar items a drag may drop on carry this attribute. */
export const DROP_TARGET = "data-drop-target";
const MARK = "application/x-recordings-drag-";

/** A new marker for each drag. Lowercase: setData lowercases a format, so `types` lists it so
 * (HTML spec, "The DataTransfer interface"). */
export function newDragMark(): string {
  const bytes = crypto.getRandomValues(new Uint8Array(8));
  return MARK + [...bytes].map((b) => b.toString(16).padStart(2, "0")).join("");
}

type WithTransfer = { dataTransfer?: { types: ArrayLike<string> } | null };

/** Whether a drag event is this page's drag: its state is set and its marker is among the event's
 * types. Types can be read in every drag event, dragover included (protected mode); a custom type
 * is listed for a drag from the same page in Chromium, Firefox and Safari (checked: the HTML
 * spec's drag data store; WebKit's typesSafeForDOMToReadAndWrite, which lists a same-origin
 * drag's custom types). So state left by a drag whose dragend never came can't make a file, text
 * or another window's drag act. */
export function ours(drag: InPageDrag | null, event: Event | WithTransfer): drag is InPageDrag {
  const types = (event as WithTransfer).dataTransfer?.types;
  return drag !== null && types !== undefined && Array.from(types).includes(drag.mark);
}

/** The window forgets a drag, whatever a handler below does with the event (capture):
 * - on any dragend;
 * - after any drop, once the drop target's own handler has read it;
 * - on the next pointerdown anywhere: no drag is under way then.
 * And this page's drag dropped anywhere but a drop target (a text field takes text/plain by
 * default) is cancelled, so "2 recordings" is never pasted there. Returns the cleanup. */
export function watchDrags(drag: DragRef, win: Window = window): () => void {
  const forget = () => { drag.current = null; };
  const dropped = (e: Event) => {
    const was = drag.current;
    if (!was) return;
    const onTarget = e.target instanceof Element && e.target.closest(`[${DROP_TARGET}]`) !== null;
    if (ours(was, e) && !onTarget) e.preventDefault();
    win.setTimeout(() => { if (drag.current === was) drag.current = null; }, 0);
  };
  win.addEventListener("dragend", forget, true);
  win.addEventListener("drop", dropped, true);
  win.addEventListener("pointerdown", forget, true);
  return () => {
    win.removeEventListener("dragend", forget, true);
    win.removeEventListener("drop", dropped, true);
    win.removeEventListener("pointerdown", forget, true);
  };
}
