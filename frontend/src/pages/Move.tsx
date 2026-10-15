// The move: a what-if over the real registers. The verdict for a date, the first date that passes, the honest
// line with and without the move, each check's load with it, what has to be true first, and saved scenarios
// to compare dates. Every figure is the engine's; this page draws it and says it is not a recommendation.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { money, shortDate } from "../format";
import { palette } from "../looks";
import { getPrefs } from "../prefs";

interface OneOff {
  name: string;
  amount: string;
  date: string;
}

interface Recurring {
  name: string;
  amount: string;
  due_day: string;
}

interface PathPoint {
  date: string;
  at_pace: number;
}

interface Result {
  name: string;
  move_date: string;
  verdict: "passes" | "short";
  summary: string;
  first_passes: string | null;
  baseline: { low: { date: string; value: number }; path: PathPoint[] };
  scenario: { low: { date: string; value: number }; path: PathPoint[]; shortfall: number; short_days: number };
  windows: { start: string; end: string; paycheck: number; total: number; baseline_total: number; delta: number; before_payday?: boolean; items: { name: string; amount: number; date: string; scenario?: string; kind: string }[] }[];
  monthly_change: number;
  one_offs_total: number;
  old_rent: number;
  new_rent: number;
  buffer: number;
  cash: number;
  caveats: string[];
  not_advice: string;
}

interface ReadinessItem {
  key: string;
  label: string;
  value: string | number;
  target: string | number;
  met: boolean;
  projected?: string | null;
  note: string;
  link: string;
}

interface Readiness {
  items: ReadinessItem[];
  met: number;
  of: number;
  ready: boolean;
}

interface Saved {
  id: number;
  name: string;
  spec: { move_date: string; new_rent: number | null; overlap_months: number; one_offs: { name: string; amount: number; date: string }[]; recurring: { name: string; amount: number; due_day: number | null }[] };
}

function Chart({ r }: { r: Result }) {
  const c = palette(getPrefs().look);
  const w = 760;
  const h = 220;
  const left = 46;
  const right = 12;
  const top = 14;
  const base = h - 28;
  const n = Math.max(2, r.scenario.path.length);
  const all = [...r.baseline.path, ...r.scenario.path].map((p) => p.at_pace);
  const hi = Math.max(1, ...all);
  const lo = Math.min(0, ...all);
  const x = (i: number) => left + (i * (w - left - right)) / (n - 1);
  const y = (v: number) => base - ((v - lo) / (hi - lo)) * (base - top);
  const line = (path: PathPoint[]) => path.map((p, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(p.at_pace).toFixed(1)}`).join(" ");
  const moveIdx = r.scenario.path.findIndex((p) => p.date >= r.move_date);
  const lowIdx = r.scenario.path.findIndex((p) => p.date === r.scenario.low.date);
  const ticks = [0, Math.floor(n / 4), Math.floor(n / 2), Math.floor((3 * n) / 4), n - 1];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" role="img" aria-label={`The honest line at the usual pace, with and without the move, over ${n} days`} style={{ display: "block", maxWidth: 900 }}>
      <line x1={left} x2={w - right} y1={y(0)} y2={y(0)} stroke={c.ink} strokeWidth={1.2} />
      <text x={left - 6} y={y(0) + 4} textAnchor="end" fontSize={10} fill={c.ink2}>0</text>
      <text x={left - 6} y={y(hi) + 4} textAnchor="end" fontSize={10} fill={c.ink2}>{money(hi)}</text>
      {lo < 0 && <text x={left - 6} y={y(lo) + 4} textAnchor="end" fontSize={10} fill={c.ink2}>{money(lo)}</text>}
      <path d={line(r.baseline.path)} fill="none" stroke={c.buffer} strokeWidth={2} strokeDasharray="5 4" />
      <path d={line(r.scenario.path)} fill="none" stroke={r.verdict === "passes" ? c.safe : c.bill} strokeWidth={2.4} />
      {moveIdx >= 0 && (
        <g>
          <line x1={x(moveIdx)} x2={x(moveIdx)} y1={top} y2={base} stroke={c.paycheck} strokeWidth={1.5} strokeDasharray="3 3" />
          <text x={x(moveIdx) + 4} y={top + 10} fontSize={11} fontWeight={800} fill={c.paycheck}>move {shortDate(r.move_date)}</text>
        </g>
      )}
      {lowIdx >= 0 && (
        <g>
          <circle cx={x(lowIdx)} cy={y(r.scenario.low.value)} r={5} fill={r.verdict === "passes" ? c.safe : c.bill} stroke={c.surface} strokeWidth={2} />
          <text x={Math.min(x(lowIdx) + 8, w - 150)} y={y(r.scenario.low.value) - 8} fontSize={11} fontWeight={800} fill={c.ink}>low {money(r.scenario.low.value)} · {shortDate(r.scenario.low.date)}</text>
        </g>
      )}
      {ticks.map((i) => <text key={i} x={x(i)} y={h - 8} textAnchor={i === 0 ? "start" : i === n - 1 ? "end" : "middle"} fontSize={10} fill={c.ink2}>{shortDate(r.scenario.path[i]?.date ?? r.move_date)}</text>)}
      <g fontSize={11} fontWeight={700} fill={c.ink2}>
        <line x1={w - 250} x2={w - 226} y1={top + 4} y2={top + 4} stroke={c.buffer} strokeWidth={2} strokeDasharray="5 4" /><text x={w - 220} y={top + 8}>as things are</text>
        <line x1={w - 130} x2={w - 106} y1={top + 4} y2={top + 4} stroke={r.verdict === "passes" ? c.safe : c.bill} strokeWidth={2.4} /><text x={w - 100} y={top + 8}>with the move</text>
      </g>
    </svg>
  );
}

export function Move() {
  const [name, setName] = useState("Voyager");
  const [moveDate, setMoveDate] = useState(() => { const d = new Date(); d.setDate(d.getDate() + 45); return d.toISOString().slice(0, 10); });
  const [newRent, setNewRent] = useState("");
  const [overlap, setOverlap] = useState("0");
  const [oneOffs, setOneOffs] = useState<OneOff[]>([{ name: "Deposit", amount: "", date: "" }, { name: "Application and admin fees", amount: "", date: "" }, { name: "Truck and help", amount: "", date: "" }]);
  const [recurring, setRecurring] = useState<Recurring[]>([{ name: "Renter's insurance", amount: "", due_day: "1" }]);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data: saved, reload: reloadSaved } = useApi<{ items: Saved[] }>("/api/v1/scenarios");
  const [ready, setReady] = useState<Readiness | null>(null);

  function spec() {
    return {
      name,
      move_date: moveDate,
      new_rent: newRent ? Number(newRent) : undefined,
      overlap_months: Number(overlap) || 0,
      one_offs: oneOffs.filter((o) => o.amount).map((o) => ({ name: o.name || "One-off", amount: Number(o.amount), date: o.date || moveDate })),
      recurring: recurring.filter((r) => r.amount).map((r) => ({ name: r.name || "Added bill", amount: Number(r.amount), due_day: r.due_day ? Number(r.due_day) : undefined })),
    };
  }
  async function run() {
    setBusy(true);
    setError(null);
    try {
      const r = await api<Result>("/api/v1/scenarios/run", { method: "POST", body: JSON.stringify(spec()) });
      setResult(r);
      const q = new URLSearchParams({ move_date: moveDate, ...(newRent ? { new_rent: newRent } : {}) });
      setReady(await api<Readiness>(`/api/v1/scenarios/readiness?${q.toString()}`));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    await api("/api/v1/scenarios", { method: "POST", body: JSON.stringify(spec()) });
    reloadSaved();
  }
  function load(s: Saved) {
    setName(s.name);
    setMoveDate(s.spec.move_date);
    setNewRent(s.spec.new_rent !== null && s.spec.new_rent !== undefined ? String(s.spec.new_rent) : "");
    setOverlap(String(s.spec.overlap_months ?? 0));
    setOneOffs(s.spec.one_offs.length ? s.spec.one_offs.map((o) => ({ name: o.name, amount: String(o.amount), date: o.date })) : [{ name: "Deposit", amount: "", date: "" }]);
    setRecurring(s.spec.recurring.length ? s.spec.recurring.map((r) => ({ name: r.name, amount: String(r.amount), due_day: r.due_day ? String(r.due_day) : "" })) : [{ name: "Renter's insurance", amount: "", due_day: "1" }]);
  }
  useEffect(() => {
    const q = new URLSearchParams({ move_date: moveDate });
    api<Readiness>(`/api/v1/scenarios/readiness?${q.toString()}`).then(setReady).catch(() => undefined);
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: 12 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>The move</h1>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>a what-if over your real checks and bills; nothing on the registers changes</div>
      </div>

      {result && (
        <section className="card" style={{ padding: "18px 22px", borderTop: `5px solid ${result.verdict === "passes" ? "var(--safe)" : "var(--critical)"}`, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 14, flexWrap: "wrap" }}>
            <div style={{ fontSize: 26, fontWeight: 800 }}>{result.verdict === "passes" ? "Holds" : "Short"} · {result.name} on {shortDate(result.move_date)}</div>
            {result.verdict === "short" && result.first_passes && <div style={{ fontSize: 14, fontWeight: 700, color: "var(--safe-ink)" }}>first date that holds: {shortDate(result.first_passes)}</div>}
          </div>
          <div style={{ fontSize: 14 }}>{result.summary}</div>
          <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 13, color: "var(--ink-2)" }}>
            <span>monthly change <strong className="num" style={{ color: result.monthly_change > 0 ? "var(--critical)" : "var(--safe-ink)" }}>{result.monthly_change >= 0 ? "+" : ""}{money(result.monthly_change)}</strong></span>
            <span>one-offs <strong className="num">{money(result.one_offs_total)}</strong></span>
            <span>rent {money(result.old_rent)} → {money(result.new_rent)}</span>
            <span>low point without the move {money(result.baseline.low.value)} on {shortDate(result.baseline.low.date)}</span>
          </div>
          <Chart r={result} />
          {result.caveats.length > 0 && <div style={{ fontSize: 12, color: "var(--warning)", fontWeight: 600 }}>{result.caveats.join(" ")}</div>}
        </section>
      )}

      <section className="card" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}><strong>The scenario</strong><span style={{ fontSize: 12, color: "var(--muted)" }}>the apartment's own figures; leave blank what you do not know yet</span></div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} style={{ width: 140 }} /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Move on</span><input className="input" type="date" value={moveDate} onChange={(e) => setMoveDate(e.target.value)} /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">New rent a month</span><input className="input num" value={newRent} onChange={(e) => setNewRent(e.target.value)} inputMode="decimal" style={{ width: 110 }} placeholder="same" /></label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Old rent still paid</span>
            <select className="input" value={overlap} onChange={(e) => setOverlap(e.target.value)}><option value="0">no overlap</option><option value="1">one more month</option><option value="2">two more months</option></select>
          </label>
        </div>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 300px), 1fr))", gap: 12 }}>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span className="lab">One-off costs</span>
            {oneOffs.map((o, i) => (
              <div key={i} style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <input className="input" value={o.name} onChange={(e) => setOneOffs(oneOffs.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} style={{ flex: "1 1 120px" }} placeholder="what" />
                <input className="input num" value={o.amount} onChange={(e) => setOneOffs(oneOffs.map((x, j) => (j === i ? { ...x, amount: e.target.value } : x)))} inputMode="decimal" style={{ width: 90 }} placeholder="amount" />
                <input className="input" type="date" value={o.date} onChange={(e) => setOneOffs(oneOffs.map((x, j) => (j === i ? { ...x, date: e.target.value } : x)))} style={{ width: 150 }} title="blank means the move date" />
              </div>
            ))}
            <button className="btn" type="button" onClick={() => setOneOffs([...oneOffs, { name: "", amount: "", date: "" }])} style={{ alignSelf: "flex-start" }}>Another one-off</button>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span className="lab">Bills that start with the move (a month)</span>
            {recurring.map((r, i) => (
              <div key={i} style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                <input className="input" value={r.name} onChange={(e) => setRecurring(recurring.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} style={{ flex: "1 1 120px" }} placeholder="what" />
                <input className="input num" value={r.amount} onChange={(e) => setRecurring(recurring.map((x, j) => (j === i ? { ...x, amount: e.target.value } : x)))} inputMode="decimal" style={{ width: 90 }} placeholder="amount" />
                <input className="input num" value={r.due_day} onChange={(e) => setRecurring(recurring.map((x, j) => (j === i ? { ...x, due_day: e.target.value } : x)))} inputMode="numeric" style={{ width: 64 }} placeholder="day" />
              </div>
            ))}
            <button className="btn" type="button" onClick={() => setRecurring([...recurring, { name: "", amount: "", due_day: "" }])} style={{ alignSelf: "flex-start" }}>Another bill</button>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <button className="btn dark" type="button" disabled={busy} onClick={run}>{busy ? "Running…" : "Run it against my checks"}</button>
          <button className="btn" type="button" disabled={busy} onClick={save}>Save this scenario</button>
          {saved && saved.items.length > 0 && (
            <span style={{ fontSize: 12, color: "var(--ink-2)" }}>
              Saved: {saved.items.map((s) => <button key={s.id} className="btn" type="button" onClick={() => load(s)} style={{ marginLeft: 6, padding: "2px 8px", minHeight: 28 }}>{s.name} · {shortDate(s.spec.move_date)}</button>)}
            </span>
          )}
        </div>
        {error && <div style={{ fontSize: 13, color: "var(--critical)" }}>{error}</div>}
      </section>

      {result && (
        <section className="card" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}><strong>Each check with the move</strong><span style={{ fontSize: 12, color: "var(--muted)" }}>what the window carries, and how much more than today</span></div>
          <table className="ledger" style={{ fontSize: 13 }}>
            <thead><tr><th>Check</th><th style={{ textAlign: "right" }}>Lands</th><th style={{ textAlign: "right" }}>Carries</th><th style={{ textAlign: "right" }}>Change</th><th>Biggest lines</th></tr></thead>
            <tbody>
              {result.windows.slice(0, 9).map((w) => (
                <tr key={w.start} style={{ background: w.delta > 0 ? "var(--warning-soft)" : undefined }}>
                  <td>{w.before_payday ? "Before the next check" : shortDate(w.start)}<div style={{ fontSize: 11, color: "var(--muted)" }}>through {shortDate(w.end)}</div></td>
                  <td className="num" style={{ textAlign: "right" }}>{w.before_payday ? money(result.cash) : money(w.paycheck)}</td>
                  <td className="num" style={{ textAlign: "right" }}>{money(w.total)}</td>
                  <td className="num" style={{ textAlign: "right", fontWeight: 800, color: w.delta > 0 ? "var(--critical)" : w.delta < 0 ? "var(--safe-ink)" : "var(--muted)" }}>{w.delta === 0 ? "—" : `${w.delta > 0 ? "+" : ""}${money(w.delta)}`}</td>
                  <td style={{ fontSize: 12 }}>{w.items.slice(0, 4).map((i) => `${i.name} ${money(i.amount)}${i.scenario ? " ·new" : ""}`).join(" · ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </section>
      )}

      {ready && (
        <section className="card" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
            <strong>What has to be true first</strong>
            <span style={{ fontSize: 12, color: ready.ready ? "var(--safe-ink)" : "var(--ink-2)", fontWeight: 700 }}>{ready.met} of {ready.of} met</span>
          </div>
          {ready.items.map((i) => (
            <div key={i.key} style={{ display: "grid", gridTemplateColumns: "22px minmax(0, 1fr) auto", gap: 10, alignItems: "start", fontSize: 13, borderTop: "1px solid var(--surface-2)", paddingTop: 6 }}>
              <span aria-label={i.met ? "met" : "not yet"} style={{ width: 16, height: 16, borderRadius: 4, background: i.met ? "var(--safe)" : "var(--surface-2)", border: i.met ? "none" : "2px solid var(--warning)", marginTop: 2 }} />
              <div style={{ minWidth: 0 }}>
                <div><strong>{i.label}</strong> <span style={{ color: "var(--ink-2)" }}>· {typeof i.value === "number" ? money(i.value) : i.value} · target {typeof i.target === "number" ? money(i.target) : i.target}{i.projected && !i.met ? ` · on track for ${shortDate(i.projected)}` : ""}</span></div>
                {i.note && <div style={{ fontSize: 12, color: "var(--muted)" }}>{i.note}</div>}
              </div>
              <Link to={i.link} className="btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", minHeight: 30, padding: "2px 10px", fontSize: 12, whiteSpace: "nowrap" }}>{i.key === "reconciled" && !i.met ? "Mark checked" : "Go →"}</Link>
            </div>
          ))}
          {!ready.items.find((i) => i.key === "reconciled")?.met && (
            <button className="btn" type="button" style={{ alignSelf: "flex-start" }} onClick={async () => { await api("/api/v1/scenarios/reconciled", { method: "POST" }); const q = new URLSearchParams({ move_date: moveDate }); setReady(await api<Readiness>(`/api/v1/scenarios/readiness?${q.toString()}`)); }}>
              I checked a paycheck window by hand and it matched the bank
            </button>
          )}
        </section>
      )}
      {result && <div style={{ fontSize: 11, color: "var(--muted)" }}>{result.not_advice}</div>}
    </div>
  );
}
