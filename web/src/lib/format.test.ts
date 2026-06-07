// Pure-logic formatters used across the UI. Behaviour mirrors the
// helpers in legacy `templates/index.html` (`_fmtTokens`, `_fmtElapsed`)
// — we pin the contract here so the React port can't accidentally
// drift from what users already see.
import { describe, expect, it } from "vitest";
import { fmtTokens, fmtElapsed, severityRank, normalizeSeverity } from "./format";

describe("fmtTokens", () => {
  // The token-counter line in the Active reviews sidebar uses this
  // formatter every 5s. It must stay narrow enough not to wrap and
  // accurate enough that operators can tell 9k from 12k at a glance.
  it("returns '0' for zero", () => {
    expect(fmtTokens(0)).toBe("0");
  });

  it("returns plain digits below 1_000", () => {
    expect(fmtTokens(7)).toBe("7");
    expect(fmtTokens(42)).toBe("42");
    expect(fmtTokens(999)).toBe("999");
  });

  it("uses 'k' suffix for thousands with one decimal place", () => {
    expect(fmtTokens(1_000)).toBe("1.0k");
    expect(fmtTokens(1_200)).toBe("1.2k");
    expect(fmtTokens(12_345)).toBe("12.3k");
    expect(fmtTokens(99_900)).toBe("99.9k");
  });

  it("uses 'M' suffix for millions with one decimal place", () => {
    expect(fmtTokens(1_000_000)).toBe("1.0M");
    expect(fmtTokens(2_500_000)).toBe("2.5M");
    expect(fmtTokens(15_400_000)).toBe("15.4M");
  });

  it("rounds (not floors) at the boundary", () => {
    // 9_950 rounds to 10.0k, not 9.9k — operators reading the counter
    // shouldn't see a number that's lower than the true value.
    expect(fmtTokens(9_950)).toBe("10.0k");
  });

  it("returns '0' for negative or NaN inputs (defensive)", () => {
    expect(fmtTokens(-5)).toBe("0");
    expect(fmtTokens(Number.NaN)).toBe("0");
  });
});

describe("fmtElapsed", () => {
  // Elapsed time appears next to the spinner in the Active reviews
  // panel. We want "Xs", "Xm Ys", "Xh Ym" — never raw seconds past
  // a minute. The format must round to whole seconds (no ".0").
  it("returns 0s for non-positive input", () => {
    expect(fmtElapsed(0)).toBe("0s");
    expect(fmtElapsed(-3)).toBe("0s");
  });

  it("uses 'Xs' under one minute", () => {
    expect(fmtElapsed(1)).toBe("1s");
    expect(fmtElapsed(45)).toBe("45s");
    expect(fmtElapsed(59)).toBe("59s");
  });

  it("uses 'Xm Ys' between one minute and one hour", () => {
    expect(fmtElapsed(60)).toBe("1m 0s");
    expect(fmtElapsed(65)).toBe("1m 5s");
    expect(fmtElapsed(125)).toBe("2m 5s");
    expect(fmtElapsed(3_599)).toBe("59m 59s");
  });

  it("uses 'Xh Ym' beyond one hour (drops seconds)", () => {
    expect(fmtElapsed(3_600)).toBe("1h 0m");
    expect(fmtElapsed(3_660)).toBe("1h 1m");
    expect(fmtElapsed(7_320)).toBe("2h 2m");
  });

  it("floors fractional seconds", () => {
    expect(fmtElapsed(59.9)).toBe("59s");
    expect(fmtElapsed(60.7)).toBe("1m 0s");
  });
});

describe("severityRank", () => {
  // Used to sort findings critical → major → minor → info in the
  // Critical findings panel and the past-reviews list. The mapping
  // must agree with Python's `report_renderer.severity_rank` so a
  // backend-produced summary table and the frontend-sorted list show
  // findings in the same order.
  it("ranks critical highest", () => {
    expect(severityRank("critical")).toBeGreaterThan(severityRank("major"));
    expect(severityRank("major")).toBeGreaterThan(severityRank("minor"));
    expect(severityRank("minor")).toBeGreaterThan(severityRank("info"));
  });

  it("returns 0 for unknown severities (sorted last)", () => {
    expect(severityRank("bogus")).toBe(0);
    expect(severityRank("")).toBe(0);
  });

  it("is case-insensitive", () => {
    expect(severityRank("Critical")).toBe(severityRank("critical"));
    expect(severityRank("MAJOR")).toBe(severityRank("major"));
  });
});

describe("normalizeSeverity", () => {
  // The summary dot in the past-reviews list keys off a CSS class
  // (`past-item-sev minor` etc). We normalise to a known set so a
  // backend that ever sends "MAJOR" or "Critical" still gets a valid
  // dot color. Unknowns fall through to "info" (grey).
  it("returns the lowercase known severity", () => {
    expect(normalizeSeverity("critical")).toBe("critical");
    expect(normalizeSeverity("MAJOR")).toBe("major");
    expect(normalizeSeverity("Minor")).toBe("minor");
    expect(normalizeSeverity("info")).toBe("info");
  });

  it("falls back to 'info' for anything else", () => {
    expect(normalizeSeverity("blocker")).toBe("info");
    expect(normalizeSeverity("")).toBe("info");
    expect(normalizeSeverity(undefined)).toBe("info");
    expect(normalizeSeverity(null)).toBe("info");
  });
});
