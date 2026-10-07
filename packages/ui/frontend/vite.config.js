import path from "node:path";
import { fileURLToPath } from "node:url";

import tailwindcss from "@tailwindcss/vite";
import react from "@vitejs/plugin-react";
import { defineConfig } from "vite";

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// React/ReactDOM are externalized and read from window.shinyreact at runtime, so this bundle
// (shadcn's components included) shares the React instance that owns the shinyreact hooks.
// Two copies = hooks silently empty.
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
      external: ["react", "react-dom", "react-dom/client"],
      output: {
        assetFileNames: "ui.[ext]",
        globals: {
          react: "window.shinyreact.React",
          "react-dom": "window.shinyreact.ReactDOM",
          "react-dom/client": "window.shinyreact.ReactDOM",
        },
      },
    },
  },
  test: { environment: "jsdom", include: ["src/**/*.test.ts"] },
});
