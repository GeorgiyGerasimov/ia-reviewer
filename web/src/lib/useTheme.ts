// Theme controller. Reads the initial value the boot script wrote
// onto `<html data-theme>` BEFORE stylesheet parsing (no FOUC),
// then owns the subsequent flips. Persists to localStorage so the
// boot script restores the choice on the next load.
//
// The boot script + this hook + the `[data-theme="dark"]` selector
// in `globals.css` are the three pieces of the theme story:
//   * boot script  (index.html)     → first-paint decision
//   * useTheme     (this file)      → runtime flips
//   * globals.css  ([data-theme])   → which colors to render
//
// Tests in `useTheme.test.ts` lock the contract.

import { useCallback, useState } from "react";

export type Theme = "light" | "dark";

const STORAGE_KEY = "ia-reviewer-theme";

function readCurrentTheme(): Theme {
  // Light is the absence of the attribute — the boot script only
  // sets it for dark. Match that convention.
  return document.documentElement.getAttribute("data-theme") === "dark"
    ? "dark"
    : "light";
}

function persistTheme(theme: Theme): void {
  // Wrap in try/catch — private browsing can make localStorage
  // throw on access. The toggle should still work in-memory;
  // only the across-reload persistence is lost.
  try {
    window.localStorage.setItem(STORAGE_KEY, theme);
  } catch {
    // intentional no-op
  }
}

function applyToDom(theme: Theme): void {
  if (theme === "dark") {
    document.documentElement.setAttribute("data-theme", "dark");
  } else {
    // Light = attribute absent. Removing it (rather than setting
    // it to "light") keeps the default :root selector path active
    // and avoids accidentally tripping any future `[data-theme=light]`
    // override block.
    document.documentElement.removeAttribute("data-theme");
  }
}

export interface UseThemeResult {
  theme: Theme;
  toggleTheme: () => void;
  setTheme: (theme: Theme) => void;
}

export function useTheme(): UseThemeResult {
  const [theme, setThemeState] = useState<Theme>(() => readCurrentTheme());

  const setTheme = useCallback((next: Theme) => {
    setThemeState(next);
    applyToDom(next);
    persistTheme(next);
  }, []);

  const toggleTheme = useCallback(() => {
    // Read from current React state rather than re-querying the
    // DOM — they're kept in sync by setTheme, and trusting state
    // means cross-tab manipulation by extensions doesn't race
    // with our toggle clicks.
    setThemeState((prev) => {
      const next: Theme = prev === "dark" ? "light" : "dark";
      applyToDom(next);
      persistTheme(next);
      return next;
    });
  }, []);

  return { theme, toggleTheme, setTheme };
}
