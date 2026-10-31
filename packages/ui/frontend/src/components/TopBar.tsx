import type { Page } from "../types";
import { ThemeSwitch } from "./ThemeSwitch";

/** "Library · Tags · Add · Status" (2b spec §15.1): Add, the Sync screen, until stage 3b. */
const PAGES: { page: Page; label: string }[] = [
  { page: "library", label: "Library" }, { page: "tags", label: "Tags" }, { page: "add", label: "Add" },
  { page: "status", label: "Status" },
];

/** At 320 pixels the four tabs stay one row: the logo keeps its mark but not its name, and the
 * theme switch is one icon (app.css, the phone block). */
export function TopBar({ page, onPage }: { page: Page; onPage: (p: Page) => void }) {
  return (
    <header className="topbar">
      <span className="logo"><i aria-hidden /><span className="logo-name">Recordings</span></span>
      <nav aria-label="Pages">
        {PAGES.map((p) => (
          <button key={p.page} type="button" className={`nav${page === p.page ? " on" : ""}`}
            aria-current={page === p.page ? "page" : undefined} onClick={() => onPage(p.page)}>
            {p.label}
          </button>
        ))}
      </nav>
      <ThemeSwitch />
    </header>
  );
}
