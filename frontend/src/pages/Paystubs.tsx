// Paystubs: the latest stub as one bar from gross to net, the last stubs as a strip, and every line with the deposit it matched.
import { useState } from "react";
import { useApi } from "../api";
import { money, shortDate } from "../format";
import { palette } from "../looks";
import { getPrefs } from "../prefs";
import type { PaystubRow } from "../types";

const TAX = /federal|social security|medicare|state|local|tax|sdi/i;

export function Paystubs() {
  const c = palette(getPrefs().look);
  const { data } = useApi<{ recent: PaystubRow[]; last: PaystubRow | null; short: PaystubRow[] }>("/api/v1/paystubs");
  const [pick, setPick] = useState<number | null>(null);
  if (!data) return <div style={{ color: "var(--muted)" }}>Loading…</div>;
  const stub = data.recent.find((p) => p.id === pick) ?? data.last;
  if (!stub) {
    return (
      <div className="card" style={{ padding: 18, maxWidth: 720 }}>
        <h1 style={{ margin: "0 0 6px", fontSize: 20, fontWeight: 800 }}>Paystubs</h1>
        <div style={{ color: "var(--ink-2)" }}>None yet. Forward a paystub PDF to a connected mailbox, or drop it into the import folder's paystubs subfolder, and it appears on Needs you.</div>
      </div>
    );
  }
  const gross = stub.gross ?? stub.net + stub.deductions.reduce((s, d) => s + d.amount, 0);
  const taxes = stub.deductions.filter((d) => TAX.test(d.name));
  const benefits = stub.deductions.filter((d) => !TAX.test(d.name));
  const segs = [...taxes.map((d) => ({ ...d, fill: c.bill })), ...benefits.map((d) => ({ ...d, fill: c.debt })), { name: "Net", amount: stub.net, fill: c.safe }];
  const W = 1100;
  const x0 = 20;
  const usable = W - 40;
  let x = x0;
  const placed = segs.map((s) => {
    const w = Math.max(2, (s.amount / Math.max(1, gross)) * usable);
    const out = { ...s, x, w };
    x += w + 2;
    return out;
  });
  const strip = [...data.recent].sort((a, b) => a.pay_date.localeCompare(b.pay_date)).slice(-6);
  const maxNet = Math.max(1, ...strip.map((p) => p.net));
  const pct = gross > 0 ? Math.round((stub.net / gross) * 1000) / 10 : 0;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>Paystub · {shortDate(stub.pay_date)}</h1>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
          {stub.period_start && stub.period_end ? `Period ${shortDate(stub.period_start)} to ${shortDate(stub.period_end)} · ` : ""}{stub.source.startsWith("mail:") ? "PDF from mail" : stub.source === "demo" ? "demo data" : stub.source}
          {stub.matched ? " · matched to the deposit" : " · no deposit matched yet"}
        </div>
        <div style={{ flexGrow: 1 }} />
        {stub.difference !== null && (
          <div style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 13, fontWeight: 700, padding: "6px 12px", borderRadius: 999, background: stub.difference < -20 ? "var(--warning-soft)" : "var(--safe-soft)", color: stub.difference < -20 ? "var(--warning-ink)" : "var(--safe-ink)" }}>
            {stub.difference < -20 ? `${money(-stub.difference)} under the usual` : stub.difference > 20 ? `${money(stub.difference)} over the usual` : "Net matches the usual"}
          </div>
        )}
      </div>

      <section className="card" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12 }}><strong>Gross to net, as one bar</strong><span style={{ fontSize: 12, color: "var(--muted)" }}>every segment is a line on the stub</span></div>
        <svg viewBox={`0 0 ${W} 120`} width="100%" style={{ display: "block", height: "auto" }} role="img" aria-label={`Gross pay of ${money(gross, { cents: true })} split into taxes, benefits and net pay of ${money(stub.net, { cents: true })}`}>
          <text x={x0} y={24} fontSize={11} fontWeight={700} fill={c.ink2}>Gross {money(gross, { cents: true })}{stub.hours ? ` · ${stub.hours} h` : ""}</text>
          {placed.map((s, i) => (
            <g key={i}>
              <rect x={s.x} y={36} width={s.w} height={36} rx={i === placed.length - 1 ? 6 : 0} fill={s.fill} />
              {s.w > 70 && <text x={s.x + 10} y={59} fontSize={12} fontWeight={800} fill="#ffffff">{s.name} {money(s.amount)}{s.name === "Net" ? ` · ${pct}% of gross` : ""}</text>}
            </g>
          ))}
          <g fontSize={11} fontWeight={700} fill={c.ink2}>
            <rect x={x0} y={96} width={10} height={10} rx={3} fill={c.bill} /><text x={x0 + 16} y={105}>Taxes {money(taxes.reduce((s, d) => s + d.amount, 0))}</text>
            <rect x={x0 + 150} y={96} width={10} height={10} rx={3} fill={c.debt} /><text x={x0 + 166} y={105}>Benefits {money(benefits.reduce((s, d) => s + d.amount, 0))}</text>
            <rect x={x0 + 320} y={96} width={10} height={10} rx={3} fill={c.safe} /><text x={x0 + 336} y={105}>Net {money(stub.net, { cents: true })}</text>
          </g>
        </svg>
      </section>

      <section className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_420px]" style={{ gap: 16, alignItems: "start" }}>
        <div className="card" style={{ padding: "16px 20px" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 8 }}><strong>Last {strip.length} paychecks, net</strong><span style={{ fontSize: 12, color: "var(--muted)" }}>tap one to open it</span></div>
          <svg viewBox="0 0 640 170" width="100%" style={{ display: "block", height: "auto" }} role="img" aria-label="Net pay for the last paychecks">
            <line x1={40} y1={140} x2={620} y2={140} stroke={c.buffer} strokeWidth={1.5} />
            {strip.map((p, i) => {
              const h = (p.net / maxNet) * 112;
              const xx = 60 + i * 96;
              const short = p.difference !== null && p.difference < -20;
              return (
                <g key={p.id} style={{ cursor: "pointer" }} onClick={() => setPick(p.id)}>
                  <rect x={xx} y={140 - h} width={24} height={h} rx={4} fill={short ? c.bill : c.paycheck} stroke={p.id === stub.id ? c.ink : "none"} strokeWidth={2} />
                  <text x={xx + 12} y={158} textAnchor="middle" fontSize={11} fontWeight={700} fill={c.ink2}>{shortDate(p.pay_date).slice(4)}</text>
                  {short && <text x={xx + 12} y={140 - h - 8} textAnchor="middle" fontSize={11} fontWeight={800} fill={c.ink}>{money(p.net)} · {money(-(p.difference ?? 0))} short</text>}
                </g>
              );
            })}
          </svg>
          {data.short.length > 0 && (
            <div style={{ marginTop: 8, padding: "12px 14px", borderRadius: 10, background: "var(--warning-soft)", fontSize: 13 }}>
              <strong>{shortDate(data.short[0].pay_date)} was {money(-(data.short[0].difference ?? 0))} under the usual.</strong> {data.short[0].hours ? `Hours were ${data.short[0].hours}.` : ""} Ask payroll before the next cycle.
            </div>
          )}
        </div>
        <div className="card" style={{ padding: "16px 20px" }}>
          <div className="lab" style={{ marginBottom: 6 }}>Every line</div>
          <table className="ledger">
            <thead><tr><th>Line</th><th style={{ textAlign: "right" }}>This stub</th></tr></thead>
            <tbody>
              <tr><td>Gross{stub.hours ? ` · ${stub.hours} h` : ""}</td><td className="num" style={{ textAlign: "right" }}>{money(gross, { cents: true })}</td></tr>
              {stub.deductions.map((d) => (
                <tr key={d.name}><td><span className="sw" style={{ background: TAX.test(d.name) ? "var(--bill)" : "var(--debt)" }} />{d.name}</td><td className="num" style={{ textAlign: "right" }}>−{money(d.amount, { cents: true })}</td></tr>
              ))}
              <tr><td><strong><span className="sw" style={{ background: "var(--safe)" }} />Net pay</strong></td><td className="num" style={{ textAlign: "right" }}><strong>{money(stub.net, { cents: true })}</strong></td></tr>
            </tbody>
          </table>
          <div style={{ marginTop: 10, fontSize: 12, color: "var(--ink-2)" }}>{stub.employer ? `${stub.employer}. ` : ""}{stub.expected_net !== null ? `Usual net ${money(stub.expected_net)} from the last stubs.` : ""} Year-to-date columns arrive with the next parser pass.</div>
        </div>
      </section>
    </div>
  );
}
