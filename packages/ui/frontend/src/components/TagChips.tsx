import { X } from "lucide-react";
import type { ReactNode } from "react";

import type { ShownTag } from "@/lib/library";
import { cn } from "@/lib/utils";

/** After × removes a chip, focus moves to the next control in the row: the next ×, or the + (spec
 * §6). Controls carry data-control; their container carries data-controls. */
export function nextControl(button: HTMLElement): HTMLElement | null {
  const controls = [...(button.closest("[data-controls]")?.querySelectorAll<HTMLElement>("[data-control]") ?? [])];
  const at = controls.indexOf(button);
  return controls[at + 1] ?? controls[at - 1] ?? null;
}

/** A recording's chips, each with an × that removes that tag from that recording (spec §6). In a
 * row, the controls are reached with Left and Right (tabindex -1); in the header, with Tab. A
 * pending chip shows its new state at once, marked pending. A tag's text is isolated left to
 * right (<bdi dir="ltr">), so a right-to-left name can't reorder what's around it, and truncated
 * when long; the full tag is in the chip's title and the ×'s name (Review Focus 5). */
export function TagChips({ tags, title, canTag, place, onRemove, children }: {
  tags: ShownTag[]; title: string; canTag: boolean; place: "row" | "header";
  onRemove: (tag: string, next: HTMLElement | null) => void; children?: ReactNode;
}) {
  return (
    <div className="chips" data-testid="chips" data-controls="">
      {tags.map((t) => (
        <span
          key={t.tag}
          className={cn("chip", t.by === "auto" && "auto", t.tag.startsWith("notes/") && "nt", t.pending && "pending", t.pending === "remove" && "pending-remove")}
          data-testid="chip"
          data-tag={t.tag}
          title={t.tag}
        >
          <span className="chip-label">
            <bdi dir="ltr">{t.tag}</bdi>
            {t.by === "auto" ? " · auto" : ""}
          </span>
          {t.pending && <span className="sr-only">{t.pending === "add" ? " (adding)" : " (removing)"}</span>}
          {canTag && !t.pending && (
            <button
              type="button"
              className="chip-x"
              data-control="remove"
              tabIndex={place === "row" ? -1 : 0}
              aria-label={`Remove tag ${t.tag} from ${title}`}
              onClick={(e) => {
                e.stopPropagation(); // never a row click: no selection change, nothing opens
                onRemove(t.tag, nextControl(e.currentTarget));
              }}
            >
              <X aria-hidden="true" />
            </button>
          )}
        </span>
      ))}
      {children}
    </div>
  );
}
