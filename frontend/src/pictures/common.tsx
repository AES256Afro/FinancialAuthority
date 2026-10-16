// What every paycheck picture shares: the hover tip, the legend (always present for two or more parts), the
// table twin, and a picker for which check to draw. The pictures draw the engine's figures and nothing else.
import { useRef, useState, type MouseEvent, type ReactNode } from "react";
import { money, shortDate } from "../format";
import { getPrefs } from "../prefs";
import { partColours, type Check, type Part, type PartKey } from "../paychecks";

export function useColours(): Record<PartKey, string> {
  return partColours(getPrefs().look);
}

export function useTip() {
  const ref = useRef<HTMLDivElement>(null);
  const [tip, setTip] = useState<{ x: number; y: number; content: ReactNode } | null>(null);
  const show = (e: MouseEvent | { clientX: number; clientY: number }, content: ReactNode) => {
    const box = ref.current?.getBoundingClientRect();
    if (!box) return;
    setTip({ x: e.clientX - box.left, y: e.clientY - box.top, content });
  };
  return { ref, tip, show, hide: () => setTip(null) };
}

export function Tip({ tip, width }: { tip: { x: number; y: number; content: ReactNode } | null; width: number }) {
  if (!tip) return null;
  const left = Math.max(4, Math.min(tip.x + 12, width - 236));
  return (
    <div role="status" style={{ position: "absolute", left, top: tip.y + 14, width: 230, pointerEvents: "none", zIndex: 5, background: "var(--surface)", border: "1px solid var(--hairline)", borderRadius: 10, padding: "8px 10px", fontSize: 12, boxShadow: "0 6px 20px rgba(0,0,0,.14)", color: "var(--ink)" }}>
      {tip.content}
    </div>
  );
}

export function PartTip({ part, colour, of }: { part: Part; colour: string; of?: number }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 3 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 6, fontWeight: 800 }}>
        <span className="sw" style={{ background: colour, margin: 0 }} />{part.label}
        <span className="num" style={{ marginLeft: "auto" }}>{money(part.amount)}</span>
      </div>
      {of && part.share_of_check != null ? <div style={{ color: "var(--muted)" }}>{Math.round(part.share_of_check * 100)}% of the check</div> : null}
      {part.items.slice(0, 5).map((i, k) => (
        <div key={`${i.source}-${k}`} style={{ display: "flex", gap: 6, color: "var(--ink-2)" }}>
          <span style={{ flexGrow: 1, overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }}>{i.name}</span>
          <span className="num">{money(i.amount)}</span>
        </div>
      ))}
      {part.items.length > 5 && <div style={{ color: "var(--muted)" }}>and {part.items.length - 5} more</div>}
      {part.key === "yours" && part.everyday !== undefined ? <div style={{ color: "var(--muted)" }}>everyday spending {money(part.everyday)}{part.kept !== undefined ? `, kept ${money(part.kept)}` : ""}</div> : null}
      {part.riding && part.riding.length > 0 ? <div style={{ color: "var(--muted)" }}>charged to the card: {part.riding.slice(0, 3).map((r) => r.name).join(", ")}</div> : null}
    </div>
  );
}

export function Legend({ parts, colours }: { parts: { key: PartKey; label: string }[]; colours: Record<PartKey, string> }) {
  return (
    <div style={{ display: "flex", gap: 12, flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)" }}>
      {parts.map((p) => <span key={p.key} style={{ display: "inline-flex", alignItems: "center" }}><span className="sw" style={{ background: colours[p.key] }} />{p.label}</span>)}
    </div>
  );
}

export function TableTwin({ checks, label = "Check" }: { checks: { title: string; check: Check }[]; label?: string }) {
  if (!checks.length) return null;
  const keys = checks[0].check.parts.map((p) => p.key);
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="ledger">
        <thead>
          <tr><th>{label}</th><th style={{ textAlign: "right" }}>Brings in</th>{checks[0].check.parts.map((p) => <th key={p.key} style={{ textAlign: "right" }}>{p.label}</th>)}<th style={{ textAlign: "right" }}>Short</th></tr>
        </thead>
        <tbody>
          {checks.map(({ title, check }) => (
            <tr key={title}>
              <td style={{ whiteSpace: "nowrap" }}>{title}</td>
              <td className="num" style={{ textAlign: "right" }}>{money(check.inflow)}</td>
              {keys.map((k) => <td key={k} className="num" style={{ textAlign: "right" }}>{money(check.parts.find((p) => p.key === k)?.amount ?? 0)}</td>)}
              <td className="num" style={{ textAlign: "right", color: check.short ? "var(--critical-ink)" : "inherit", fontWeight: check.short ? 800 : undefined }}>{check.short ? money(check.short) : "—"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function CheckPicker({ checks, value, onChange }: { checks: Check[]; value: number; onChange: (i: number) => void }) {
  return (
    <div role="tablist" aria-label="Which check" style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
      {checks.map((c, i) => (
        <button key={c.start} type="button" role="tab" aria-selected={value === i} onClick={() => onChange(i)}
          style={{ font: "inherit", fontSize: 12, fontWeight: 800, padding: "5px 10px", borderRadius: 999, border: "1px solid var(--hairline)", cursor: "pointer", minHeight: 32, background: value === i ? "var(--ink)" : "var(--surface)", color: value === i ? "var(--inverse)" : "var(--ink-2)" }}>
          {shortDate(c.start)}{c.actual ? "" : " · planned"}
        </button>
      ))}
    </div>
  );
}

export function checkTitle(c: Check): string {
  return `${shortDate(c.start)} check${c.actual ? "" : " (planned)"}`;
}
