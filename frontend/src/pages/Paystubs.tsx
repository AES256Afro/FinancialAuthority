// Paystubs: the latest stub as one bar from gross to net, the last stubs as a strip, and every line with the deposit it matched.
// A stub the app kept the file of has the PDF one tap away, and its text to read.
import { useState } from "react";
import { BookOpen, Download, TriangleAlert } from "lucide-react";
import { useApi } from "../api";
import { money, monthDay, shortDate } from "../format";
import { Reader } from "../components/Documents";
import { PaycheckSetup } from "../components/PaycheckSetup";
import { Failed, Loading, useJumpToHash } from "../components/State";
import { StatusPill } from "../components/Status";
import { palette } from "../looks";
import { inkOn } from "../paychecks";
import { getPrefs } from "../prefs";
import type { PaystubWithFile } from "../types-receipts";
import { Where } from "../components/Where";


export function Paystubs() {
  const c = palette(getPrefs().look);
  const { data, error, reload } = useApi<{ recent: PaystubWithFile[]; last: PaystubWithFile | null; short: PaystubWithFile[] }>("/api/v1/paystubs");
  const [pick, setPick] = useState<number | null>(null);
  const [reading, setReading] = useState<{ id: number; page: number } | null>(null);
  // The header's "Add your paycheck" link lands on the form, with focus in it, once the page has loaded.
  useJumpToHash(data);
  if (error && !data) return <Failed error={error} what="your paystubs" onRetry={reload} />;
  if (!data) return <Loading what="your paystubs" />;
  const stub = data.recent.find((p) => p.id === pick) ?? data.last;
  if (!stub) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 960 }}>
        <div className="card" style={{ padding: 18 }}>
          <h1 style={{ margin: "0 0 6px", fontSize: 20, fontWeight: 800 }}>Paystubs</h1>
          <div style={{ color: "var(--ink-2)" }}>None yet. Upload one below, forward a paystub PDF to a connected mailbox, or drop it into the import folder's paystubs subfolder; it appears on Needs you.</div>
          <Where id="paystub" />
        </div>
        <PaycheckSetup />
      </div>
    );
  }
  const latest = data.last ?? stub; // the newest stub is offered as the paycheck when none is on file
  const offer = { id: latest.id, pay_date: latest.pay_date, net: latest.net };
  const gross = stub.gross_total;
  const taxes = stub.deductions_grouped.filter((d) => d.group === "tax");
  const benefits = stub.deductions_grouped.filter((d) => d.group === "benefit");
  const segs = [...taxes.map((d) => ({ ...d, fill: c.bill })), ...benefits.map((d) => ({ ...d, fill: c.debt })), { name: "Net", amount: stub.net, fill: c.safe }];
  const strip = [...data.recent].sort((a, b) => a.pay_date.localeCompare(b.pay_date)).slice(-6);
  const maxNet = Math.max(1, ...strip.map((p) => p.net));
  // The share of gross is the engine's; when it has none, none is shown (never "0% of gross").
  const pct = stub.net_share !== null ? `${Math.round(stub.net_share * 1000) / 10}% of gross` : null;
  const from = stub.source.startsWith("mail:") ? "a PDF from your mail" : stub.source === "demo" ? "demo data" : stub.source === "fact" ? "uploaded and confirmed on Needs you" : "kept by the app";
  const isShort = (p: PaystubWithFile) => Boolean(p.verdict?.short);
  const file = stub.document_id ?? null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>Paystubs</h1>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 16 }}>
        <h2 style={{ margin: 0, fontSize: 16, fontWeight: 800 }}>The {shortDate(stub.pay_date)} stub</h2>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
          {stub.period_start && stub.period_end ? `Period ${shortDate(stub.period_start)} to ${shortDate(stub.period_end)} · ` : ""}{from}
          {stub.matched ? " · matched to the deposit" : " · no deposit matched yet"}
        </div>
        <div style={{ flexGrow: 1 }} />
        {stub.difference !== null && (
          <StatusPill status={stub.verdict?.status ?? "good"}>
            {stub.verdict?.word ?? "Take-home pay matches the usual"}
          </StatusPill>
        )}
      </div>
      {file !== null && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <a className="btn" href={`/api/v1/documents/${file}/file`} download style={{ minHeight: 32, padding: "4px 12px", display: "inline-flex", alignItems: "center", gap: 6, textDecoration: "none" }}>
              <Download size={14} aria-hidden="true" />The PDF
            </a>
            <button type="button" className={reading?.id === file ? "btn dark" : "btn"} aria-expanded={reading?.id === file} onClick={() => setReading(reading?.id === file ? null : { id: file, page: 1 })} style={{ minHeight: 32, padding: "4px 12px", display: "inline-flex", alignItems: "center", gap: 6 }}>
              <BookOpen size={14} aria-hidden="true" />{reading?.id === file ? "Close the text" : "Read it"}
            </button>
            <span style={{ fontSize: 12, color: "var(--ink-2)" }}>The {shortDate(stub.pay_date)} stub as the app kept it.</span>
          </div>
          {reading && reading.id === file && <Reader open={reading} onMove={(page) => setReading({ id: file, page })} onClose={() => setReading(null)} />}
        </div>
      )}

      <section className="card" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}><h2 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>Gross to net, as one bar</h2><span style={{ fontSize: 12, color: "var(--muted)" }}>every segment is a line on the stub</span></div>
        <div style={{ fontSize: 12, fontWeight: 700, color: "var(--ink-2)" }} className="num">Gross {money(gross, { cents: true })}{stub.hours ? ` · ${stub.hours} h` : ""}</div>
        {/* The bar is drawn in HTML so its words stay readable at phone width; each part's width is its share of gross. */}
        <div role="img" aria-label={`Gross pay of ${money(gross, { cents: true })} split into taxes of ${money(stub.taxes_total)}, benefits of ${money(stub.benefits_total)} and net pay of ${money(stub.net, { cents: true })}`} style={{ display: "flex", gap: 2, height: 36 }}>
          {segs.map((s, i) => {
            const share = s.amount / Math.max(1, gross);
            return (
              <div key={i} title={`${s.name} ${money(s.amount, { cents: true })}`} style={{ flex: `${Math.max(0.002, share)} 1 0`, minWidth: 2, background: s.fill, color: inkOn(s.fill), borderRadius: i === segs.length - 1 ? "0 6px 6px 0" : 0, overflow: "hidden", whiteSpace: "nowrap", textOverflow: "ellipsis", fontSize: 12, fontWeight: 800, display: "flex", alignItems: "center", padding: share > 0.12 ? "0 10px" : 0 }}>
                {share > 0.12 ? <span className="num" style={{ overflow: "hidden", textOverflow: "ellipsis", minWidth: 0 }}>{s.name} {money(s.amount)}{s.name === "Net" && pct ? ` · ${pct}` : ""}</span> : null}
              </div>
            );
          })}
        </div>
        <div className="num" style={{ display: "flex", gap: 16, flexWrap: "wrap", fontSize: 12, fontWeight: 700, color: "var(--ink-2)" }}>
          <span><span className="sw" style={{ background: c.bill }} />Taxes {money(stub.taxes_total)}</span>
          <span><span className="sw" style={{ background: c.debt }} />Benefits {money(stub.benefits_total)}</span>
          <span><span className="sw" style={{ background: c.safe }} />Net {money(stub.net, { cents: true })}{pct ? ` · ${pct}` : ""}</span>
        </div>
      </section>

      <PaycheckSetup stub={offer} />

      <section className="grid grid-cols-1 xl:grid-cols-[minmax(0,1fr)_420px]" style={{ gap: 16, alignItems: "start" }}>
        <div className="card" style={{ padding: "16px 20px" }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, marginBottom: 8, flexWrap: "wrap" }}><h2 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>Last {strip.length} paychecks, net</h2><span style={{ fontSize: 12, color: "var(--muted)" }}>pick a date below to open that stub</span></div>
          <svg viewBox="0 0 640 170" width="100%" style={{ display: "block", height: "auto" }} role="img" aria-label="Net pay for the last paychecks">
            <line x1={40} y1={140} x2={620} y2={140} stroke={c.buffer} strokeWidth={1.5} />
            {strip.map((p, i) => {
              const h = (p.net / maxNet) * 112;
              const xx = 60 + i * 96;
              const short = isShort(p);
              return (
                <g key={p.id} style={{ cursor: "pointer" }} onClick={() => setPick(p.id)}>
                  <title>{`${shortDate(p.pay_date)}: ${money(p.net)}${short ? `, ${money(-(p.difference ?? 0))} under the usual` : ""}`}</title>
                  <rect x={xx} y={140 - h} width={24} height={h} rx={4} fill={short ? c.bill : c.paycheck} stroke={p.id === stub.id ? c.ink : "none"} strokeWidth={2} />
                  <text x={xx + 12} y={158} textAnchor="middle" fontSize={11} fontWeight={700} fill={c.ink2}>{monthDay(p.pay_date)}</text>
                  {short && <text className="num" x={xx + 12} y={140 - h - 8} textAnchor="middle" fontSize={11} fontWeight={800} fill={c.ink}>{money(p.net)} · {money(-(p.difference ?? 0))} short</text>}
                </g>
              );
            })}
          </svg>
          <div role="group" aria-label="Open a paystub" style={{ display: "flex", gap: 6, flexWrap: "wrap", marginTop: 8 }}>
            {strip.map((p) => (
              <button key={p.id} type="button" className={`btn${p.id === stub.id ? " dark" : ""}`} aria-pressed={p.id === stub.id} onClick={() => setPick(p.id)} style={{ minHeight: 36, padding: "4px 10px", fontSize: 12 }}>
                {shortDate(p.pay_date)} · <span className="num">{money(p.net)}</span>{isShort(p) ? " · under the usual" : ""}
              </button>
            ))}
          </div>
          {data.short.length > 0 && (
            <div style={{ marginTop: 8, padding: "12px 14px", borderRadius: 10, background: "var(--warning-soft)", color: "var(--warning-ink)", fontSize: 13, display: "flex", gap: 8, alignItems: "flex-start" }}>
              <TriangleAlert size={16} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} />
              <span><strong>{shortDate(data.short[0].pay_date)} was <span className="num">{money(-(data.short[0].difference ?? 0))}</span> under the usual.</strong> {data.short[0].hours ? `Hours were ${data.short[0].hours}.` : ""} Ask payroll before the next payday.</span>
            </div>
          )}
        </div>
        <div className="card" style={{ padding: "16px 20px" }}>
          <h2 className="lab" style={{ margin: "0 0 6px" }}>Every line</h2>
          <div style={{ overflowX: "auto" }}>
            <table className="ledger">
              <thead><tr><th>Line</th><th style={{ textAlign: "right" }}>This stub</th></tr></thead>
              <tbody>
                <tr><td>Gross{stub.hours ? ` · ${stub.hours} h` : ""}</td><td className="num" style={{ textAlign: "right" }}>{money(gross, { cents: true })}</td></tr>
                {stub.deductions_grouped.map((d) => (
                  <tr key={d.name}><td><span className="sw" style={{ background: d.group === "tax" ? "var(--bill)" : "var(--debt)" }} />{d.name} <span style={{ color: "var(--muted)", fontSize: 11 }}>{d.group === "tax" ? "tax" : "benefit"}</span></td><td className="num" style={{ textAlign: "right" }}>−{money(d.amount, { cents: true })}</td></tr>
                ))}
                <tr><td><strong><span className="sw" style={{ background: "var(--safe)" }} />Net pay</strong></td><td className="num" style={{ textAlign: "right" }}><strong>{money(stub.net, { cents: true })}</strong></td></tr>
              </tbody>
            </table>
          </div>
          <div style={{ marginTop: 10, fontSize: 12, color: "var(--ink-2)" }}>{stub.employer ? `${stub.employer}. ` : ""}{stub.expected_net !== null ? <>Usual take-home pay <span className="num">{money(stub.expected_net)}</span>, from the last stubs.</> : ""}</div>
        </div>
      </section>
    </div>
  );
}
