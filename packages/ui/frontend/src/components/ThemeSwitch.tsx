import { Monitor, Moon, Sun } from "lucide-react";
import { useEffect, useState } from "react";

import { ToggleGroup, ToggleGroupItem } from "@/components/ui/toggle-group";
import { applyTheme, browserStorage, loadChoice, resolveTheme, saveChoice, type ThemeChoice } from "@/lib/theme";

export function ThemeSwitch() {
  const [choice, setChoice] = useState<ThemeChoice>(() => loadChoice(browserStorage()));

  useEffect(() => {
    const media = window.matchMedia("(prefers-color-scheme: dark)");
    const sync = () => applyTheme(document.documentElement, resolveTheme(choice, media.matches));
    sync();
    media.addEventListener("change", sync);
    return () => media.removeEventListener("change", sync);
  }, [choice]);

  return (
    <ToggleGroup
      type="single"
      size="sm"
      variant="outline"
      className="ml-auto"
      value={choice}
      aria-label="Colour theme"
      onValueChange={(v) => {
        if (!v) return; // Radix sends "" when the active item is clicked again
        setChoice(v as ThemeChoice);
        saveChoice(browserStorage(), v as ThemeChoice);
      }}
    >
      <ToggleGroupItem value="light" aria-label="Light" data-testid="theme-light"><Sun /></ToggleGroupItem>
      <ToggleGroupItem value="system" aria-label="System" data-testid="theme-system"><Monitor /></ToggleGroupItem>
      <ToggleGroupItem value="dark" aria-label="Dark" data-testid="theme-dark"><Moon /></ToggleGroupItem>
    </ToggleGroup>
  );
}
