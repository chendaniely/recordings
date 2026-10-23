import { useEffect, useMemo, useState } from "react";

import { Toaster } from "@/components/ui/sonner";

import { RecordingList, openNewTag } from "./components/RecordingList";
import { RecordingPane } from "./components/RecordingPane";
import { Sidebar, useListedFilter } from "./components/Sidebar";
import { StatusPage } from "./components/StatusPage";
import { TagPickerDialog } from "./components/TagPicker";
import { TagsPage } from "./components/TagsPage";
import { TopBar } from "./components/TopBar";
import { hashFor, phoneRoute, routeFromHash } from "./lib/nav";
import { useGridState } from "./lib/useGridState";
import { UNTAGGED, useListState } from "./lib/useListState";
import { PHONE, useMediaQuery } from "./lib/useMediaQuery";
import { useShinyInitialized, useShinyInput, useShinyOutputStatus, useShinyOutputValue } from "./sr";
import { ConfirmDialog } from "./tagging/ConfirmDialog";
import { TaggingContext, useTagging } from "./tagging/useTagging";
import type { Filter, LibraryView, Page, Route } from "./types";

/** Sonner's offsets take a CSS length: the toaster and the pickers' band read the same variable. */
const TOAST_OFFSET = "var(--toast-offset)";

export default function App() {
  const initialized = useShinyInitialized();
  const library = useShinyOutputValue<LibraryView>("library");
  // Every page's output lives as long as the app. shinyreact 0.1.0 removes an unmounted output's
  // element before Shiny unbinds it, so Shiny never does, and each later value for that output
  // logs "Output <id> not found". Held here, no output is ever unmounted. A new output gets a
  // line here too (docs/upstream-issues.md). Message handlers live here too, in useTagging:
  // shinyreact drops a message that has no handler.
  useShinyOutputStatus("recording");
  useShinyOutputStatus("status");
  const tagging = useTagging(library);
  const [selectedId, setSelectedId] = useShinyInput<string | null>("selected_id", null);
  const [filter, setFilter] = useState<Filter>({ kind: "all" });
  const [asked, setRoute] = useState<Route>(() => routeFromHash(window.location.hash));
  // On a phone the Tags tab is the Library filtered to Untagged, so a Tags route shows the Library
  // at once; the effect below moves the address and the filter to match (spec §6).
  const phone = useMediaQuery(PHONE);
  const route = phoneRoute(asked, phone);
  const view = tagging.view;
  // The order, its rows and the selection (with its pruning and announcement).
  const { order, rows, sel, act, announce, fitted } = useListState(tagging, filter);
  const openRow = useMemo(() => view?.recordings.find((r) => r.id === selectedId) ?? null, [view, selectedId]);
  // Held here, not in the sidebar, which unmounts on the Status page while the filter stays.
  const lastListed = useListedFilter(filter, view);
  // The Tags grid's filters, sort, selection and columns, held here so they survive a visit to
  // another page (Task 14 fix round 1), as the Library's do.
  const grid = useGridState(tagging);
  // Toasts come in at the top on touch screens and in windows up to 1,000 px wide: at the bottom
  // they covered the selection bar, its Clear included, and a phone's sheet (spec §6; Task 15 fix
  // round 1, item 4). A touch tablet has no Esc to clear with instead.
  const topToasts = useMediaQuery("(hover: none), (max-width: 1000px)");

  // Hash routes: Back returns to the page you came from, and the server never logs them. The hash
  // only chooses what to show: `#/library/<id>` opens that recording, read-only (spec §7, §15).
  useEffect(() => {
    const onHash = () => setRoute(routeFromHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  useEffect(() => {
    if (initialized && route.open) setSelectedId(route.open);
  }, [initialized, route.open, setSelectedId]);
  // The Tags tab on a phone, by the tab, by a `#/tags` address or by turning a tablet onto one: the
  // Library with the Untagged filter. The address is replaced, not added to, so it, the top bar's
  // current tab and Back all agree. The filter is App's, so the Library tab shows it next too.
  useEffect(() => {
    if (phone && asked.page === "tags") {
      setFilter(UNTAGGED);
      window.location.replace(hashFor("library"));
      setRoute({ page: "library", open: null });
    }
  }, [phone, asked.page]);
  const go = (next: Page) => {
    if (phone && next === "tags") {
      setFilter(UNTAGGED);
      next = "library";
    }
    window.location.hash = hashFor(next);
    setRoute({ page: next, open: null });
  };

  if (!initialized) return null; // no flash of empty defaults during connection
  return (
    <TaggingContext.Provider value={tagging}>
      <div className="app">
        <TopBar page={route.page} onPage={go} />
        {/* The Tags grid reads the same merged library, with its own order and selection (spec §7). */}
        {route.page === "status" ? <StatusPage /> : !view ? <p className="empty">Loading…</p> : route.page === "tags" ? <TagsPage grid={grid} /> : (
          <main className="panes">
            {/* + New tag…: a drop opens the picker for the dragged recordings, a click for the
                selection (spec §6). */}
            <Sidebar library={view} filter={filter} onFilter={setFilter} lastListed={lastListed}
              onNewTag={(ids) => openNewTag(ids, sel, order.ids, rows, tagging)} />
            <RecordingList rows={rows} sel={sel} act={act} openId={selectedId} onOpen={setSelectedId} announce={announce} fitted={fitted} />
            <RecordingPane selectedId={selectedId} row={openRow} />
          </main>
        )}
        <TagPickerDialog />
        <ConfirmDialog />
        {/* One offset at every width, from app.css, where the pickers keep clear of the toasts' band;
            Sonner's own are 24 px, and 16 px only up to 600 px wide (Task 15 fix round 2, item 2). */}
        <Toaster position={topToasts ? "top-center" : "bottom-right"} offset={TOAST_OFFSET} mobileOffset={TOAST_OFFSET} />
      </div>
    </TaggingContext.Provider>
  );
}
