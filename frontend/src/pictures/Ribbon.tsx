// The ribbon: one check splitting into where it goes, width in dollars. Tap a part and it splits again into
// its bills. Every band is the engine's figure; the ribbon only draws it.
import { OctagonAlert } from "lucide-react";
import { useState } from "react";
import { money, shortDate } from "../format";
import { inkOn, type Check, type Part, type PartKey } from "../paychecks";
import { CheckPicker, NoChecks, PartTip, Tip, useColours, useTip } from "./common";

const W = 820;

function band(x0: number, y0: number, x1: number, y1: number, h: number): string {
  const xm = (x0 + x1) / 2;
  return `M ${x0} ${y0} C ${xm} ${y0}, ${xm} ${y1}, ${x1} ${y1} L ${x1} ${y1 + h} C ${xm} ${y1 + h}, ${xm} ${y0 + h}, ${x0} ${y0 + h} Z`;
}

export function Ribbon({ checks, initial = 0 }: { checks: Check[]; initial?: number }) {
  const colours = useColours();
  const [i, setI] = useState(Math.min(initial, checks.length - 1));
  const [open, setOpen] = useState<PartKey | null>(null);
  const { ref, tip, show, hide } = useTip();
  const c = checks[i];
  if (!c) return <NoChecks />;
  const parts = c.parts.filter((p) => p.amount > 0.5);
  const total = Math.max(c.inflow, parts.reduce((t, p) => t + p.amount, 0));
  const H = 300;
  const padT = 26;
  const gap = 6;
  const avail = H - padT - 10 - gap * (parts.length - 1);
  const scale = avail / Math.max(1, total);
  const nodeW = 14;
  const x0 = 0;
  const x1 = open ? 330 : W - 250;
  const x2 = W - 230;
  const srcH = c.inflow * scale;
  const srcY = padT + (H - padT - 10 - srcH) / 2;
  let ySrc = srcY;
  let yT = padT;
  const opened: Part | undefined = parts.find((p) => p.key === open);
  const nodes = parts.map((p) => {
    const h = Math.max(2, p.amount * scale);
    const node = { p, h, ySrc, yT };
    ySrc += Math.min(h, Math.max(0, srcY + srcH - ySrc));
    yT += h + gap;
    return node;
  });
  const items = opened ? opened.items.filter((it) => it.amount > 0).slice(0, 14) : [];
  const itemsTotal = items.reduce((t, it) => t + it.amount, 0) || 1;
  const openNode = nodes.find((n) => n.p.key === open);
  // The opened part's bills: each slice of its band leads to a row tall enough to read.
  const itemRows: { key: string; it: (typeof items)[number]; y: number; h: number; ys: number; hs: number }[] = [];
  if (openNode) {
    let y = padT;
    let ys = openNode.yT;
    const iScale = (H - padT - 10) / itemsTotal;
    items.forEach((it, k) => {
      const hs = (openNode.h * it.amount) / itemsTotal;
      const h = Math.max(3, Math.min(it.amount * iScale, 60));
      itemRows.push({ key: `${it.source}-${k}`, it, y, h, ys, hs });
      ys += hs;
      y += Math.max(h, 16) + 4;
    });
  }
  const svgH = Math.max(H, (itemRows.length ? itemRows[itemRows.length - 1].y + 30 : 0));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <CheckPicker checks={checks} value={i} onChange={(n) => { setI(n); setOpen(null); }} />
      <div ref={ref} style={{ position: "relative", overflowX: "auto" }} onMouseLeave={hide}>
        <svg viewBox={`0 0 ${W} ${svgH}`} width="100%" role="img" aria-label={`${money(c.inflow)} into ${parts.map((p) => `${p.label} ${money(p.amount)}`).join(", ")}`} style={{ display: "block", maxWidth: 980, minWidth: 560, height: "auto" }}>
          <rect x={x0} y={srcY} width={nodeW} height={Math.max(2, srcH)} rx={3} fill="var(--ink)" />
          <text className="num" x={x0 + nodeW + 6} y={srcY - 8} fontSize="12" fontWeight="800" fill="var(--ink)">{money(c.paycheck)} check{c.carried_in > 0 ? ` + ${money(c.carried_in)} carried in` : ""}{c.borrowed ? ` + ${money(c.borrowed)} borrowed` : ""}</text>
          {nodes.map(({ p, h, ySrc: ys, yT: yt }) => {
            const colour = colours[p.key];
            const dim = open && open !== p.key;
            return (
              <g key={p.key} style={{ cursor: p.items.length ? "pointer" : "default" }} opacity={dim ? 0.35 : 1}
                onMouseMove={(e) => show(e, <PartTip part={p} colour={colour} of={c.inflow} />)}
                onClick={() => p.items.length && setOpen(open === p.key ? null : p.key)}>
                <path d={band(x0 + nodeW, ys, x1, yt, Math.min(h, Math.max(2, srcY + srcH - ys)))} fill={colour} opacity={0.45} />
                <rect x={x1} y={yt} width={nodeW} height={h} rx={3} fill={colour} />
                {!open && <text className="num" x={x1 + nodeW + 8} y={yt + h / 2 + 4} fontSize="12" fontWeight="800" fill="var(--ink)">{p.label} <tspan fontWeight="600" fill="var(--ink-2)">{money(p.amount)}</tspan></text>}
                {open && <title>{`${p.label}: ${money(p.amount)}`}</title>}
              </g>
            );
          })}
          {opened && openNode && itemRows.map((r) => (
            <g key={r.key} onMouseMove={(e) => show(e, <div><strong>{r.it.name}</strong><div className="num">{money(r.it.amount)} · {shortDate(r.it.date)}</div></div>)}>
              <path d={band(x1 + nodeW, r.ys, x2, r.y, r.hs)} fill={colours[opened.key]} opacity={0.3} />
              <rect x={x2} y={r.y} width={nodeW} height={r.h} rx={2} fill={colours[opened.key]} />
              <text className="num" x={x2 + nodeW + 6} y={r.y + r.h / 2 + 4} fontSize="11" fill="var(--ink)">{r.it.name.length > 24 ? `${r.it.name.slice(0, 23)}…` : r.it.name} <tspan fill="var(--ink-2)">{money(r.it.amount)}</tspan></text>
            </g>
          ))}
          {opened && openNode && <text className="num" x={x1 + nodeW + 6} y={openNode.yT - 6} fontSize="12" fontWeight="800" fill="var(--ink)">{opened.label} {money(opened.amount)}</text>}
        </svg>
        <Tip tip={tip} width={ref.current?.clientWidth ?? W} />
      </div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center", fontSize: 12, color: "var(--ink-2)" }}>
        {parts.map((p) => (
          <button key={p.key} type="button" onClick={() => p.items.length && setOpen(open === p.key ? null : p.key)} aria-pressed={open === p.key}
            style={{ display: "inline-flex", alignItems: "center", gap: 6, font: "inherit", fontSize: 12, padding: "4px 8px", minHeight: 32, borderRadius: 999, border: "1px solid var(--hairline)", background: open === p.key ? colours[p.key] : "var(--surface)", color: open === p.key ? inkOn(colours[p.key]) : "var(--ink-2)", cursor: p.items.length ? "pointer" : "default" }}>
            <span className="sw" style={{ background: colours[p.key], margin: 0 }} />{p.label} <strong className="num">{money(p.amount)}</strong>
          </button>
        ))}
        {c.short > 0 && <strong style={{ color: "var(--critical-ink)", display: "inline-flex", alignItems: "center", gap: 4 }}><OctagonAlert size={14} aria-hidden="true" />Short <span className="num">{money(c.short)}</span></strong>}
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>Tap a part, or its button above, to split it into its bills. The wider the band, the more dollars.</div>
    </div>
  );
}
