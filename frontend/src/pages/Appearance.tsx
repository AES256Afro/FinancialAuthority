// Settings › Appearance: pick the home view and the look. Kept on this device; the server keeps a default.
import { useState } from "react";
import { api } from "../api";
import { LOOKS, applyLook } from "../looks";
import type { LookId } from "../looks";
import { getPrefs, setPrefs } from "../prefs";
import { VIEWS } from "../views";

export function Appearance() {
  const [prefs, update] = useState(getPrefs());
  const [saved, setSaved] = useState<string | null>(null);

  function choose(partial: Partial<typeof prefs>) {
    const next = setPrefs(partial);
    update(next);
    if (partial.look) applyLook(partial.look);
  }

  async function makeDefault() {
    try {
      await api("/api/v1/settings/appearance", { method: "PUT", body: JSON.stringify({ value: { view: prefs.view, look: prefs.look } }) });
      setSaved("Saved as the default for new devices.");
    } catch (e) {
      setSaved(`Could not save: ${(e as Error).message}`);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18, maxWidth: 1100 }}>
      <section>
        <h1 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 800 }}>Appearance</h1>
        <p style={{ margin: 0, color: "var(--ink-2)" }}>Every home view shows the same numbers. Pick the shape that reads best; it is kept on this device.</p>
      </section>
      <section>
        <div className="lab" style={{ marginBottom: 8 }}>Home view</div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(240px, 1fr))", gap: 12 }}>
          {VIEWS.map((v) => {
            const on = prefs.view === v.id;
            return (
              <button key={v.id} type="button" onClick={() => choose({ view: v.id })} className="card" style={{ textAlign: "left", padding: 14, cursor: "pointer", border: on ? "2px solid var(--ink)" : "1px solid var(--hairline)", background: "var(--surface)", color: "var(--ink)", font: "inherit" }}>
                <div style={{ display: "flex", justifyContent: "space-between", alignItems: "baseline", gap: 8 }}>
                  <strong>{v.name}</strong>
                  <span className="lab" style={{ color: v.status === "ready" ? "var(--safe-ink)" : "var(--muted)" }}>{v.status === "ready" ? "ready" : v.milestone}</span>
                </div>
                <div style={{ fontSize: 13, color: "var(--ink-2)", marginTop: 4 }}>{v.description}</div>
              </button>
            );
          })}
        </div>
      </section>
      <section>
        <div className="lab" style={{ marginBottom: 8 }}>Look</div>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap" }}>
          {LOOKS.map((l) => {
            const on = prefs.look === l.id;
            return (
              <button key={l.id} type="button" onClick={() => choose({ look: l.id as LookId })} className="card" style={{ display: "flex", gap: 12, alignItems: "center", padding: 12, cursor: "pointer", border: on ? "2px solid var(--ink)" : "1px solid var(--hairline)", background: "var(--surface)", color: "var(--ink)", font: "inherit", minWidth: 220 }}>
                <span aria-hidden="true" style={{ width: 44, height: 44, borderRadius: 10, background: l.swatch.ground, border: "1px solid var(--hairline)", display: "grid", placeItems: "center" }}>
                  <span style={{ width: 18, height: 18, borderRadius: 5, background: l.swatch.accent }} />
                </span>
                <span style={{ textAlign: "left" }}>
                  <strong>{l.name}</strong>
                  <div style={{ fontSize: 12, color: "var(--ink-2)" }}>{l.description}</div>
                </span>
              </button>
            );
          })}
        </div>
      </section>
      <section style={{ display: "flex", gap: 12, alignItems: "center", flexWrap: "wrap" }}>
        <button type="button" className="btn" onClick={makeDefault}>Make this the default for new devices</button>
        {saved && <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{saved}</span>}
      </section>
    </div>
  );
}
