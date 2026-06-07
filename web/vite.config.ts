import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// Vite builds the SPA into `dist/`. FastAPI mounts that directory at
// / when it exists; otherwise it falls back to the legacy
// `templates/index.html`. The dev server proxies API + WS calls to
// uvicorn so `npm run dev` gives a hot-reloading frontend against
// the real backend on :8000.
//
// Vitest configuration lives in `vitest.config.ts` to avoid the
// dual-vite-types conflict that arises when one file plays the role
// of both vite + vitest config simultaneously. `overrides.vite` in
// package.json forces both projects to resolve to the same vite
// version so types align.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  build: {
    outDir: "dist",
    emptyOutDir: true,
    sourcemap: true,
  },
  server: {
    port: 5173,
    proxy: {
      "/health": "http://localhost:8000",
      "/review": "http://localhost:8000",
      "/reviews": "http://localhost:8000",
      "/reports": "http://localhost:8000",
      "/chat": "http://localhost:8000",
      "/img.png": "http://localhost:8000",
      "/ws": {
        target: "ws://localhost:8000",
        ws: true,
      },
    },
  },
});
