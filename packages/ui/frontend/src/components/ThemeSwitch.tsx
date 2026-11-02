import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { applyTheme, browserStorage, loadChoice, resolveTheme, saveChoice, type ThemeChoice } from "@/lib/theme";

const NEXT: Record<ThemeChoice, ThemeChoice> = { light: "system", system: "dark", dark: "light" };
const NAMES: Record<ThemeChoice, string> = { light: "Light", system: "System", dark: "Dark" };
const ICONS = { light: Sun, system: Monitor, dark: Moon };

/** Three choices; at 320 pixels one icon that steps through them (2b spec §15.1). Both are drawn,
 * and app.css shows one (`.theme-full`, `.theme-icon`), so the hidden one is out of the
 * accessibility tree. */
export function ThemeSwitch() {
  const [choice, setChoice] = useState<ThemeChoice>(() => loadChoice(browserStorage()));

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const sync = () => applyTheme(document.documentElement, resolveTheme(choice, media.matches));
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, [choice]);

  const pick = (v: ThemeChoice) => {
    setChoice(v);
    saveChoice(browserStorage(), v);
  };
  const Icon = ICONS[choice];
  return (
    <>
      <ToggleGroup
        type="single"
        size="sm"
        variant="outline"
        className="ml-auto theme-full"
        value={choice}
        aria-label="Colour theme"
        onValueChange={(v) => {
          if (!v) return; // Radix sends "" when the active item is clicked again
          pick(v as ThemeChoice);
        }}
      >
        <ToggleGroupItem value="light" aria-label="Light" data-testid="theme-light"><Sun /></ToggleGroupItem>
        <ToggleGroupItem value="system" aria-label="System" data-testid="theme-system"><Monitor /></ToggleGroupItem>
        <ToggleGroupItem value="dark" aria-label="Dark" data-testid="theme-dark"><Moon /></ToggleGroupItem>
      </ToggleGroup>
      <button type="button" className="theme-icon" data-testid="theme-cycle"
        aria-label={`Colour theme: ${NAMES[choice]}. Change to ${NAMES[NEXT[choice]]}`} onClick={() => pick(NEXT[choice])}>
        <Icon aria-hidden="true" />
      </button>
    </>
  );
}
