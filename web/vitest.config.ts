import { defineConfig } from "vitest/config";
import react from "@vitejs/plugin-react";

// Tests run under jsdom so React Testing Library has a DOM. The
// setup file registers `@testing-library/jest-dom` matchers globally.
// We follow Vitest's modern colocation convention: tests live next to
// their source as `*.test.{ts,tsx}`. Cross-component / wiring tests
// can still live under `tests/` — picked up by the second glob.
export default defineConfig({
  plugins: [react()],
  test: {
    globals: true,
    environment: "jsdom",
    setupFiles: ["./src/test/setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}", "tests/**/*.{test,spec}.{ts,tsx}"],
    css: false,
  },
});
