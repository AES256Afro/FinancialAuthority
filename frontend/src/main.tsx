import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, HashRouter } from "react-router-dom";
import App from "./App";
import "@fontsource-variable/manrope";
import "./looks/tokens.css";
import { DEMO } from "./demo/shim";
import { applyLook } from "./looks";
import { getPrefs } from "./prefs";

applyLook(getPrefs().look);

// After an update, a page piece this tab knew may have been renamed. Load the new app once instead of breaking;
// the flag stops a loop if the piece is truly missing.
window.addEventListener("vite:preloadError", (event) => {
  if (sessionStorage.getItem("fa-reloaded-for-update")) return;
  event.preventDefault();
  try {
    sessionStorage.setItem("fa-reloaded-for-update", "1");
  } catch {
    // private mode: reload anyway, once per page view
  }
  window.location.reload();
});
window.setTimeout(() => {
  try {
    sessionStorage.removeItem("fa-reloaded-for-update");
  } catch {
    // nothing to clear
  }
}, 10000);

// The static demo is served from a single page at a fixed address, so its routes live after the hash.
const Router = DEMO ? HashRouter : BrowserRouter;

// Installable: the shell is kept for offline opening; the server's answers never are.
if (!DEMO && import.meta.env.PROD && "serviceWorker" in navigator) {
  window.addEventListener("load", () => {
    navigator.serviceWorker.register("/sw.js").catch(() => undefined);
  });
}

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Router>
      <App />
    </Router>
  </StrictMode>,
);
