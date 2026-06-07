// Flat-config ESLint. Mirrors the recommended TS + React + hooks setup
// the Vite React-TS template ships with. We deliberately keep the rule
// set tight (no `any`, no unused locals, exhaustive deps) so the
// frontend has the same engineering rigor as the backend.
import js from "@eslint/js";
import globals from "globals";
import reactHooks from "eslint-plugin-react-hooks";
import reactRefresh from "eslint-plugin-react-refresh";
import tseslint from "typescript-eslint";

export default tseslint.config(
  { ignores: ["dist", "node_modules", "coverage"] },
  {
    extends: [js.configs.recommended, ...tseslint.configs.recommended],
    files: ["**/*.{ts,tsx}"],
    languageOptions: {
      ecmaVersion: 2022,
      globals: { ...globals.browser, ...globals.node },
    },
    plugins: {
      "react-hooks": reactHooks,
      "react-refresh": reactRefresh,
    },
    rules: {
      ...reactHooks.configs.recommended.rules,
      "react-refresh/only-export-components": [
        "warn",
        { allowConstantExport: true },
      ],
      "@typescript-eslint/no-unused-vars": [
        "error",
        { argsIgnorePattern: "^_", varsIgnorePattern: "^_" },
      ],
    },
  },
  {
    // shadcn/ui components are vendored from upstream — they
    // intentionally co-export non-component variants
    // (`buttonVariants`, `tabsListVariants`, …) so consumers can
    // apply the same cva classes elsewhere. That trips the
    // react-refresh single-export rule, which exists only for hot-
    // reload speed, not correctness. We re-vendor these files via
    // `npx shadcn add --overwrite`, so reformatting them by hand to
    // appease the rule would just churn on every shadcn update.
    files: ["src/components/ui/**/*.{ts,tsx}"],
    rules: {
      "react-refresh/only-export-components": "off",
    },
  },
);
