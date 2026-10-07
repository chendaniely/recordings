import "@/index.css";
import "@/theme.css";
import "@/app.css";

import App from "@/App";

const { ReactDOM } = (window as unknown as { shinyreact: { ReactDOM: typeof import("react-dom/client") } }).shinyreact;

ReactDOM.createRoot(document.body.appendChild(document.createElement("div"))).render(<App />);
