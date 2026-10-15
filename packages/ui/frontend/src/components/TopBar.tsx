import type { Page } from "../types";
import { ThemeSwitch } from "./ThemeSwitch";

const PAGES: { page: Page; label: string }[] = [{ page: "library", label: "Library" }, { page: "status", label: "Status" }];

export function TopBar({ page, onPage }: { page: Page; onPage: (p: Page) => void }) {
  return (
    <header className="topbar">
      <span className="logo"><i aria-hidden />Recordings</span>
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
