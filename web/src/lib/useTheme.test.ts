// Theme toggling: read the initial value the boot script wrote
// onto <html data-theme>, expose a `toggleTheme()` that flips it
// + persists the new choice to localStorage. The boot script
// (index.html, runs BEFORE stylesheet parsing — no FOUC) is the
// single source for the FIRST paint; this hook owns subsequent
// changes.
//
// We don't watch localStorage cross-tab — the legacy template
// didn't either, and the use case (operator running one tab) is
// niche enough to defer until someone asks.

import { act, renderHook } from "@testing-library/react";
import { afterEach, beforeEach, describe, expect, it } from "vitest";
import { useTheme } from "./useTheme";

beforeEach(() => {
  // Reset the document + storage between tests so each starts
  // from a known baseline.
  document.documentElement.removeAttribute("data-theme");
  window.localStorage.clear();
});

afterEach(() => {
  document.documentElement.removeAttribute("data-theme");
  window.localStorage.clear();
});

describe("useTheme", () => {
  it("reads the initial theme from <html data-theme>", () => {
    document.documentElement.setAttribute("data-theme", "dark");
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("dark");
  });

  it("defaults to 'light' when no data-theme attribute is set", () => {
    // The boot script intentionally leaves the attribute OFF for
    // light mode — :root :not([data-theme="dark"]) is the default
    // stylesheet path. So absence == light.
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("light");
  });

  it("toggleTheme() flips light → dark", () => {
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("light");

    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe("dark");
    expect(document.documentElement.getAttribute("data-theme")).toBe("dark");
  });

  it("toggleTheme() flips dark → light (clears the attribute)", () => {
    document.documentElement.setAttribute("data-theme", "dark");
    const { result } = renderHook(() => useTheme());
    expect(result.current.theme).toBe("dark");

    act(() => result.current.toggleTheme());
    expect(result.current.theme).toBe("light");
    // Light = attribute absent (default selector path in
    // globals.css :root). Don't leave a stale `data-theme=light`
    // attribute that other code might branch on.
    expect(document.documentElement.hasAttribute("data-theme")).toBe(false);
  });

  it("persists the new theme to localStorage so the boot script reads it next load", () => {
    const { result } = renderHook(() => useTheme());
    act(() => result.current.toggleTheme());
    expect(window.localStorage.getItem("ia-reviewer-theme")).toBe("dark");

    act(() => result.current.toggleTheme());
    expect(window.localStorage.getItem("ia-reviewer-theme")).toBe("light");
  });

  it("setTheme(t) lets callers force a specific theme", () => {
    // Useful for future "Match system" / "Reset" affordances —
    // not strictly needed by the pill button (toggle is enough)
    // but cheap to expose.
    const { result } = renderHook(() => useTheme());
    act(() => result.current.setTheme("dark"));
    expect(result.current.theme).toBe("dark");
    act(() => result.current.setTheme("light"));
    expect(result.current.theme).toBe("light");
  });

  it("survives a missing localStorage (private browsing / SSR edge case)", () => {
    // Storage access can throw in some browsers' private modes.
    // The hook MUST NOT crash the page in that case — just skip
    // persistence.
    const original = window.localStorage;
    Object.defineProperty(window, "localStorage", {
      configurable: true,
      get() {
        throw new Error("storage disabled");
      },
    });
    try {
      const { result } = renderHook(() => useTheme());
      expect(() => act(() => result.current.toggleTheme())).not.toThrow();
      expect(result.current.theme).toBe("dark");
    } finally {
      Object.defineProperty(window, "localStorage", {
        configurable: true,
        value: original,
      });
    }
  });
});
