import { Lock } from "lucide-react";
import { useState, type DragEvent, type ReactNode } from "react";

import { cn } from "@/lib/utils";

import { filterIsShown } from "../lib/library";
import { useTaggingContext } from "../tagging/useTagging";
import type { Filter, LibraryView, TagCount } from "../types";

type Props = {
  library: LibraryView; filter: Filter; onFilter: (f: Filter) => void;
  onNewTag: (ids: string[] | null) => void; // null: the selection
};

const keyOf = (f: Filter) => (f.kind === "tag" ? `tag:${f.tag}` : f.kind);
const NOTES = "notes/";

/** The filters, and the drop targets for tagging by drag and drop (spec §6). A drop acts only on
 * a drag that started in this page: the IDs come from in-page state set at dragstart, never from
 * dataTransfer, so files, text and other windows' drags are ignored. Any other drag is refused at
 * dragover, so the browser never drops it here, and a drop that comes anyway does nothing.
 *
 * The active tag filter's item stays, at 0, when its tag has lost its last recording (Review
 * Focus 4), in the group the sidebar last listed it in: the browser never works out privacy
 * itself (spec §5). A note type at 0, which the archive has only through an exclusion, is hidden
 * unless it is the active filter (WM6 refinement); the picker still offers it. */
export function Sidebar({ library, filter, onFilter, onNewTag }: Props) {
  const tagging = useTaggingContext();
  const [over, setOver] = useState<string | null>(null);
  const active = filter.kind === "tag" ? filter.tag : null;
  // The active tag as last listed, for its group once it is gone (React's documented pattern for
  // keeping something from an earlier render).
  const listed = active === null ? undefined : library.tags.find((t) => t.tag === active);
  const [last, setLast] = useState<TagCount | null>(null);
  if (listed && (last?.tag !== listed.tag || last.private !== listed.private)) setLast(listed);

  const drop = (target: string, tag: string | null) => {
    const allow = (e: DragEvent<HTMLElement>) => {
      if (!tagging.drag.current) return; // not ours: no drop allowed here
      e.preventDefault();
      e.dataTransfer.dropEffect = "copy";
      setOver(target);
    };
    return {
      onDragEnter: allow,
      onDragOver: allow,
      onDragLeave: (e: DragEvent<HTMLElement>) => {
        if (e.relatedTarget instanceof Node && e.currentTarget.contains(e.relatedTarget)) return; // onto its own text
        setOver((o) => (o === target ? null : o));
      },
      onDrop: (e: DragEvent<HTMLElement>) => {
        setOver(null);
        const ids = tagging.drag.current;
        if (!ids) return; // a foreign drop: ignored
        e.preventDefault();
        tagging.drag.current = null;
        // With tagging off (it went off mid-drag), tag() and the picker say why in a toast.
        if (tag === null) onNewTag(ids);
        else tagging.tag("add", ids, [tag], { kind: "drop" });
      },
    };
  };

  const item = (f: Filter, label: ReactNode, n: number, testid: string) => {
    const key = keyOf(f);
    return (
      <button key={key} type="button" className={cn("si", key === keyOf(filter) && "on", over === key && "drop")}
        data-testid={testid} onClick={() => onFilter(f)} {...(f.kind === "tag" ? drop(key, f.tag) : {})}>
        <span>{label}</span><span className="n">{n}</span>
      </button>
    );
  };

  const open = library.tags.filter((t) => !t.private);
  const hidden = library.tags.filter((t) => t.private);
  const types = library.note_types.filter((n) => n.count > 0 || `${NOTES}${n.note_type}` === active);
  // The active filter's tag lost its last recording: keep its item, at 0, while it applies.
  if (active !== null && !filterIsShown(filter, library)) {
    if (active.startsWith(NOTES)) types.push({ note_type: active.slice(NOTES.length), count: 0 });
    else {
      const wasPrivate = last?.tag === active && last.private;
      (wasPrivate ? hidden : open).push({ tag: active, count: 0, private: wasPrivate });
    }
  }
  const tagItem = (t: TagCount) => item({ kind: "tag", tag: t.tag }, t.tag, t.count, "filter-tag");
  const lockedItem = (t: TagCount) =>
    item({ kind: "tag", tag: t.tag }, <><Lock size={12} className="lock" aria-hidden /> {t.tag}</>, t.count, "filter-tag");
  return (
    <nav className="sidebar" aria-label="Filters">
      {item({ kind: "all" }, "All recordings", library.counts.all, "filter-all")}
      {item({ kind: "untagged" }, "Untagged", library.counts.untagged, "filter-untagged")}
      {library.problems.length > 0 && (
        <div className="problems" role="status">
          {library.problems.length} recording file{library.problems.length > 1 ? "s" : ""} couldn't be read. Run <code>recordings validate</code>.
        </div>
      )}
      <div className="grp">Tags</div>
      {open.map(tagItem)}
      {tagging.canTag && (
        <button type="button" className={cn("si", "new-tag", over === "new" && "drop")} data-testid="new-tag"
          onClick={() => onNewTag(null)} {...drop("new", null)}>
          <span>+ New tag{"…"}</span>
        </button>
      )}
      {hidden.length > 0 && <div className="grp">Private</div>}
      {hidden.map(lockedItem)}
      {types.length > 0 && <div className="grp">Note types</div>}
      {types.map((n) => item({ kind: "tag", tag: `${NOTES}${n.note_type}` }, n.note_type, n.count, "filter-tag"))}
    </nav>
  );
}
