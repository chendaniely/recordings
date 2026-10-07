export type ThemeChoice = "light" | "dark" | "system";
export type Resolved = "light" | "dark";

const KEY = "recordings.theme";
type Storage = { getItem(k: string): string | null; setItem(k: string, v: string): void };

export function resolveTheme(choice: ThemeChoice, prefersDark: boolean): Resolved {
  return choice === "system" ? (prefersDark ? "dark" : "light") : choice;
}

export function loadChoice(storage: Storage | null): ThemeChoice {
  try {
    const v = storage?.getItem(KEY);
    return v === "light" || v === "dark" || v === "system" ? v : "system";
  } catch {
    return "system"; // storage can throw in private windows; the theme must still work
  }
}

export function saveChoice(storage: Storage | null, choice: ThemeChoice): void {
  try {
    storage?.setItem(KEY, choice);
  } catch {
    /* a per-browser convenience only; nothing else depends on it */
  }
}

/** shadcn's convention: the `dark` class on <html>. `light` is set too, so theme.css can tell
 * "the user chose light" from "nothing chosen yet, follow the OS". */
export function applyTheme(root: HTMLElement, resolved: Resolved): void {
  root.classList.toggle("dark", resolved === "dark");
  root.classList.toggle("light", resolved === "light");
}
