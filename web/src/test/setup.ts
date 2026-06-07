// Vitest setup. Registers @testing-library/jest-dom matchers on the
// global `expect` so component tests can use `.toBeInTheDocument()`,
// `.toHaveTextContent()`, etc. Also ensures the DOM is reset between
// tests so leaked nodes never bleed across files.
import "@testing-library/jest-dom/vitest";
import { afterEach } from "vitest";
import { cleanup } from "@testing-library/react";

afterEach(() => {
  cleanup();
});
