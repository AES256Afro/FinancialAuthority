// One line under a field that asks for something: what it is, and a link to the page where you get it, with the
// steps folded underneath. The words live in help/where.ts, so every screen says the same thing the same way.
import { ExternalLink, Info } from "lucide-react";
import { WHERE, type WhereEntry, type WhereId } from "../help/where";

export function Where({ id, fill }: { id: WhereId; fill?: Record<string, string> }) {
  const entry: WhereEntry = WHERE[id];
  const put = (text: string) => text.replace(/\{(\w+)\}/g, (_, k: string) => fill?.[k] ?? `{${k}}`);
  return (
    <div style={{ fontSize: 12, color: "var(--ink-2)", display: "flex", flexDirection: "column", gap: 4 }}>
      <div style={{ display: "flex", gap: 6, alignItems: "flex-start" }}>
        <Info size={14} aria-hidden style={{ flexShrink: 0, marginTop: 1, color: "var(--muted)" }} />
        <span>
          {put(entry.what)}{" "}
          {entry.get.map((g, i) => (
            <span key={g.href}>
              {i > 0 ? " · " : ""}
              <a href={put(g.href)} target="_blank" rel="noreferrer noopener" style={{ display: "inline-flex", alignItems: "center", gap: 3, fontWeight: 700 }}>{g.label}<ExternalLink size={11} aria-hidden /></a>
            </span>
          ))}
        </span>
      </div>
      {entry.steps && entry.steps.length > 0 && (
        <details style={{ marginLeft: 20 }}>
          <summary style={{ cursor: "pointer", fontWeight: 700 }}>Show the steps</summary>
          <ol style={{ margin: "6px 0 0", paddingLeft: 18, display: "flex", flexDirection: "column", gap: 3 }}>
            {entry.steps.map((s) => <li key={s}>{put(s)}</li>)}
          </ol>
          {entry.note && <div style={{ marginTop: 4, color: "var(--muted)" }}>{put(entry.note)}</div>}
        </details>
      )}
    </div>
  );
}
