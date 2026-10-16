import { useEffect, useMemo, useState } from "react";

import { Toaster } from "@/components/ui/sonner";

import { RecordingList } from "./components/RecordingList";
import { RecordingPane } from "./components/RecordingPane";
import { Sidebar } from "./components/Sidebar";
import { StatusPage } from "./components/StatusPage";
import { TagPickerDialog } from "./components/TagPicker";
import { TopBar } from "./components/TopBar";
import { hashFor, routeFromHash } from "./lib/nav";
import { useOrderedRows, useRowOrder } from "./lib/useRowOrder";
import { useSelection } from "./lib/useSelection";
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
  // The order, its rows and the selection (with its pruning and announcement) come from the
  // hooks the phone's Untagged list shares (Task 15).
  const order = useRowOrder(view, filter, tagging.loadKey, tagging.revealed);
  const { sel, act, announce } = useSelection(order);
  const rows = useOrderedRows(order.ids, view);
  const openRow = useMemo(() => view?.recordings.find((r) => r.id === selectedId) ?? null, [view, selectedId]);

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
        {route.page === "status" ? <StatusPage /> : view ? (
          <main className="panes">
            <Sidebar library={view} filter={filter} onFilter={setFilter} />
            <RecordingList rows={rows} sel={sel} act={act} openId={selectedId} onOpen={setSelectedId} announce={announce} />
            <RecordingPane selectedId={selectedId} row={openRow} />
          </main>
        ) : <p className="empty">Loading…</p>}
        <TagPickerDialog />
        <ConfirmDialog />
        <Toaster position="bottom-right" />
      </div>
    </TaggingContext.Provider>
  );
}
