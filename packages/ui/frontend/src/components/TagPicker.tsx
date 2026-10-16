import { Lock, Plus, X } from "lucide-react";
import { useId, useMemo, useState, type KeyboardEvent, type MouseEvent } from "react";

import { Command, CommandEmpty, CommandGroup, CommandInput, CommandItem, CommandList } from "@/components/ui/command";
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { Popover, PopoverContent, PopoverTrigger } from "@/components/ui/popover";
import { composing } from "@/lib/keys";
import { shownTags } from "@/lib/library";
import {
  alreadyWords, createOffer, createRefusal, pickerGroups, pickerTags, stateWords, tagStates, type PickerTag, type TagState,
} from "@/lib/picker";
import { useReturnFocus } from "@/tagging/useReturnFocus";
import { useTaggingContext, type PickerTarget } from "@/tagging/useTagging";

export function pickerTitle(ids: readonly string[], titleOf: (id: string) => string): string {
  return ids.length === 1 ? `Tag ${titleOf(ids[0])}` : `Tag ${ids.length.toLocaleString("en")} recordings`;
}

const TAG = "tag:";
const CREATE = "create:";
const GLYPH: Record<TagState["state"], string> = { all: "✓", some: "–", none: "" };

/** A click in the list keeps focus in the box, so typing goes on there (and a touch keeps the
 * on-screen keyboard up). */
const keepFocus = (e: MouseEvent) => e.preventDefault();

/** The picker's body, shared by both forms (spec §6).
 * - Enter only ever adds: the highlighted tag, then the box clears and the picker stays open. On
 *   a ✓ item it says "Already on all 12" and sends nothing.
 * - Shift+Enter creates exactly the typed text (trimmed, NFC), when Create is offered; otherwise
 *   it says why not.
 * - Shift+Delete, or Cmd/Ctrl+Backspace, removes the highlighted ✓ or – tag from the targets;
 *   the × on the item does the same with the mouse.
 * - Esc closes the topmost layer only (Radix): the confirmation over the picker, then the picker.
 * States are in words as well as glyphs, counting pending changes at once. Private tags are
 * grouped under a lock by the server's flags (pickerTags reads them from the view's tags, as
 * tagging.isPrivate does); the browser never applies the rule itself. */
export function PickerBody({ ids, title }: { ids: string[]; title: string }) {
  const tagging = useTaggingContext();
  const { view, sessionTags, pending } = tagging;
  const keysId = useId();
  const [query, setQuery] = useState("");
  const [value, setValue] = useState("");
  const [note, setNote] = useState("");

  const all = useMemo(() => (view ? pickerTags(view, sessionTags) : []), [view, sessionTags]);
  // The targets' tags as they will be: a pending add counts at once, a pending removal is gone
  // (spec §6), so a quick second Enter on a tag just added says "Already…" and sends nothing.
  const stateOf = useMemo(() => {
    const wanted = new Set(ids);
    const rows = view ? view.recordings.filter((r) => wanted.has(r.id)) : [];
    return tagStates(rows.map((r) => ({ tags: shownTags(r, pending).filter((t) => t.pending !== "remove") })));
  }, [view, ids, pending]);

  const groups = pickerGroups(all, query);
  const create = createOffer(all, query);
  // cmdk highlights the first item of each new list (React renders the input, and cmdk's
  // re-highlight, before the next key), and reports each highlight here.
  const highlighted = value.startsWith(TAG) ? value.slice(TAG.length) : null;

  const search = (q: string) => {
    setQuery(q);
    setNote("");
  };
  const add = (tag: string) => {
    const state = stateOf(tag);
    if (state.state === "all") {
      setNote(alreadyWords(state));
      return;
    }
    tagging.tag("add", ids, [tag], { kind: "picker" });
    search("");
  };
  const remove = (tag: string) => {
    if (stateOf(tag).state === "none") return;
    tagging.tag("remove", ids, [tag], { kind: "picker" });
    setNote("");
  };

  // Runs before cmdk's own keys; cmdk skips an event this prevents, and any key while composing.
  const onKeyDown = (e: KeyboardEvent<HTMLDivElement>) => {
    if (composing(e)) return; // an IME's Enter or Backspace is the IME's
    if (e.key === "Enter" && e.shiftKey) {
      e.preventDefault(); // so cmdk never adds the highlighted tag instead
      if (create) add(create);
      else setNote(createRefusal(all, query) ?? "");
      return;
    }
    const removing = (e.key === "Delete" && e.shiftKey) || (e.key === "Backspace" && (e.metaKey || e.ctrlKey));
    if (removing && highlighted !== null && stateOf(highlighted).state !== "none") {
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

  const empty = query.trim() ? (createRefusal(all, query) ?? "No tag matches.") : "No tags yet: type a name to create one.";
  return (
    <Command
      shouldFilter={false}
      value={value}
      onValueChange={setValue}
      onKeyDown={onKeyDown}
      label={title}
      className="picker"
    >
      <div className="picker-title" aria-hidden="true">{title}</div>
      <CommandInput
        value={query}
        onValueChange={search}
        placeholder="Find or create a tag"
        aria-describedby={keysId}
        data-testid="picker-input"
      />
      <CommandList label="Tags">
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
      <p className="picker-keys" id={keysId}>Enter add · Shift+Enter create · Shift+Delete remove · Esc close</p>
    </Command>
  );
}

/** The dialog form: `T`, the selection bar's Tag…, a drop (spec §6). Mounted once, in App.
 *
 * Not `CommandDialog`: as generated, it renders its children as they are and puts its sr-only
 * title outside the dialog's content (Task 8), and `PickerBody`'s `<Command>`, which carries
 * `shouldFilter`, `value` and `onKeyDown`, must be the only cmdk root. So the dialog is built from
 * the parts `CommandDialog` uses, with its own title and description inside the content, for
 * screen readers only: they name the dialog, and go with it when it closes.
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
        {...returnFocus}
      >
        <DialogHeader className="sr-only">
          <DialogTitle>{shown?.title ?? "Tag"}</DialogTitle>
          <DialogDescription>Find, add, create or remove tags.</DialogDescription>
        </DialogHeader>
        {shown && <PickerBody ids={shown.ids} title={shown.title} />}
      </DialogContent>
    </Dialog>
  );
}

const inConfirmation = (target: EventTarget | null) => target instanceof Element && target.closest("[role=alertdialog]") !== null;

/** The + after a row's chips, and in the header: the picker for that one recording, as a Popover
 * on the +, whose row stays rendered (spec §6). Clicking it never changes the selection or opens
 * the row. Radix gives focus back to the + when the popover closes. */
export function AddTagsButton({ id, title, place }: { id: string; title: string; place: "row" | "header" }) {
  const tagging = useTaggingContext();
  const [open, setOpen] = useState(false);
  const ids = useMemo(() => [id], [id]);
  // The confirmation opens over the picker without closing it (spec §6). A focus move into the
  // Alert Dialog is "outside" this Popover, which Radix would close, so it is refused while the
  // confirmation is open or when it lands in one. (A pointer can't reach outside: the modal
  // confirmation turns pointer events off below it.)
  const keepOpen = (e: Event) => {
    if (tagging.confirm !== null || inConfirmation(e.target)) e.preventDefault();
  };
  return (
    <Popover open={open} onOpenChange={setOpen}>
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
        aria-label={`Tag ${title}`}
        onFocusOutside={keepOpen}
        onInteractOutside={keepOpen}
      >
        <PickerBody ids={ids} title={`Tag ${title}`} />
      </PopoverContent>
    </Popover>
  );
}
