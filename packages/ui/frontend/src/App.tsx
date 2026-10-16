import { useEffect, useMemo, useState } from "react";

import { Toaster } from "@/components/ui/sonner";

import { RecordingList, openNewTag } from "./components/RecordingList";
import { RecordingPane } from "./components/RecordingPane";
import { Sidebar, useListedFilter } from "./components/Sidebar";
import { StatusPage } from "./components/StatusPage";
import { TagPickerDialog } from "./components/TagPicker";
import { TagsPage } from "./components/TagsPage";
import { TopBar } from "./components/TopBar";
import { hashFor, routeFromHash } from "./lib/nav";
import { useGridState } from "./lib/useGridState";
import { UNTAGGED, useListState } from "./lib/useListState";
import { useMediaQuery } from "./lib/useMediaQuery";
import { useShinyInitialized, useShinyInput, useShinyOutputStatus, useShinyOutputValue } from "./sr";
import { ConfirmDialog } from "./tagging/ConfirmDialog";
import { TaggingContext, useTagging } from "./tagging/useTagging";
import type { Filter, LibraryView, Page, Route } from "./types";

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
  const [route, setRoute] = useState<Route>(() => routeFromHash(window.location.hash));
  const view = tagging.view;
  // The order, its rows and the selection (with its pruning and announcement), from the hook the
  // phone's Untagged list shares (Task 15).
  const { order, rows, sel, act, announce, fitted } = useListState(tagging, filter);
  const openRow = useMemo(() => view?.recordings.find((r) => r.id === selectedId) ?? null, [view, selectedId]);
  // Held here, not in the sidebar, which unmounts on the Status page while the filter stays.
  const lastListed = useListedFilter(filter, view);
  // The Tags grid's filters, sort, selection and columns, held here so they survive a visit to
  // another page (Task 14 fix round 1), as the Library's do.
  const grid = useGridState(tagging);
  // On a phone the Tags page is the Untagged list instead (spec §6): its state is held here too.
  const untagged = useListState(tagging, UNTAGGED);
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
  const go = (next: Page) => {
    window.location.hash = hashFor(next);
    setRoute({ page: next, open: null });
  };

  if (!initialized) return null; // no flash of empty defaults during connection
  return (
    <TaggingContext.Provider value={tagging}>
      <div className="app">
        <TopBar page={route.page} onPage={go} />
        {/* The Tags grid reads the same merged library, with its own order and selection (spec §7). */}
        {route.page === "status" ? <StatusPage /> : !view ? <p className="empty">Loading…</p> : route.page === "tags" ? <TagsPage grid={grid} untagged={untagged} /> : (
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
        <Toaster position={topToasts ? "top-center" : "bottom-right"} />
      </div>
    </TaggingContext.Provider>
  );
}
