import { TopBar } from "./components/TopBar";
import { useShinyInitialized, useShinyOutputValue } from "./sr";
import type { LibraryView } from "./types";

export default function App() {
  const initialized = useShinyInitialized();
  const library = useShinyOutputValue<LibraryView>("library");
  if (!initialized) return null; // no flash of empty defaults during connection
  return (
    <div className="app">
      <TopBar />
      <main className="panes">
        {library ? <p className="muted">{library.counts.all} recordings</p> : <p className="muted">Loading…</p>}
      </main>
    </div>
  );
}
