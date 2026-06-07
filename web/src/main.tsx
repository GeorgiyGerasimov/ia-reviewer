import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { App } from "./App";
import "./styles/tokens.css";
import "./styles/app.css";

// Root entry. The page shell + theme bootstrap lives in index.html;
// React only renders into #root. StrictMode is enabled because the
// migration will likely surface effect-cleanup issues that we want
// to catch early, not in production.
const root = document.getElementById("root");
if (!root) {
  throw new Error("missing #root element");
}
createRoot(root).render(
  <StrictMode>
    <App />
  </StrictMode>,
);
