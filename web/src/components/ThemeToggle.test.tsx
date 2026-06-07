// Pill-style theme toggle. Renders the current state as a chip
// (sun icon + "Light" / moon + "Dark") and flips on click via
// the `useTheme` hook.

import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { ThemeToggle } from "./ThemeToggle";

beforeEach(() => {
  document.documentElement.removeAttribute("data-theme");
  window.localStorage.clear();
});

afterEach(() => {
  document.documentElement.removeAttribute("data-theme");
  window.localStorage.clear();
});

describe("<ThemeToggle />", () => {
  it("renders an accessible button reflecting the current theme", () => {
    render(<ThemeToggle />);
    // Default is light → label says "Dark" (next state) so the
    // user reads what clicking will DO. Matches macOS / GitHub
    // convention.
    const btn = screen.getByRole("button", { name: /switch to dark/i });
    expect(btn).toBeInTheDocument();
  });

  it("flips the theme on click and updates its own label", async () => {
    render(<ThemeToggle />);
    const btn = screen.getByRole("button", { name: /switch to dark/i });
    await userEvent.click(btn);

    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
    // Label flips to "switch to light" now that dark is active.
    expect(screen.getByRole("button", { name: /switch to light/i })).toBeInTheDocument();
  });

  it("toggles back on a second click", async () => {
    render(<ThemeToggle />);
    const btn = screen.getByRole("button");
    await userEvent.click(btn);
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");

    await userEvent.click(screen.getByRole("button"));
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });

  it("respects the initial theme from <html data-theme>", () => {
    // Operator reloaded the page while in dark mode — the boot
    // script already set the attribute; we should respect it.
    document.documentElement.setAttribute("data-theme", "dark");
    render(<ThemeToggle />);
    expect(
      screen.getByRole("button", { name: /switch to light/i }),
    ).toBeInTheDocument();
  });

  it("persists the new theme to localStorage", async () => {
    render(<ThemeToggle />);
    await userEvent.click(screen.getByRole("button"));
    expect(window.localStorage.getItem("ia-reviewer-theme")).toBe("dark");
  });
});
