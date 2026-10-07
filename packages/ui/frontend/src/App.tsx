import { useMemo, useState } from "react";

import { RecordingList } from "./components/RecordingList";
import { RecordingPane } from "./components/RecordingPane";
import { Sidebar } from "./components/Sidebar";
import { TopBar } from "./components/TopBar";
import { filterRows } from "./lib/transcript";
import { useShinyInitialized, useShinyInput, useShinyOutputValue } from "./sr";
import type { Filter, LibraryView } from "./types";

export default function App() {
  const initialized = useShinyInitialized();
  const library = useShinyOutputValue<LibraryView>("library");
  const [selectedId, setSelectedId] = useShinyInput<string | null>("selected_id", null);
  const [filter, setFilter] = useState<Filter>({ kind: "all" });
  const rows = useMemo(() => (library ? filterRows(library.recordings, filter) : []), [library, filter]);

  if (!initialized) return null; // no flash of empty defaults during connection
  return (
    <div className="app">
      <TopBar />
      {library ? (
        <main className="panes">
          <Sidebar library={library} filter={filter} onFilter={setFilter} />
          <RecordingList rows={rows} selectedId={selectedId} onSelect={setSelectedId} />
          <RecordingPane selectedId={selectedId} />
        </main>
      ) : <p className="empty">Loading…</p>}
    </div>
  );
}
