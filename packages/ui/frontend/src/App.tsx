import { useEffect, useMemo, useState } from "react";

import { RecordingList } from "./components/RecordingList";
import { RecordingPane } from "./components/RecordingPane";
import { Sidebar } from "./components/Sidebar";
import { StatusPage } from "./components/StatusPage";
import { TopBar } from "./components/TopBar";
import { hashFor, pageFromHash } from "./lib/nav";
import { filterRows } from "./lib/transcript";
import { useShinyInitialized, useShinyInput, useShinyOutputValue } from "./sr";
import type { Filter, LibraryView, Page } from "./types";

export default function App() {
  const initialized = useShinyInitialized();
  const library = useShinyOutputValue<LibraryView>("library");
  const [selectedId, setSelectedId] = useShinyInput<string | null>("selected_id", null);
  const [filter, setFilter] = useState<Filter>({ kind: "all" });
  const [page, setPage] = useState<Page>(() => pageFromHash(window.location.hash));
  const rows = useMemo(() => (library ? filterRows(library.recordings, filter) : []), [library, filter]);

  // Hash routes: Back returns to the page you came from, and the server never logs them.
  useEffect(() => {
    const onHash = () => setPage(pageFromHash(window.location.hash));
    window.addEventListener("hashchange", onHash);
    return () => window.removeEventListener("hashchange", onHash);
  }, []);
  const go = (next: Page) => {
    window.location.hash = hashFor(next);
    setPage(next);
  };

  if (!initialized) return null; // no flash of empty defaults during connection
  return (
    <div className="app">
      <TopBar page={page} onPage={go} />
      {page === "status" ? <StatusPage /> : library ? (
        <main className="panes">
          <Sidebar library={library} filter={filter} onFilter={setFilter} />
          <RecordingList rows={rows} selectedId={selectedId} onSelect={setSelectedId} />
          <RecordingPane selectedId={selectedId} />
        </main>
      ) : <p className="empty">Loading…</p>}
    </div>
  );
}
