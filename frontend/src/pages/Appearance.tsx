// Settings › Appearance: make the app yours. Pick a home (a preset or your own arrangement of sections, each with
// its picture, any hidden), the look, the colour of each part (checked for colour-blind readers before it can be
// kept), the app's name and mark, the type size, the density and hiding amounts. Kept on the server, so every
// device agrees; the page waits for the server's copy before it lets anything change.
import { ArrowDown, ArrowUp, CircleCheck, Eye, EyeOff, GripVertical, OctagonAlert, TriangleAlert, X } from "lucide-react";
import { useEffect, useState, type ReactNode } from "react";
import { saveAppearance, useAppearance, useAppearanceLoad, type Arrangement, type AppearanceState, type Section } from "../appearance";
import { defaultRoleColours, LOOKS, ROLES, VALIDATED_ORDER, type LookId, type Role } from "../looks";
import { asHex, validatePalette, type Report } from "../looks/validate";
import { PRESETS, SECTIONS, sectionByKey } from "../sections";
import { ConfirmButton } from "../components/ConfirmButton";
import { Failed, Loading, Said } from "../components/State";

// Where a save's outcome is said: beside the part of the page whose button caused it.
type Area = "home" | "look" | "colours" | "name" | "text";

function H({ children, sub }: { children: string; sub?: string }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 2, marginBottom: 8 }}>
      <h2 style={{ margin: 0, fontSize: 16, fontWeight: 800 }}>{children}</h2>
      {sub && <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{sub}</span>}
    </div>
  );
}

function Thumb({ a }: { a: Arrangement }) {
  // Series colours only: green, amber and red are kept for status, which always comes with an icon and a word.
  const fills: Record<string, string> = { one_thing: "var(--ink-2)", safe: "var(--housing)", paycheck: "var(--paycheck)", cover: "var(--debt)", saving: "var(--saving)", view: "var(--muted)", sixty: "var(--paycheck)", borrowing: "var(--later)", debts: "var(--debt)" };
  return (
    <div aria-hidden="true" style={{ display: "flex", flexDirection: "column", gap: 3, padding: 6, borderRadius: 8, background: "var(--surface-2)", width: 70, flexShrink: 0 }}>
      {a.sections.filter((s) => !s.hidden).slice(0, 6).map((s, i) => <div key={i} style={{ height: s.key === "view" || s.key === "paycheck" ? 14 : 6, borderRadius: 3, background: fills[s.key] ?? "var(--hairline)", opacity: 0.85 }} />)}
    </div>
  );
}

function StateIcon({ state }: { state: "pass" | "warn" | "fail" }) {
  if (state === "pass") return <span style={{ display: "inline-flex", alignItems: "center", gap: 4, color: "var(--safe-ink)", fontWeight: 800 }}><CircleCheck size={14} aria-hidden="true" />Passes</span>;
  if (state === "warn") return <span style={{ display: "inline-flex", alignItems: "center", gap: 4, color: "var(--warning-ink)", fontWeight: 800 }}><TriangleAlert size={14} aria-hidden="true" />Passes when labels are shown</span>;
  return <span style={{ display: "inline-flex", alignItems: "center", gap: 4, color: "var(--critical-ink)", fontWeight: 800 }}><OctagonAlert size={14} aria-hidden="true" />Fails</span>;
}

function Editor({ start, onSave, onCancel, said }: { start: Arrangement; onSave: (a: Arrangement) => void; onCancel: () => void; said: ReactNode }) {
  const [a, setA] = useState<Arrangement>(start);
  const [dragging, setDragging] = useState<number | null>(null);
  const [adding, setAdding] = useState("");
  const move = (from: number, to: number) => {
    if (to < 0 || to >= a.sections.length || from === to) return;
    const next = [...a.sections];
    const [s] = next.splice(from, 1);
    next.splice(to, 0, s);
    setA({ ...a, sections: next });
  };
  const patch = (i: number, p: Partial<Section>) => setA({ ...a, sections: a.sections.map((s, k) => (k === i ? { ...s, ...p } : s)) });
  return (
    <div className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
      <label style={{ display: "flex", flexDirection: "column", gap: 4, maxWidth: 360 }}>
        <span className="lab">Name</span>
        <input className="input" value={a.name} maxLength={40} onChange={(e) => setA({ ...a, name: e.target.value })} />
      </label>
      <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 }} aria-label="Sections, in order">
        {a.sections.map((s, i) => {
          const def = sectionByKey(s.key);
          return (
            <li key={`${s.key}-${i}`} draggable onDragStart={() => setDragging(i)} onDragOver={(e) => e.preventDefault()} onDrop={() => { if (dragging !== null) move(dragging, i); setDragging(null); }}
              style={{ display: "grid", gridTemplateColumns: "auto minmax(0, 1fr) auto auto", gap: 8, alignItems: "center", padding: "8px 10px", borderRadius: 10, border: "1px solid var(--hairline)", background: dragging === i ? "var(--surface-2)" : "var(--surface)", opacity: s.hidden ? 0.55 : 1 }}>
              <span style={{ display: "inline-flex", gap: 2, alignItems: "center" }}>
                <GripVertical size={16} aria-hidden="true" style={{ color: "var(--muted)", cursor: "grab" }} />
                <button type="button" className="btn" style={{ minHeight: 32, padding: "2px 6px" }} onClick={() => move(i, i - 1)} aria-label={`Move ${def?.name} up`} disabled={i === 0}><ArrowUp size={14} /></button>
                <button type="button" className="btn" style={{ minHeight: 32, padding: "2px 6px" }} onClick={() => move(i, i + 1)} aria-label={`Move ${def?.name} down`} disabled={i === a.sections.length - 1}><ArrowDown size={14} /></button>
              </span>
              <span style={{ minWidth: 0 }}>
                <strong style={{ fontSize: 13 }}>{def?.name ?? s.key}</strong>
                <span style={{ display: "block", fontSize: 12, color: "var(--ink-2)" }}>{def?.description}</span>
              </span>
              {def?.modes ? (
                <select className="input" style={{ minHeight: 34, padding: "4px 8px", fontSize: 13 }} value={s.show_as ?? def.modes[0].id} onChange={(e) => patch(i, { show_as: e.target.value })} aria-label={`Show ${def.name} as`}>
                  {def.modes.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                </select>
              ) : <span />}
              <span style={{ display: "inline-flex", gap: 4 }}>
                <button type="button" className="btn" style={{ minHeight: 32, padding: "2px 8px", display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12 }} onClick={() => patch(i, { hidden: !s.hidden })} aria-pressed={!s.hidden}>
                  <span className="sr-only">{def?.name ?? s.key} on Home: </span>
                  {s.hidden ? <EyeOff size={14} aria-hidden="true" /> : <Eye size={14} aria-hidden="true" />}{s.hidden ? "Hidden" : "Shown"}
                </button>
                <button type="button" className="btn" style={{ minHeight: 32, padding: "2px 8px" }} onClick={() => setA({ ...a, sections: a.sections.filter((_, k) => k !== i) })} aria-label={`Remove ${def?.name}`}><X size={14} /></button>
              </span>
            </li>
          );
        })}
      </ol>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <select className="input" value={adding} onChange={(e) => setAdding(e.target.value)} aria-label="Add a section" style={{ minHeight: 38 }}>
          <option value="">Add a section…</option>
          {SECTIONS.map((s) => <option key={s.key} value={s.key}>{s.name}</option>)}
        </select>
        <button type="button" className="btn" disabled={!adding || a.sections.length >= 24} onClick={() => { const def = sectionByKey(adding); setA({ ...a, sections: [...a.sections, { key: adding, show_as: def?.modes?.[0].id ?? null }] }); setAdding(""); }}>Add</button>
        <div style={{ flexGrow: 1 }} />
        <button type="button" className="btn" onClick={onCancel}>Cancel</button>
        <button type="button" className="btn dark" disabled={!a.name.trim() || !a.sections.length} onClick={() => onSave({ ...a, name: a.name.trim() })}>Keep this home</button>
        {said}
      </div>
    </div>
  );
}

function ColourEditor({ ap, onSave, said }: { ap: AppearanceState; onSave: (colours: AppearanceState["colours"]) => Promise<boolean>; said: ReactNode }) {
  const start = (mode: "light" | "dark") => ({ ...defaultRoleColours(mode), ...(ap.colours?.[mode] ?? {}) });
  const [light, setLight] = useState<Record<Role, string>>(start("light"));
  const [dark, setDark] = useState<Record<Role, string>>(start("dark"));
  const [busy, setBusy] = useState(false);
  const reports: Report[] = [validatePalette(VALIDATED_ORDER.map((r) => light[r]), "light"), validatePalette(VALIDATED_ORDER.map((r) => dark[r]), "dark")];
  const ok = reports.every((r) => r.ok);
  // Kept as the server keeps them: "#rrggbb" in lower case, a "#" added to a colour typed without one.
  const changed = (mode: "light" | "dark", v: Record<Role, string>) =>
    Object.fromEntries(Object.entries(v).map(([r, hex]) => [r, asHex(hex) ?? hex] as const).filter(([r, hex]) => hex !== defaultRoleColours(mode)[r as Role].toLowerCase()));
  return (
    <div className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 12 }}>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))", gap: 14 }}>
        {(["light", "dark"] as const).map((mode, k) => {
          const vals = mode === "light" ? light : dark;
          const set = mode === "light" ? setLight : setDark;
          return (
            <div key={mode} style={{ display: "flex", flexDirection: "column", gap: 8 }}>
              <h3 style={{ margin: 0, fontSize: 13, fontWeight: 800 }}>{mode === "light" ? "On light looks" : "On dark looks"}</h3>
              <div aria-hidden="true" style={{ display: "flex", height: 22, borderRadius: 6, overflow: "hidden", gap: 2, background: mode === "light" ? "#fcfcfb" : "#1a1a19", padding: 2 }}>
                {ROLES.map((r) => <div key={r.role} style={{ flex: 1, background: vals[r.role], borderRadius: 3 }} />)}
              </div>
              {ROLES.map((r) => (
                <label key={r.role} style={{ display: "grid", gridTemplateColumns: "34px minmax(0, 1fr) minmax(0, 92px)", gap: 8, alignItems: "center", fontSize: 13 }}>
                  <input type="color" value={asHex(vals[r.role]) ?? "#000000"} onChange={(e) => set({ ...vals, [r.role]: e.target.value })} aria-label={`${r.label} on ${mode} looks`} style={{ width: 34, height: 32, border: 0, background: "none", padding: 0 }} />
                  <span>{r.label}{r.role === "aside" ? <span style={{ color: "var(--ink-2)" }}> · a neutral</span> : null}</span>
                  <input className="input num" value={vals[r.role]} maxLength={7} onChange={(e) => set({ ...vals, [r.role]: e.target.value })} aria-label={`${r.label} hex on ${mode} looks`} style={{ minHeight: 32, padding: "4px 8px", fontSize: 12 }} />
                </label>
              ))}
              <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
                {reports[k].checks.map((c) => (
                  <div key={c.name} style={{ display: "grid", gridTemplateColumns: "auto minmax(0, 1fr)", gap: 8 }}>
                    <StateIcon state={c.state} />
                    <span style={{ color: "var(--ink-2)" }}><strong style={{ color: "var(--ink)" }}>{c.name}.</strong> {c.detail}</span>
                  </div>
                ))}
              </div>
            </div>
          );
        })}
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button type="button" className="btn dark" disabled={!ok || busy} onClick={async () => {
          const l = changed("light", light);
          const d = changed("dark", dark);
          setBusy(true);
          await onSave(Object.keys(l).length || Object.keys(d).length ? { light: l, dark: d } : null);
          setBusy(false);
        }}>{busy ? "Keeping…" : "Keep these colours"}</button>
        <button type="button" className="btn" onClick={() => { setLight(defaultRoleColours("light")); setDark(defaultRoleColours("dark")); }}>Back to the checked palette</button>
        {!ok && <Said text="A check fails, so these cannot be kept yet: change the colour it names." tone="error" />}
        {ok && said}
      </div>
      <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Parts sit side by side in this order, so each colour is checked against its neighbours. The warning, past-due and good colours are reserved and always come with an icon and a word.</span>
    </div>
  );
}

function readFile(f: File): Promise<string> {
  return new Promise((resolve, reject) => {
    const r = new FileReader();
    r.onload = () => resolve(String(r.result));
    r.onerror = () => reject(new Error("Could not read the file"));
    r.readAsDataURL(f);
  });
}

export function Appearance() {
  const ap = useAppearance();
  const load = useAppearanceLoad();
  const [editing, setEditing] = useState<Arrangement | null>(null);
  const [note, setNote] = useState<{ at: Area; text: string; tone: "info" | "error" } | null>(null);
  const [name, setName] = useState(ap.brand.name);
  // The name field follows what is kept: it arrives after the page opens, and changes when kept from another device.
  useEffect(() => setName(ap.brand.name), [ap.brand.name]);
  // Says "kept" only when the server took it; a failure is said in its place, next to the same button.
  const save = async (at: Area, patch: Partial<AppearanceState>, done: string): Promise<boolean> => {
    setNote(null);
    try {
      await saveAppearance({ ...ap, ...patch });
      setNote({ at, text: done, tone: "info" });
      return true;
    } catch (e) {
      setNote({ at, text: (e as Error).message, tone: "error" });
      return false;
    }
  };
  const said = (at: Area) => (note?.at === at ? <Said text={note.text} tone={note.tone} /> : null);
  const mine = ap.arrangements;
  const all = [...mine.map((m) => ({ ...m, description: `${m.sections.length} sections, yours` })), ...PRESETS];
  const copy = (a: Arrangement) => setEditing({ id: `mine-${Date.now().toString(36)}`, name: mine.some((m) => m.id === a.id) ? a.name : `My ${a.name}`, sections: a.sections.map((s) => ({ ...s })) });
  const heading = (
    <section>
      <h1 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 800 }}>Appearance</h1>
      <p style={{ margin: 0, color: "var(--ink-2)" }}>Every home shows the same numbers. Pick the arrangement that reads best, or make your own. Saved for all your devices.</p>
    </section>
  );
  // Until the kept appearance is here, nothing on the page can change it: a save would send the defaults.
  if (load.status !== "ready") {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 22, maxWidth: 1100 }}>
        {heading}
        {load.status === "failed" ? <Failed error={load.error ?? "The app did not answer."} what="your appearance" onRetry={load.retry} /> : <Loading what="your appearance" />}
      </div>
    );
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 22, maxWidth: 1100 }}>
      {heading}

      <section>
        <H sub="A home is a list of sections, each drawn the way you choose. Copy any of these and change it.">Home</H>
        {editing ? (
          <Editor start={editing} said={said("home")} onCancel={() => setEditing(null)} onSave={async (a) => {
            const exists = mine.some((m) => m.id === a.id);
            if (await save("home", { arrangements: exists ? mine.map((m) => (m.id === a.id ? a : m)) : [...mine, a], active: a.id }, `${a.name} is your home now.`)) setEditing(null);
          }} />
        ) : (
          <>
            {note?.at === "home" && <div style={{ marginBottom: 8 }}>{said("home")}</div>}
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 300px), 1fr))", gap: 10 }}>
              {all.map((a) => {
                const on = ap.active === a.id;
                const isMine = mine.some((m) => m.id === a.id);
                return (
                  <div key={a.id} className="card" style={{ padding: 12, display: "flex", gap: 10, border: on ? "2px solid var(--ink)" : "1px solid var(--hairline)" }}>
                    <Thumb a={a} />
                    <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0, flexGrow: 1 }}>
                      <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>{a.name}{on ? <span style={{ color: "var(--safe-ink)", fontWeight: 700 }}> · in use</span> : null}</h3>
                      <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{a.description}</span>
                      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: "auto" }}>
                        {!on && <button type="button" className="btn dark" style={{ minHeight: 32, padding: "4px 10px" }} onClick={() => save("home", { active: a.id }, `${a.name} is your home now.`)}>Use</button>}
                        <button type="button" className="btn" style={{ minHeight: 32, padding: "4px 10px" }} onClick={() => (isMine ? setEditing(a) : copy(a))}>{isMine ? "Edit" : "Copy and change"}</button>
                        {isMine && <ConfirmButton label="Remove" question={`Remove ${a.name}?`} yes="Yes, remove it" no="Keep it" onConfirm={() => save("home", { arrangements: mine.filter((m) => m.id !== a.id), active: on ? "paycheck-first" : ap.active }, `${a.name} removed.`)} style={{ minHeight: 32, padding: "4px 10px" }} />}
                      </div>
                    </div>
                  </div>
                );
              })}
              <button type="button" className="card" onClick={() => setEditing({ id: `mine-${Date.now().toString(36)}`, name: "My home", sections: [{ key: "one_thing" }, { key: "safe", show_as: "hero" }] })} style={{ padding: 12, border: "1px dashed var(--hairline)", background: "transparent", color: "var(--ink-2)", font: "inherit", fontWeight: 800, cursor: "pointer", minHeight: 96 }}>Start from blank</button>
            </div>
          </>
        )}
      </section>

      <section>
        <H>Look</H>
        <div style={{ display: "flex", gap: 12, flexWrap: "wrap", alignItems: "center" }}>
          {LOOKS.map((l) => {
            const on = ap.look === l.id;
            return (
              <button key={l.id} type="button" onClick={() => save("look", { look: l.id as LookId }, `${l.name} is the look now.`)} className="card" aria-pressed={on} style={{ display: "flex", gap: 12, alignItems: "center", padding: 12, cursor: "pointer", border: on ? "2px solid var(--ink)" : "1px solid var(--hairline)", background: "var(--surface)", color: "var(--ink)", font: "inherit", minWidth: "min(220px, 100%)" }}>
                <span aria-hidden="true" style={{ width: 44, height: 44, borderRadius: 10, background: l.swatch.ground, border: "1px solid var(--hairline)", display: "grid", placeItems: "center" }}>
                  <span style={{ width: 18, height: 18, borderRadius: 5, background: l.swatch.accent }} />
                </span>
                <span style={{ textAlign: "left" }}><strong>{l.name}</strong><div style={{ fontSize: 12, color: "var(--ink-2)" }}>{l.description}</div></span>
              </button>
            );
          })}
          {said("look")}
        </div>
      </section>

      <section>
        <H sub="Pick any colour for a part. It is checked against its neighbours for colour-blind readers and full colour vision, on light and dark looks, before it can be kept.">Colours</H>
        <ColourEditor ap={ap} said={said("colours")} onSave={(colours) => save("colours", { colours }, "Kept. Every picture uses these colours now.")} />
      </section>

      <section>
        <H sub="Shown in the rail, the browser tab, the brief and every export.">Name and mark</H>
        <div className="card" style={{ padding: 14, display: "flex", gap: 14, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 220px" }}>
            <span className="lab">Name</span>
            <input className="input" value={name} maxLength={40} placeholder="FinancialAuthority" onChange={(e) => setName(e.target.value)} />
          </label>
          <button type="button" className="btn dark" onClick={() => save("name", { brand: { ...ap.brand, name: name.trim() } }, "Name kept.")}>Keep the name</button>
          <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap", minWidth: 0 }}>
            {ap.brand.mark ? <img src={ap.brand.mark} alt="Your mark" width={40} height={40} style={{ objectFit: "contain", borderRadius: 8, background: "var(--surface-2)" }} /> : <span style={{ fontSize: 12, color: "var(--ink-2)" }}>No mark yet</span>}
            <input type="file" accept="image/png,image/jpeg,image/webp,image/svg+xml" aria-label="Upload a mark" style={{ maxWidth: "100%" }} onChange={async (e) => {
              const f = e.target.files?.[0];
              if (!f) return;
              if (f.size > 300_000) { setNote({ at: "name", text: "The mark is larger than 300 KB; use a smaller image.", tone: "error" }); return; }
              try {
                await save("name", { brand: { ...ap.brand, name: name.trim(), mark: await readFile(f) } }, "Mark kept.");
              } catch (err) {
                setNote({ at: "name", text: (err as Error).message, tone: "error" });
              }
            }} />
            {ap.brand.mark && <button type="button" className="btn" onClick={() => save("name", { brand: { ...ap.brand, mark: "" } }, "Mark removed.")}>Remove</button>}
          </div>
          {said("name")}
        </div>
      </section>

      <section>
        <H sub="The whole app scales together. Hide amounts keeps figures blurred until you tap them: pictures first. The header's Hide amounts button is the same switch.">Text and spacing</H>
        <div className="card" style={{ padding: 14, display: "flex", gap: 22, flexWrap: "wrap", alignItems: "center" }}>
          <div role="radiogroup" aria-label="Type size" style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
            <span className="lab">Type size</span>
            {(["regular", "larger", "largest"] as const).map((t) => <button key={t} type="button" role="radio" aria-checked={ap.type_size === t} className={`btn${ap.type_size === t ? " dark" : ""}`} onClick={() => save("text", { type_size: t }, "Type size kept.")}>{t[0].toUpperCase() + t.slice(1)}</button>)}
          </div>
          <div role="radiogroup" aria-label="Density" style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
            <span className="lab">Density</span>
            {(["comfortable", "compact"] as const).map((t) => <button key={t} type="button" role="radio" aria-checked={ap.density === t} className={`btn${ap.density === t ? " dark" : ""}`} onClick={() => save("text", { density: t }, "Density kept.")}>{t[0].toUpperCase() + t.slice(1)}</button>)}
          </div>
          <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, minHeight: 32 }}>
            <input type="checkbox" checked={ap.quiet} onChange={(e) => save("text", { quiet: e.target.checked }, e.target.checked ? "Amounts are hidden until tapped." : "Amounts are shown.")} style={{ width: 18, height: 18 }} />
            Hide amounts until tapped
          </label>
          {said("text")}
        </div>
      </section>
    </div>
  );
}
