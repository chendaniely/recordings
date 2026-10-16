import path from "node:path";
import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// React stays external: `react` and `react-dom/client` come from window.shinyreact, so this bundle
// (shadcn's components included) shares the React instance that owns the shinyreact hooks. Two
// copies = hooks silently empty. The `react-dom` INDEX module is bundled: shinyreact's ReactDOM
// global is react-dom/client only, without createPortal or flushSync, which Radix, Sonner and
// TanStack Virtual import (tagging spec §6; docs/upstream-issues.md). That module requires
// `react`, which stays external, so there is still one React.
export const EXTERNAL = ["react", "react-dom/client"];
export const GLOBALS = {
  react: "window.shinyreact.React",
  "react-dom/client": "window.shinyreact.ReactDOM",
};

export default defineConfig({
  define: { "process.env.NODE_ENV": JSON.stringify("production") },
  plugins: [react(), tailwindcss()],
  resolve: { alias: { "@": path.resolve(__dirname, "src") } },
  build: {
    // Next to shiny_app.py, where ReactApp discovers www/ui.js and www/ui.css.
    outDir: path.resolve(__dirname, "../src/recordings_ui/www"),
    emptyOutDir: false, // www/ also holds the committed fonts
    cssCodeSplit: false,
    lib: {
      entry: path.resolve(__dirname, "src/ui.tsx"),
      formats: ["iife"],
      name: "RecordingsUI",
      fileName: () => "ui.js",
    },
    rollupOptions: {
      external: EXTERNAL,
      output: { assetFileNames: "ui.[ext]", globals: GLOBALS },
    },
  },
  test: { environment: "jsdom", include: ["src/**/*.test.ts"] },
});
