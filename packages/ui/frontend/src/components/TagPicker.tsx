import { Lock, Plus, X } from "lucide-react";
import {
  useEffect, useId, useLayoutEffect, useMemo, useRef, useState, type CSSProperties, type KeyboardEvent, type MouseEvent,
  type ReactNode,
} from "react";

import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { composing, isMac } from "@/lib/keys";
import { shownTags } from "@/lib/library";
import {
  alreadyWords, createOffer, createRefusal, pickerGroups, pickerTags, stateWords, tagStates, type PickerTag, type TagState,
} from "@/lib/picker";
import { PHONE, TOUCH, useMediaQuery } from "@/lib/useMediaQuery";
import { useSheetRoom } from "@/lib/viewport";
import { inToaster } from "@/tagging/toaster";
import { useReturnFocus } from "@/tagging/useReturnFocus";
import { useTaggingContext, type PickerFrom, type PickerTarget } from "@/tagging/useTagging";

export function pickerTitle(ids: readonly string[], titleOf: (id: string) => string): string {
  return ids.length === 1 ? `Tag ${titleOf(ids[0])}` : `Tag ${ids.length.toLocaleString("en")} recordings`;
}

/** The keys line under the list, and the box's description. */
export function keysLine(mac: boolean): string {
  return `Enter add · Shift+Enter create · Shift+Delete or ${mac ? "⌘⌫" : "Ctrl+Backspace"} remove · Esc close`;
}

const TAG = "tag:";
const CREATE = "create:";
const GLYPH: Record<TagState["state"], string> = { all: "✓", some: "–", none: "" };
const BOX = "Find or create a tag";
const PICK = "Type a tag name, or use the arrows";
const NO_MATCH = "No tag matches.";
const PAGE = 8; // items a Page Up or Page Down moves
// The keys that move the highlight: cmdk's (arrows, Home, End, and Ctrl+N/J/P/K), and paging.
const MOVES = new Set(["ArrowUp", "ArrowDown", "Home", "End", "PageUp", "PageDown"]);
const steps = (e: KeyboardEvent) => e.key === "ArrowUp" || e.key === "ArrowDown" || (e.ctrlKey && ["n", "j", "p", "k"].includes(e.key));
const moves = (e: KeyboardEvent) => MOVES.has(e.key) || steps(e);

/** A click in the list keeps focus in the box, so typing goes on there (and a touch keeps the
 * on-screen keyboard up). */
const keepFocus = (e: MouseEvent) => e.preventDefault();

/** An Esc an IME is composing with is the IME's: it must not close the picker. */
const imeEscape = (e: globalThis.KeyboardEvent) => {
  if (composing(e)) e.preventDefault();
};

/** cmdk 1.1.1 leaves the box's aria-activedescendant on the item highlighted before, or none,
 * after the highlight moves without an arrow key (on opening, typing, or the box clearing): it
 * reads the highlighted item before a controlled value has been applied
 * (docs/upstream-issues.md). So the highlighted item's id is mirrored onto the box, and the list,
 * whenever an item's aria-selected changes, cmdk writes its own value back, or the root's
 * data-highlight turns the highlight on or off: while it is off, nothing is announced. */
function mirrorActiveItem(root: HTMLElement): () => void {
  const ATTR = "aria-activedescendant";
  const box = root.querySelector("[cmdk-input]");
  const list = root.querySelector("[cmdk-list]");
  if (!box || !list) return () => {};
  const sync = () => {
    const on = root.getAttribute("data-highlight") !== "off";
    const id = (on && list.querySelector("[cmdk-item][aria-selected=true]")?.id) || null;
    for (const el of [box, list]) {
      if (id === null) {
        if (el.hasAttribute(ATTR)) el.removeAttribute(ATTR);
      } else if (el.getAttribute(ATTR) !== id) {
        el.setAttribute(ATTR, id);
      }
    }
  };
  const observer = new MutationObserver(sync);
  observer.observe(list, { subtree: true, childList: true, attributes: true, attributeFilter: ["aria-selected", ATTR] });
  observer.observe(box, { attributes: true, attributeFilter: [ATTR] });
  observer.observe(root, { attributes: true, attributeFilter: ["data-highlight"] });
  sync();
  return () => observer.disconnect();
}

/** The picker's body, shared by both forms (spec §6). `heading` is the element that shows the
 * title, and names the form: the dialog's DialogTitle, or the popover's labelled heading. The box
 * has a name of its own, so the title is read once.
 * - Enter only ever adds: the highlighted tag, then the box clears and the picker stays open. On
 *   a ✓ item it says "Already on all 12" and sends nothing. With nothing listed it says why.
 * - With an empty box, Enter and the removal keys act only on an item the user moved to (arrows,
 *   Home, End, Page Up or Down, the pointer); never on the top item unasked, as after opening or
 *   after an add has cleared the box (ruled, Task 11 council). Until then no item is shown or
 *   announced as highlighted, so what is seen and heard matches what Enter does; the first arrow
 *   shows the top item without stepping past it (ruled, fix round 2).
 * - Shift+Enter creates exactly the typed text (trimmed, NFC), when Create is offered; otherwise
 *   it says why not.
 * - Shift+Delete removes the highlighted ✓ or – tag from the targets; so does Cmd/Ctrl+Backspace
 *   in an empty box (with text, it edits the text); the × on the item does with the mouse. A tag
 *   removed here stays listed, as none, when its last use goes.
 * - Esc closes the topmost layer only (Radix): the confirmation over the picker, then the picker.
 * States are in words as well as glyphs, counting pending changes at once. Private tags are
 * grouped under a lock by the server's flags (pickerTags reads them from the view's tags, as
 * tagging.isPrivate does); the browser never applies the rule itself. */
export function PickerBody({ ids, heading }: { ids: string[]; heading: ReactNode }) {
  const tagging = useTaggingContext();
  const { view, sessionTags, pending } = tagging;
  const keysId = useId();
  const keys = useMemo(() => keysLine(isMac()), []);
  const root = useRef<HTMLDivElement>(null);
  const [query, setQuery] = useState("");
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");
  const [chosen, setChosen] = useState(false); // the user moved the highlight since the last search
  // Tags removed here, with the server's private flag as it was then (it listed them). An add isn't
  // recorded: it can be refused, and a new tag is held by sessionTags.
  const [touched, setTouched] = useState<ReadonlyMap<string, boolean>>(new Map());

  const kept = useMemo(() => [...touched].map(([tag, flag]) => ({ tag, private: flag, count: 0 })), [touched]);
  const all = useMemo(() => (view ? pickerTags(view, sessionTags, kept) : []), [view, sessionTags, kept]);
  // The targets' tags as they will be: a pending add counts at once, a pending removal is gone
  // (spec §6), so a quick second Enter on a tag just added says "Already…" and sends nothing.
  const stateOf = useMemo(() => {
    const wanted = new Set(ids);
    const rows = view ? view.recordings.filter((r) => wanted.has(r.id)) : [];
    return tagStates(rows.map((r) => ({ tags: shownTags(r, pending).filter((t) => t.pending !== "remove") })));
  }, [view, ids, pending]);

  const groups = pickerGroups(all, query);
  const create = createOffer(all, query);
  const listed = groups.length > 0 || create !== null;
  const blank = query.trim() === "";
  const shown = chosen || !blank; // the highlight is shown and announced (data-highlight)
  // cmdk highlights the first item of each new list (React renders the input, and cmdk's
  // re-highlight, before the next key), and reports each highlight here.
  const highlighted = value.startsWith(TAG) ? value.slice(TAG.length) : null;

  useLayoutEffect(() => (root.current ? mirrorActiveItem(root.current) : undefined), []);

  const touch = (tag: string) => {
    if (touched.get(tag) !== tagging.isPrivate(tag)) setTouched((m) => new Map(m).set(tag, tagging.isPrivate(tag)));
  };
  /** The value of the first item listed for a query: each search starts there (cmdk does the same
   * when the query changes; this also covers an add that clears an already empty box). */
  const firstOf = (q: string) => {
    const top = pickerGroups(all, q)[0]?.tags[0];
    if (top) return `${TAG}${top.tag}`;
    const offer = createOffer(all, q);
    return offer ? `${CREATE}${offer}` : "";
  };
  const search = (q: string) => {
    setQuery(q);
    setValue(firstOf(q));
    setNote("");
    setChosen(false);
  };
  const add = (tag: string) => {
    const state = stateOf(tag);
    if (state.state === "all") {
      setNote(alreadyWords(state));
      return;
    }
    // Not touched: an add can be refused, and a touched tag stays listed at 0. A new tag is held
    // by sessionTags (which a refusal clears); a listed one is in the view.
    tagging.tag("add", ids, [tag], { kind: "picker" });
    search("");
  };
  const remove = (tag: string) => {
    if (stateOf(tag).state === "none") return;
    touch(tag);
    tagging.tag("remove", ids, [tag], { kind: "picker" });
    setNote("");
  };
  /** Page Up and Down, which cmdk 1.1.1 doesn't handle: a page of items at a time. */
  const page = (list: HTMLElement, by: number) => {
    const items = [...list.querySelectorAll<HTMLElement>("[cmdk-item]:not([aria-disabled=true])")];
    const at = items.findIndex((el) => el.getAttribute("aria-selected") === "true");
    const to = items[Math.min(items.length - 1, Math.max(0, Math.max(at, 0) + by))];
    if (!to) return;
    setValue(to.getAttribute("data-value") ?? "");
    to.scrollIntoView({ block: "nearest" });
  };

  // Runs before cmdk's own keys; cmdk skips an event this prevents, and any key while composing.
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (composing(e)) return; // an IME's Enter or Backspace is the IME's
    if (moves(e)) {
      setChosen(true);
      if (!shown && steps(e)) {
        e.preventDefault(); // the first arrow shows the top item; cmdk would step past it
        return;
      }
      if (e.key === "PageUp" || e.key === "PageDown") {
        e.preventDefault();
        page(e.currentTarget, e.key === "PageDown" ? PAGE : -PAGE);
      }
      return;
    }
    if (e.key === "Enter" && e.shiftKey) {
      e.preventDefault(); // so cmdk never adds the highlighted tag instead
      if (create) add(create);
      else setNote(createRefusal(all, query) ?? (blank ? PICK : ""));
      return;
    }
    if (e.key === "Enter") {
      if (blank && !chosen) {
        e.preventDefault(); // nobody chose the top item
        setNote(PICK);
      } else if (!listed) {
        e.preventDefault();
        setNote(createRefusal(all, query) ?? NO_MATCH);
      }
      return; // otherwise cmdk adds the highlighted item
    }
    // Shift+Delete always; Cmd/Ctrl+Backspace only in an empty box, where it has no text to edit.
    const removing = (e.key === "Delete" && e.shiftKey) || (e.key === "Backspace" && (e.metaKey || e.ctrlKey) && query === "");
    if (!removing) return;
    if (blank && !chosen) {
      e.preventDefault();
      setNote(PICK);
    } else if (highlighted !== null && stateOf(highlighted).state !== "none") {
      e.preventDefault();
      remove(highlighted);
    }
  };

  const item = (t: PickerTag) => {
    const state = stateOf(t.tag);
    const words = stateWords(state);
    return (
      <CommandItem
        key={t.tag}
        value={`${TAG}${t.tag}`}
        onSelect={() => add(t.tag)}
        onMouseDown={keepFocus}
        className="picker-item"
        aria-label={words ? `${t.tag}, ${words}` : t.tag}
        data-testid="picker-item"
        data-tag={t.tag}
        data-state={state.state}
      >
        <span className="picker-glyph" aria-hidden="true">{GLYPH[state.state]}</span>
        {/* Isolated left to right, so a right-to-left name can't reorder what's around it; truncated
            when long, in full in the item's name and title, and in full while highlighted. */}
        <span className="picker-tag" title={t.tag}><bdi dir="ltr">{t.tag}</bdi></span>
        {words && <span className="picker-state" aria-hidden="true">{words}</span>}
        {state.state !== "none" && (
          // The mouse's way to remove; the keyboard's is Shift+Delete. Not a button inside the
          // option (axe: nested-interactive), so it is hidden from assistive technology.
          <span
            className="picker-x"
            aria-hidden="true"
            data-testid="picker-x"
            title={`Remove ${t.tag}`}
            onMouseDown={keepFocus}
            onClick={(e) => {
              // The one stop kept: this click is inside the open layer and must not reach the cmdk item
              // around it, which selects on click and would add the tag (cmdk 1.1.1). Nothing outside
              // the layer needs it, so Radix still sees clicks elsewhere.
              e.stopPropagation();
              remove(t.tag);
            }}
          >
            <X />
          </span>
        )}
      </CommandItem>
    );
  };

  const empty = blank ? "No tags yet: type a name to create one." : (createRefusal(all, query) ?? NO_MATCH);
  return (
    <Command
      ref={root}
      shouldFilter={false}
      value={value}
      onValueChange={setValue}
      onKeyDown={onKeyDown}
      label={BOX}
      className="picker"
      data-highlight={shown ? "on" : "off"}
    >
      {heading}
      <CommandInput value={query} onValueChange={search} placeholder={BOX} aria-describedby={keysId} data-testid="picker-input" />
      {/* Pointer movement over the list moves the highlight (cmdk): a choice, as the arrows are. */}
      <CommandList onPointerMove={() => setChosen(true)}>
        <CommandEmpty>{empty}</CommandEmpty>
        {groups.map((g) => (
          <CommandGroup
            key={g.locked ? "locked" : "open"}
            heading={g.locked ? <span className="picker-lock"><Lock aria-hidden="true" />Private</span> : "Tags"}
          >
            {g.tags.map(item)}
          </CommandGroup>
        ))}
        {create && (
          <CommandGroup heading="New">
            <CommandItem
              value={`${CREATE}${create}`}
              onSelect={() => add(create)}
              onMouseDown={keepFocus}
              className="picker-item"
              aria-label={`Create ${create}`}
              data-testid="picker-create"
            >
              <Plus aria-hidden="true" />
              <span className="picker-tag">Create “<bdi dir="ltr">{create}</bdi>”</span>
            </CommandItem>
          </CommandGroup>
        )}
      </CommandList>
      <p className="picker-note" data-testid="picker-note" role="status">{note}</p>
      <p className="picker-keys" id={keysId}>{keys}</p>
    </Command>
  );
}

/** The + a phone's sheet was opened from, found again (Task 11 carry): a row's, by its row's id,
 * as a virtual list may have drawn the row anew; or the header's. */
export function plusFor(from: PickerFrom): HTMLElement | null {
  if (from.place === "header") return document.querySelector<HTMLElement>('[data-testid="recording-header"] [data-control=add]');
  const row = [...document.querySelectorAll<HTMLElement>("[role=row][data-id]")].find((r) => r.dataset.id === from.id);
  return row?.querySelector<HTMLElement>("[data-control=add]") ?? null;
}

/** The dialog form: `T`, the selection bar's Tag…, a drop (spec §6), and on a phone the + too,
 * where it is a full-width bottom sheet (app.css). Mounted once, in App.
 *
 * Not `CommandDialog`: as generated, it renders its children as they are and puts its sr-only
 * title outside the dialog's content (Task 8), and `PickerBody`'s `<Command>`, which carries
 * `shouldFilter`, `value` and `onKeyDown`, must be the only cmdk root. So the dialog is built from
 * the parts `CommandDialog` uses. Its visible title is its DialogTitle, which names it; the box
 * describes the keys, so the dialog has no description of its own.
 *
 * Radix returns focus only to a Trigger, and this dialog has none, so useReturnFocus gives focus
 * back to what had it (the row or cell `T` was pressed on, the selection bar's button), or to the
 * + a phone's sheet came from, which a tap in Safari never focused. The last target stays drawn
 * while the dialog animates out.
 *
 * On a touch screen it sits on what the screen shows: above an on-screen keyboard and below the
 * toasts, which come in at the top there, its list giving up the room (useSheetRoom, app.css;
 * checked: MDN, VisualViewport and the dynamic viewport units). Radix makes everything outside a
 * modal dialog inert, and a tap there closes it; a tap on a toast does neither, so a toast's Undo
 * works with the picker open (Task 15 fix round 1, item 1; checked: Radix Dialog's
 * onPointerDownOutside, and DismissableLayer's source, which dismisses only when it isn't
 * prevented). */
export function TagPickerDialog() {
  const tagging = useTaggingContext();
  const target = tagging.picker;
  const [shown, setShown] = useState<PickerTarget | null>(target);
  if (target !== null && target !== shown) setShown(target);
  const from = useRef<PickerFrom | null>(null);
  from.current = shown?.from ?? null;
  const returnFocus = useReturnFocus(() => (from.current ? plusFor(from.current) : null));
  const phone = useMediaQuery(PHONE);
  const touch = useMediaQuery(TOUCH);
  const room = useSheetRoom((phone || touch) && target !== null);
  const sheet = room
    ? ({ "--sheet-top": `${room.top}px`, "--sheet-bottom": `${room.bottom}px`, "--sheet-height": `${room.height}px` } as CSSProperties)
    : undefined;
  // The phone's sheet has a Close button with a 44 px target (app.css): with the keyboard up there
  // is no Esc, and the overlay shows only a gutter (Task 15 fix round 2, item 3).
  return (
    <Dialog open={target !== null} onOpenChange={(open) => { if (!open) tagging.closePicker(); }}>
      <DialogContent
        className="picker-dialog top-1/3 translate-y-0 overflow-hidden p-0"
        style={sheet}
        showCloseButton={phone}
        aria-describedby={undefined}
        onEscapeKeyDown={imeEscape}
        onPointerDownOutside={(e) => { if (inToaster(e.target)) e.preventDefault(); }}
        {...returnFocus}
      >
        {shown && <PickerBody ids={shown.ids} heading={<DialogTitle className="picker-title">{shown.title}</DialogTitle>} />}
      </DialogContent>
    </Dialog>
  );
}

/** A row's picker opens beside its +, and stays on screen when a virtual list scrolls its row away
 * (the row stays drawn while the picker is open). Radix shifts the content only along the
 * alignment axis, and `sticky: "always"` keeps it inside the viewport there whatever the trigger
 * does; beside the +, that axis is the vertical one (checked: Radix Popper 1.4.0's shift
 * middleware, `crossAxis: false`; the Popover docs' `side`, `sticky`, `collisionPadding`). */
const ROW_PLACEMENT = { side: "right", sticky: "always" } as const;

const inConfirmation = (target: EventTarget | null) => target instanceof Element && target.closest("[role=alertdialog]") !== null;

/** The + after a row's chips, and in the header: the picker for that one recording, as a Popover
 * on the +, whose row stays rendered (spec §6). Clicking it never changes the selection or opens
 * the row. Radix gives focus back to the + when the popover closes. `onOpenChange` hears every
 * open and close, so a virtual list can keep the row drawn while its picker is open (a click
 * doesn't focus a button in Safari or Firefox, or on touch, so focus can't keep it).
 *
 * On a phone the + opens the dialog form instead, as a full-width bottom sheet (spec §6), told
 * which + it came from. The button is the same element either way, so turning a tablet into a
 * phone's width closes an open popover, reports the close, and leaves focus on the + (Task 11
 * carry: Radix gives focus back to the trigger when a controlled popover closes). The sheet's open
 * and close are reported too, so a virtual list keeps the row drawn meanwhile (fix round 1, item
 * 10).
 *
 * On a touch screen the popover keeps clear of an on-screen keyboard with nothing added here:
 * floating-ui, which Radix places it with, measures against the visual viewport, and its
 * autoUpdate follows the visual viewport's resize (an overflow ancestor), so the popover moves and
 * its max-height (--radix-popover-content-available-height) shrinks as the keyboard comes. Adding
 * the keyboard to the collision padding counted it twice (Task 15 fix round 2, item 1; checked:
 * floating-ui's autoUpdate docs, and @floating-ui/dom 1.8.0's getViewportRect and
 * getOverflowAncestors, which the docs don't cover). */
export function AddTagsButton({ id, title, place, onOpenChange }: {
  id: string; title: string; place: "row" | "header"; onOpenChange?: (open: boolean) => void;
}) {
  const tagging = useTaggingContext();
  const headingId = useId();
  const phone = useMediaQuery(PHONE);
  const [open, setOpen] = useState(false);
  const ids = useMemo(() => [id], [id]);
  const change = (next: boolean) => {
    setOpen(next);
    onOpenChange?.(next);
  };
  useEffect(() => {
    if (!phone || !open) return;
    setOpen(false);
    onOpenChange?.(false);
  }, [phone, open, onOpenChange]);
  // The sheet this + opened is open while the picker's target still comes from it.
  const sheetOpen = tagging.picker?.from?.id === id && tagging.picker.from.place === place;
  const wasSheet = useRef(false);
  useLayoutEffect(() => {
    if (sheetOpen === wasSheet.current) return;
    wasSheet.current = sheetOpen;
    onOpenChange?.(sheetOpen);
  }, [sheetOpen, onOpenChange]);
  // The confirmation opens over the picker without closing it (spec §6). A focus move into the
  // Alert Dialog is "outside" this Popover, which Radix would close, so it is refused while the
  // confirmation is open or when it lands in one. (A pointer can't reach outside: the modal
  // confirmation turns pointer events off below it.)
  const keepOpen = (e: Event) => {
    if (tagging.confirm !== null || inConfirmation(e.target)) e.preventDefault();
  };
  return (
    <Popover open={open && !phone} onOpenChange={change}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="add-tag"
          data-control="add"
          data-testid="add-tag"
          tabIndex={place === "row" ? -1 : 0}
          aria-label={`Add tags to ${title}`}
          onClick={(e) => {
            // No stopPropagation: the row's click already ignores a [data-control], and Radix takes
            // a click that never reaches the document as consumed, so another open picker would
            // stay open (found by the built-bundle e2e test).
            if (!phone) return;
            e.preventDefault(); // Radix skips its toggle: on a phone, the sheet instead
            tagging.openPicker({ ids, title: pickerTitle(ids, () => title), from: { id, place } });
          }}
        >
          <Plus aria-hidden="true" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        className="picker-popover p-0"
        align="start"
        {...(place === "row" ? ROW_PLACEMENT : {})}
        collisionPadding={8}
        aria-labelledby={headingId}
        onEscapeKeyDown={imeEscape}
        onFocusOutside={keepOpen}
        onInteractOutside={keepOpen}
      >
        <PickerBody ids={ids} heading={<div id={headingId} className="picker-title">Tag {title}</div>} />
      </PopoverContent>
    </Popover>
  );
}
