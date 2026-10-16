// Exports: your data and your plan, out of the app. Data in open formats (CSV, JSON, a calendar, Beancount,
// Ledger, QIF, OFX); reports in a template you build from a layout, a palette, mascots and the sections you
// want, previewed live and kept by name; and a calendar feed your phone subscribes to. Every file is made from
// the engine's results, carries its sources and the not-advice line, and never holds a secret.
import { ArrowDown, ArrowUp, Download } from "lucide-react";
import { useEffect, useMemo, useState } from "react";
import { api, useApi } from "../api";
import { DEMO } from "../demo/shim";
import { ConfirmButton } from "../components/ConfirmButton";
import { Failed, Loading, Said } from "../components/State";
import { Where } from "../components/Where";

interface Catalog {
  formats: { id: string; name: string; kind: "data" | "report"; ext: string; description: string }[];
  layouts: { id: string; name: string; family: string; description: string; dark: boolean; charts: boolean; mascot: string; swatch: string[] }[];
  mascots: { id: string; name: string }[];
  palettes: { id: string; name: string }[];
  sections: { id: string; name: string; default: boolean }[];
  templates: Template[];
  feed: { active: boolean; amounts: boolean; created: string | null };
}

interface Template {
  id?: string;
  name: string;
  layout: string;
  palette: string;
  mascot: string | null;
  sections: string[];
  notes: string;
  css: string;
}

// Where a result is said: beside the part of the page whose button caused it.
type Area = "template" | "preview" | "feed" | "copy";

function fileUrl(format: string, t: Template | null, templateId?: string): string {
  const p = new URLSearchParams({ format });
  if (templateId) p.set("template", templateId);
  else if (t) {
    p.set("layout", t.layout);
    p.set("palette", t.palette);
    if (t.mascot) p.set("mascot", t.mascot);
    p.set("sections", t.sections.join(","));
    if (t.notes) p.set("notes", t.notes.slice(0, 1500));
  }
  return `/api/v1/exports/file?${p.toString()}`;
}

function DownloadLink({ href, label }: { href: string; label: string }) {
  return (
    <a href={DEMO ? undefined : href} className="btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 6, opacity: DEMO ? 0.5 : 1 }} download aria-disabled={DEMO}>
      <Download size={14} aria-hidden="true" />{label}
    </a>
  );
}

export function Exports() {
  const cat = useApi<Catalog>("/api/v1/exports");
  const [t, setT] = useState<Template>({ name: "My report", layout: "board", palette: "layout", mascot: null, sections: [], notes: "", css: "" });
  const [preview, setPreview] = useState<string>("");
  const [note, setNote] = useState<{ at: Area; text: string; tone: "info" | "error" } | null>(null);
  const [feedUrl, setFeedUrl] = useState<string | null>(null);
  const [amounts, setAmounts] = useState(false);
  const [advanced, setAdvanced] = useState(false);
  const say = (at: Area, text: string, tone: "info" | "error" = "info") => setNote({ at, text, tone });
  const said = (at: Area) => (note?.at === at ? <Said text={note.text} tone={note.tone} /> : null);
  useEffect(() => {
    if (cat.data && t.sections.length === 0) setT((x) => ({ ...x, sections: cat.data!.sections.filter((s) => s.default).map((s) => s.id) }));
  }, [cat.data, t.sections.length]);
  const key = useMemo(() => JSON.stringify(t), [t]);
  useEffect(() => {
    if (!cat.data || !t.sections.length) return;
    const timer = window.setTimeout(() => {
      api<{ html: string; css_refused: boolean }>("/api/v1/exports/preview", { method: "POST", body: JSON.stringify(t) })
        .then((r) => {
          setPreview(r.html);
          if (r.css_refused) say("preview", "That CSS reaches outside the page (an import, a url or a script), so it is left out.", "error");
        })
        .catch((e) => say("preview", (e as Error).message, "error"));
    }, 350);
    return () => window.clearTimeout(timer);
  }, [key, cat.data]); // eslint-disable-line react-hooks/exhaustive-deps
  if (cat.error && !cat.data) return <Failed error={cat.error} what="the export formats" onRetry={cat.reload} />;
  if (!cat.data) return <Loading what="the export formats" />;
  const c = cat.data;
  const data = c.formats.filter((f) => f.kind === "data");
  const reports = c.formats.filter((f) => f.kind === "report");
  const move = (i: number, d: number) => {
    const s = [...t.sections];
    const j = i + d;
    if (j < 0 || j >= s.length) return;
    [s[i], s[j]] = [s[j], s[i]];
    setT({ ...t, sections: s });
  };
  async function saveTemplate() {
    try {
      const r = await api<{ template: Template }>("/api/v1/exports/templates", { method: "POST", body: JSON.stringify(t) });
      setT(r.template);
      say("template", `Kept as "${r.template.name}".`);
      cat.reload();
    } catch (e) {
      say("template", (e as Error).message, "error");
    }
  }
  // Making the address throws on failure: the first time it is said beside the button, a remake by ConfirmButton.
  async function makeFeed() {
    const r = await api<{ url: string }>("/api/v1/calendar/feed", { method: "POST", body: JSON.stringify({ amounts }) });
    setFeedUrl(r.url);
    say("feed", "The address is made. Copy it below.");
    cat.reload();
  }
  async function firstFeed() {
    try {
      await makeFeed();
    } catch (e) {
      say("feed", (e as Error).message, "error");
    }
  }
  async function feedOff() {
    await api("/api/v1/calendar/feed", { method: "DELETE" });
    setFeedUrl(null);
    say("feed", "The feed is off. Calendars subscribed to it stop updating.");
    cat.reload();
  }
  async function removeTemplate(x: Template) {
    await api(`/api/v1/exports/templates/${x.id}`, { method: "DELETE" });
    cat.reload();
  }
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 1200 }}>
      <div>
        <h1 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 800 }}>Exports</h1>
        <p style={{ margin: 0, color: "var(--ink-2)", maxWidth: "75ch" }}>Your data and your plan, out of the app. Every file is made from the app's own figures, carries its sources and the not-advice line, and never holds a password, key or token.</p>
      </div>

      <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>Your data, in open formats</h2>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 260px), 1fr))", gap: 10 }}>
          {data.map((f) => (
            <div key={f.id} style={{ display: "flex", flexDirection: "column", gap: 6, padding: 10, borderRadius: 10, background: "var(--surface-2)" }}>
              <h3 style={{ margin: 0, fontSize: 13, fontWeight: 800 }}>{f.name}</h3>
              <span style={{ fontSize: 12, color: "var(--ink-2)", flexGrow: 1 }}>{f.description}</span>
              <DownloadLink href={fileUrl(f.id, null)} label={`.${f.ext}`} />
            </div>
          ))}
        </div>
      </section>

      <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>A report, in a template you build</h2>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 380px), 1fr))", gap: 16, alignItems: "start" }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
            <div>
              <div className="lab" style={{ marginBottom: 6 }}>Layout</div>
              <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(150px, 100%), 1fr))", gap: 6 }}>
                {c.layouts.map((l) => (
                  <button key={l.id} type="button" aria-pressed={t.layout === l.id} onClick={() => setT({ ...t, layout: l.id, mascot: t.mascot ?? null })} title={l.description}
                    style={{ display: "flex", gap: 8, alignItems: "center", padding: 8, borderRadius: 10, cursor: "pointer", font: "inherit", textAlign: "left", border: t.layout === l.id ? "2px solid var(--ink)" : "1px solid var(--hairline)", background: "var(--surface)", color: "var(--ink)" }}>
                    <span aria-hidden="true" style={{ width: 30, height: 30, borderRadius: 6, background: l.swatch[0], border: "1px solid var(--hairline)", display: "grid", placeItems: "center", flexShrink: 0 }}>
                      <span style={{ width: 12, height: 12, borderRadius: 3, background: l.swatch[1] }} />
                    </span>
                    <span><strong style={{ fontSize: 12.5 }}>{l.name}</strong><span style={{ display: "block", fontSize: 11, color: "var(--ink-2)" }}>{l.family}</span></span>
                  </button>
                ))}
              </div>
            </div>
            <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
              <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
                <span className="lab">Colours</span>
                <select className="input" value={t.palette} onChange={(e) => setT({ ...t, palette: e.target.value })}>
                  {c.palettes.map((p) => <option key={p.id} value={p.id}>{p.name}</option>)}
                </select>
              </label>
              <label style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
                <span className="lab">Mascots</span>
                <select className="input" value={t.mascot ?? ""} onChange={(e) => setT({ ...t, mascot: e.target.value || null })}>
                  <option value="">The layout's own</option>
                  {c.mascots.map((m) => <option key={m.id} value={m.id}>{m.name}</option>)}
                </select>
              </label>
            </div>
            <div>
              <div className="lab" style={{ marginBottom: 6 }}>Sections, in order</div>
              <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 4 }}>
                {t.sections.map((id, i) => {
                  const name = c.sections.find((s) => s.id === id)?.name ?? id;
                  return (
                    <li key={id} style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13, padding: "4px 8px", borderRadius: 8, background: "var(--surface-2)" }}>
                      <span style={{ flexGrow: 1 }}>{name}</span>
                      <button type="button" className="btn" style={{ minHeight: 32, padding: "2px 8px" }} onClick={() => move(i, -1)} aria-label={`Move ${name} up`} disabled={i === 0}><ArrowUp size={13} /></button>
                      <button type="button" className="btn" style={{ minHeight: 32, padding: "2px 8px" }} onClick={() => move(i, 1)} aria-label={`Move ${name} down`} disabled={i === t.sections.length - 1}><ArrowDown size={13} /></button>
                      <button type="button" className="btn" style={{ minHeight: 32, padding: "2px 8px" }} onClick={() => setT({ ...t, sections: t.sections.filter((x) => x !== id) })} disabled={t.sections.length === 1} aria-label={`Take ${name} out`}>Remove</button>
                    </li>
                  );
                })}
              </ol>
              <div style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 6 }}>
                {c.sections.filter((s) => !t.sections.includes(s.id)).map((s) => <button key={s.id} type="button" className="btn" style={{ minHeight: 32, padding: "3px 9px", fontSize: 12 }} onClick={() => setT({ ...t, sections: [...t.sections, s.id] })}>+ {s.name}</button>)}
              </div>
            </div>
            {t.sections.includes("notes") && (
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span className="lab">Your notes</span>
                <textarea className="input" rows={3} maxLength={4000} value={t.notes} onChange={(e) => setT({ ...t, notes: e.target.value })} />
              </label>
            )}
            <button type="button" className="btn" style={{ alignSelf: "flex-start" }} onClick={() => setAdvanced((a) => !a)} aria-expanded={advanced}>{advanced ? "Hide the stylesheet" : "Edit the stylesheet"}</button>
            {advanced && (
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span className="lab">Extra CSS</span>
                <span style={{ fontSize: 12, color: "var(--ink-2)" }}>For the web page export, applied last. Tokens: --bg, --card, --ink, --muted, --accent, --rule, --head-bg, --head-ink, --track. Styling only: no imports, urls or scripts.</span>
                <textarea className="input num" rows={6} maxLength={20000} value={t.css} onChange={(e) => setT({ ...t, css: e.target.value })} placeholder="h2 { letter-spacing: .08em; }" style={{ fontFamily: "monospace", fontSize: 12 }} />
              </label>
            )}
            {said("preview")}
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span className="lab">Template name</span>
                <input className="input" value={t.name} maxLength={40} onChange={(e) => setT({ ...t, name: e.target.value })} style={{ width: "min(220px, 100%)" }} />
              </label>
              <button type="button" className="btn dark" onClick={saveTemplate} disabled={DEMO}>Keep this template</button>
              {said("template")}
            </div>
            <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
              {reports.map((f) => <DownloadLink key={f.id} href={fileUrl(f.id, t)} label={f.name} />)}
            </div>
          </div>
          <div style={{ minWidth: 0 }}>
            <div className="lab" style={{ marginBottom: 6 }}>Preview, the web page export</div>
            <iframe title="Report preview" sandbox="" srcDoc={preview} style={{ width: "100%", height: 640, border: "1px solid var(--hairline)", borderRadius: 10, background: "#fff" }} />
          </div>
        </div>
      </section>

      {c.templates.length > 0 && (
        <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
          <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>Your templates</h2>
          {c.templates.map((x) => (
            <div key={x.id} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center", padding: 8, borderRadius: 10, background: "var(--surface-2)" }}>
              <strong style={{ minWidth: "min(140px, 100%)" }}>{x.name}</strong>
              <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{c.layouts.find((l) => l.id === x.layout)?.name} · {x.sections.length} sections</span>
              <div style={{ flexGrow: 1 }} />
              <button type="button" className="btn" onClick={() => setT(x)}>Edit</button>
              {reports.map((f) => <DownloadLink key={f.id} href={fileUrl(f.id, null, x.id)} label={`.${f.ext}`} />)}
              <ConfirmButton label="Remove" question={`Remove the template ${x.name}?`} yes="Yes, remove it" no="Keep it" onConfirm={() => removeTemplate(x)} />
            </div>
          ))}
        </section>
      )}

      <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>Paydays and due dates in your phone's calendar</h2>
        <span style={{ fontSize: 13, color: "var(--ink-2)", maxWidth: "75ch" }}>A private address your calendar app subscribes to; it refreshes on its own. Anyone with the address can read it, so amounts stay off unless you turn them on. Making a new address stops the old one.</span>
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <span style={{ fontSize: 13 }}>{c.feed.active ? `A feed is active${c.feed.amounts ? ", with amounts" : ", names and dates only"}.` : "No feed yet."}</span>
          <label style={{ display: "flex", gap: 6, alignItems: "center", fontSize: 13, minHeight: 32 }}><input type="checkbox" checked={amounts} onChange={(e) => setAmounts(e.target.checked)} style={{ width: 18, height: 18 }} />Include amounts</label>
          {c.feed.active
            ? <ConfirmButton label="Make a new address" question="Make a new address? The old one stops working, so every calendar using it must subscribe again." yes="Yes, make a new one" no="Keep the old one" onConfirm={makeFeed} className="btn dark" />
            : <button type="button" className="btn dark" onClick={firstFeed} disabled={DEMO}>Make the address</button>}
          {c.feed.active && <ConfirmButton label="Turn the feed off" question="Turn the feed off? Calendars subscribed to it stop updating." yes="Yes, turn it off" no="Keep it on" onConfirm={feedOff} />}
          {said("feed")}
        </div>
        {feedUrl && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: 10, borderRadius: 10, background: "var(--surface-2)" }}>
            <span style={{ fontSize: 13 }}>Shown once: copy it now.</span>
            <Where id="calendar" />
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span className="lab">Calendar feed address</span>
              <input className="input num" readOnly value={feedUrl} onFocus={(e) => e.target.select()} style={{ fontSize: 12 }} />
            </label>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <button type="button" className="btn" onClick={() => navigator.clipboard?.writeText(feedUrl).then(() => say("copy", "Copied the feed address."), () => say("copy", "This browser would not copy; select the address and copy it by hand.", "error"))}>Copy</button>
              {said("copy")}
            </div>
          </div>
        )}
      </section>
    </div>
  );
}
