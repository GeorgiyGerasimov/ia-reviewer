// Pill-style theme toggle in the header. Accessible name describes
// what clicking will DO ("Switch to dark") — operators read the
// label as the next state, matching macOS / GitHub convention.

import { useTheme } from "../lib/useTheme";

export function ThemeToggle() {
  const { theme, toggleTheme } = useTheme();
  const nextTheme = theme === "dark" ? "light" : "dark";

  return (
    <button
      type="button"
      onClick={toggleTheme}
      aria-label={`Switch to ${nextTheme} theme`}
      title={`Switch to ${nextTheme} theme`}
      className={
        "inline-flex items-center gap-1.5 px-3 py-1.5 rounded-full " +
        "border border-border bg-card text-foreground text-xs font-medium " +
        "hover:bg-accent transition-colors " +
        "focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring"
      }
    >
      <span aria-hidden className="text-sm leading-none">
        {theme === "dark" ? "🌙" : "☀️"}
      </span>
      <span className="capitalize">{theme}</span>
    </button>
  );
}
