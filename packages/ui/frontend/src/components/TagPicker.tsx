import { Lock, Plus, X } from "lucide-react";
import { useId, useLayoutEffect, useMemo, useRef, useState, type KeyboardEvent, type MouseEvent, type ReactNode } from "react";

import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Dialog, DialogContent, DialogTitle } from "@/components/ui/dialog";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { composing, isMac } from "@/lib/keys";
import { shownTags } from "@/lib/library";
import {
  alreadyWords, createOffer, createRefusal, pickerGroups, pickerTags, stateWords, tagStates, type PickerTag, type TagState,
} from "@/lib/picker";
import { useReturnFocus } from "@/tagging/useReturnFocus";
import { useTaggingContext, type PickerTarget } from "@/tagging/useTagging";

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
  // Tags added or removed here, with the server's private flag as it was then (it listed them).
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
    touch(tag);
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
              e.stopPropagation(); // never the item's own click, which adds
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

/** The dialog form: `T`, the selection bar's Tag…, a drop (spec §6). Mounted once, in App.
 *
 * Not `CommandDialog`: as generated, it renders its children as they are and puts its sr-only
 * title outside the dialog's content (Task 8), and `PickerBody`'s `<Command>`, which carries
 * `shouldFilter`, `value` and `onKeyDown`, must be the only cmdk root. So the dialog is built from
 * the parts `CommandDialog` uses. Its visible title is its DialogTitle, which names it; the box
 * describes the keys, so the dialog has no description of its own.
 *
 * Radix returns focus only to a Trigger, and this dialog has none, so useReturnFocus gives focus
 * back to what had it (the row or cell `T` was pressed on, the selection bar's button). The last
 * target stays drawn while the dialog animates out. */
export function TagPickerDialog() {
  const tagging = useTaggingContext();
  const returnFocus = useReturnFocus();
  const target = tagging.picker;
  const [shown, setShown] = useState<PickerTarget | null>(target);
  if (target !== null && target !== shown) setShown(target);
  return (
    <Dialog open={target !== null} onOpenChange={(open) => { if (!open) tagging.closePicker(); }}>
      <DialogContent
        className="picker-dialog top-1/3 translate-y-0 overflow-hidden rounded-xl! p-0"
        showCloseButton={false}
        aria-describedby={undefined}
        onEscapeKeyDown={imeEscape}
        {...returnFocus}
      >
        {shown && <PickerBody ids={shown.ids} heading={<DialogTitle className="picker-title">{shown.title}</DialogTitle>} />}
      </DialogContent>
    </Dialog>
  );
}

const inConfirmation = (target: EventTarget | null) => target instanceof Element && target.closest("[role=alertdialog]") !== null;

/** The + after a row's chips, and in the header: the picker for that one recording, as a Popover
 * on the +, whose row stays rendered (spec §6). Clicking it never changes the selection or opens
 * the row. Radix gives focus back to the + when the popover closes. `onOpenChange` hears every
 * open and close, so a virtual list can keep the row drawn while its picker is open (a click
 * doesn't focus a button in Safari or Firefox, or on touch, so focus can't keep it). */
export function AddTagsButton({ id, title, place, onOpenChange }: {
  id: string; title: string; place: "row" | "header"; onOpenChange?: (open: boolean) => void;
}) {
  const tagging = useTaggingContext();
  const headingId = useId();
  const [open, setOpen] = useState(false);
  const ids = useMemo(() => [id], [id]);
  const change = (next: boolean) => {
    setOpen(next);
    onOpenChange?.(next);
  };
  // The confirmation opens over the picker without closing it (spec §6). A focus move into the
  // Alert Dialog is "outside" this Popover, which Radix would close, so it is refused while the
  // confirmation is open or when it lands in one. (A pointer can't reach outside: the modal
  // confirmation turns pointer events off below it.)
  const keepOpen = (e: Event) => {
    if (tagging.confirm !== null || inConfirmation(e.target)) e.preventDefault();
  };
  return (
    <Popover open={open} onOpenChange={change}>
      <PopoverTrigger asChild>
        <button
          type="button"
          className="add-tag"
          data-control="add"
          data-testid="add-tag"
          tabIndex={place === "row" ? -1 : 0}
          aria-label={`Add tags to ${title}`}
          onClick={(e) => e.stopPropagation()} // never a row click: no selection change, nothing opens
        >
          <Plus aria-hidden="true" />
        </button>
      </PopoverTrigger>
      <PopoverContent
        className="picker-popover p-0"
        align="start"
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
