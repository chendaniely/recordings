import "@/index.css";
import "@/theme.css";
import "@/app.css";

import App from "@/App";
import { applyTheme, browserStorage, loadChoice, resolveTheme } from "@/lib/theme";

const { ReactDOM } = (window as unknown as { shinyreact: { ReactDOM: typeof import("react-dom/client") } }).shinyreact;

// Apply the saved theme now, not when ThemeSwitch mounts after Shiny connects, so a saved
// light/dark choice never flashes the OS theme first.
applyTheme(document.documentElement, resolveTheme(loadChoice(browserStorage()), window.matchMedia("(prefers-color-scheme: dark)").matches));

ReactDOM.createRoot(document.body.appendChild(document.createElement("div"))).render(<App />);
