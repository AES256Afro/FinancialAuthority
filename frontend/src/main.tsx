import { StrictMode } from "react";
import { createRoot } from "react-dom/client";
import { BrowserRouter, HashRouter } from "react-router-dom";
import App from "./App";
import "./looks/tokens.css";
import { DEMO } from "./demo/shim";
import { applyLook } from "./looks";
import { getPrefs } from "./prefs";

applyLook(getPrefs().look);

// The static demo is served from a single page at a fixed address, so its routes live after the hash.
const Router = DEMO ? HashRouter : BrowserRouter;

createRoot(document.getElementById("root")!).render(
  <StrictMode>
    <Router>
      <App />
    </Router>
  </StrictMode>,
);
