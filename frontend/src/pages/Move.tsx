// Moving home: a what-if over your real bills and paychecks. The verdict for a date, the first date that fits,
// your balance day by day with and without the move, each paycheck's load with it, what has to be true first,
// and saved scenarios to compare dates. Every figure is the engine's; this page draws it and says it is not a
// recommendation.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { TriangleAlert } from "lucide-react";
import { api, useApi } from "../api";
import { Failed, Said } from "../components/State";
import { StatusPill } from "../components/Status";
import { localToday, money, parseIso, parseMoney, shortDate } from "../format";
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

/** A date some days after today, as YYYY-MM-DD in local time. */
function daysFromToday(days: number): string {
  const d = parseIso(localToday());
  d.setDate(d.getDate() + days);
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
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
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" role="img" aria-label={`Your balance day by day at your usual spending, with and without the move, over ${n} days`} style={{ display: "block", maxWidth: 900 }}>
      <line x1={left} x2={w - right} y1={y(0)} y2={y(0)} stroke={c.ink} strokeWidth={1.2} />
      <text x={left - 6} y={y(0) + 4} textAnchor="end" fontSize={10} fill={c.ink2}>0</text>
      <text x={left - 6} y={y(hi) + 4} textAnchor="end" fontSize={10} fill={c.ink2}>{money(hi)}</text>
      {lo < 0 && <text x={left - 6} y={y(lo) + 4} textAnchor="end" fontSize={10} fill={c.ink2}>{money(lo)}</text>}
      <path d={line(r.baseline.path)} fill="none" stroke={c.buffer} strokeWidth={2} strokeDasharray="5 4" />
      <path d={line(r.scenario.path)} fill="none" stroke={r.verdict === "passes" ? c.safe : c.bill} strokeWidth={2.4} />
      {moveIdx >= 0 && (
        <g>
          <line x1={x(moveIdx)} x2={x(moveIdx)} y1={top} y2={base} stroke={c.paycheck} strokeWidth={1.5} strokeDasharray="3 3" />
          <text x={x(moveIdx) + 4} y={top + 10} fontSize={11} fontWeight={800} fill="var(--paycheck-ink)">move {shortDate(r.move_date)}</text>
        </g>
      )}
      {lowIdx >= 0 && (
        <g>
          <circle cx={x(lowIdx)} cy={y(r.scenario.low.value)} r={5} fill={r.verdict === "passes" ? c.safe : c.bill} stroke={c.surface} strokeWidth={2} />
          <text x={Math.min(x(lowIdx) + 8, w - 150)} y={y(r.scenario.low.value) - 8} fontSize={11} fontWeight={800} fill={c.ink}>lowest {money(r.scenario.low.value)} · {shortDate(r.scenario.low.date)}</text>
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
  const [name, setName] = useState("New place");
  const [moveDate, setMoveDate] = useState(() => daysFromToday(45));
  const [newRent, setNewRent] = useState("");
  const [overlap, setOverlap] = useState("0");
  const [oneOffs, setOneOffs] = useState<OneOff[]>([{ name: "Deposit", amount: "", date: "" }, { name: "Application and admin fees", amount: "", date: "" }, { name: "Truck and help", amount: "", date: "" }]);
  const [recurring, setRecurring] = useState<Recurring[]>([{ name: "Renter's insurance", amount: "", due_day: "1" }]);
  const [result, setResult] = useState<Result | null>(null);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const { data: saved, reload: reloadSaved } = useApi<{ items: Saved[] }>("/api/v1/scenarios");
  const [ready, setReady] = useState<Readiness | null>(null);
  const [readyError, setReadyError] = useState<string | null>(null);
  const [savedNote, setSavedNote] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const [checkedNote, setCheckedNote] = useState<string | null>(null);

  /** The scenario as the engine reads it, or null (with the reason said) when an amount cannot be read. */
  function spec() {
    const typed = [newRent, ...oneOffs.map((o) => o.amount), ...recurring.map((r) => r.amount)].filter((t) => t.trim());
    if (typed.some((t) => parseMoney(t) === null)) {
      setError("Type an amount like 120 or 1200.50");
      return null;
    }
    return {
      name,
      move_date: moveDate,
      new_rent: newRent.trim() ? parseMoney(newRent) ?? undefined : undefined,
      overlap_months: Number(overlap) || 0,
      one_offs: oneOffs.filter((o) => o.amount.trim()).map((o) => ({ name: o.name || "One-off", amount: parseMoney(o.amount) as number, date: o.date || moveDate })),
      recurring: recurring.filter((r) => r.amount.trim()).map((r) => ({ name: r.name || "Added bill", amount: parseMoney(r.amount) as number, due_day: r.due_day ? Number(r.due_day) : undefined })),
    };
  }
  async function loadReadiness(q: URLSearchParams) {
    try {
      setReady(await api<Readiness>(`/api/v1/scenarios/readiness?${q.toString()}`));
      setReadyError(null);
    } catch (e) {
      setReadyError((e as Error).message);
    }
  }
  async function run() {
    setError(null);
    const body = spec();
    if (!body) return;
    setBusy(true);
    try {
      const r = await api<Result>("/api/v1/scenarios/run", { method: "POST", body: JSON.stringify(body) });
      setResult(r);
      const q = new URLSearchParams({ move_date: moveDate, ...(body.new_rent !== undefined ? { new_rent: String(body.new_rent) } : {}) });
      await loadReadiness(q);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }
  async function save() {
    setError(null);
    setSavedNote(null);
    const body = spec();
    if (!body) return;
    try {
      await api("/api/v1/scenarios", { method: "POST", body: JSON.stringify(body) });
      setSavedNote({ tone: "info", text: `Saved as "${name}". It is listed under Saved.` });
      reloadSaved();
    } catch (e) {
      setSavedNote({ tone: "error", text: `Couldn't save it: ${(e as Error).message}` });
    }
  }
  async function markChecked() {
    setCheckedNote(null);
    try {
      await api("/api/v1/scenarios/reconciled", { method: "POST" });
      await loadReadiness(new URLSearchParams({ move_date: moveDate }));
    } catch (e) {
      setCheckedNote(`Couldn't save that: ${(e as Error).message}`);
    }
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
    loadReadiness(new URLSearchParams({ move_date: moveDate }));
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <div style={{ display: "flex", flexWrap: "wrap", alignItems: "baseline", gap: 12 }}>
        <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800, letterSpacing: "-0.01em" }}>Moving home</h1>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>a what-if over your real paychecks and bills; nothing in your bills list or transactions changes</div>
      </div>

      {result && (
        <section className="card" style={{ padding: "18px 22px", borderTop: `5px solid ${result.verdict === "passes" ? "var(--safe)" : "var(--critical)"}`, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 14, flexWrap: "wrap" }}>
            <div style={{ fontSize: 26, fontWeight: 800 }}>{result.verdict === "passes" ? "Fits" : "Short"} · {result.name} on {shortDate(result.move_date)}</div>
            {result.verdict === "short" && result.first_passes && <div style={{ fontSize: 14, fontWeight: 700, color: "var(--safe-ink)" }}>first date that fits: {shortDate(result.first_passes)}</div>}
          </div>
          <div style={{ fontSize: 14 }}>{result.summary}</div>
          <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 13, color: "var(--ink-2)" }}>
            <span>monthly change <strong className="num" style={{ color: result.monthly_change > 0 ? "var(--critical-ink)" : "var(--safe-ink)" }}>{result.monthly_change >= 0 ? "+" : ""}{money(result.monthly_change)}</strong></span>
            <span>one-offs <strong className="num">{money(result.one_offs_total)}</strong></span>
            <span>rent {money(result.old_rent)} → {money(result.new_rent)}</span>
            <span>lowest balance with the move {money(result.scenario.low.value)} on {shortDate(result.scenario.low.date)}</span>
            <span>lowest balance without the move {money(result.baseline.low.value)} on {shortDate(result.baseline.low.date)}</span>
          </div>
          <Chart r={result} />
          {result.caveats.length > 0 && <div style={{ fontSize: 12, color: "var(--warning-ink)", fontWeight: 600, display: "flex", gap: 6, alignItems: "flex-start" }}><TriangleAlert size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} /><span>{result.caveats.join(" ")}</span></div>}
        </section>
      )}

      <section className="card" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}><h2 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>The scenario</h2><span style={{ fontSize: 12, color: "var(--muted)" }}>the new place's own figures; leave blank what you do not know yet</span></div>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Name</span><input className="input" value={name} onChange={(e) => setName(e.target.value)} style={{ width: "min(100%, 160px)" }} /></label>
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
              <div key={i} style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "flex-end" }}>
                <label style={{ display: "flex", flexDirection: "column", gap: 2, flex: "1 1 120px" }}><span className="lab">What</span><input className="input" value={o.name} onChange={(e) => setOneOffs(oneOffs.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} /></label>
                <label style={{ display: "flex", flexDirection: "column", gap: 2 }}><span className="lab">Amount</span><input className="input num" value={o.amount} onChange={(e) => setOneOffs(oneOffs.map((x, j) => (j === i ? { ...x, amount: e.target.value } : x)))} inputMode="decimal" style={{ width: 90 }} /></label>
                <label style={{ display: "flex", flexDirection: "column", gap: 2 }}><span className="lab">Date (blank: move day)</span><input className="input" type="date" value={o.date} onChange={(e) => setOneOffs(oneOffs.map((x, j) => (j === i ? { ...x, date: e.target.value } : x)))} style={{ width: 150 }} /></label>
              </div>
            ))}
            <button className="btn" type="button" onClick={() => setOneOffs([...oneOffs, { name: "", amount: "", date: "" }])} style={{ alignSelf: "flex-start" }}>Another one-off</button>
          </div>
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            <span className="lab">Bills that start with the move (a month)</span>
            {recurring.map((r, i) => (
              <div key={i} style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "flex-end" }}>
                <label style={{ display: "flex", flexDirection: "column", gap: 2, flex: "1 1 120px" }}><span className="lab">What</span><input className="input" value={r.name} onChange={(e) => setRecurring(recurring.map((x, j) => (j === i ? { ...x, name: e.target.value } : x)))} /></label>
                <label style={{ display: "flex", flexDirection: "column", gap: 2 }}><span className="lab">Amount</span><input className="input num" value={r.amount} onChange={(e) => setRecurring(recurring.map((x, j) => (j === i ? { ...x, amount: e.target.value } : x)))} inputMode="decimal" style={{ width: 90 }} /></label>
                <label style={{ display: "flex", flexDirection: "column", gap: 2 }}><span className="lab">Due day</span><input className="input num" value={r.due_day} onChange={(e) => setRecurring(recurring.map((x, j) => (j === i ? { ...x, due_day: e.target.value } : x)))} inputMode="numeric" style={{ width: 64 }} /></label>
              </div>
            ))}
            <button className="btn" type="button" onClick={() => setRecurring([...recurring, { name: "", amount: "", due_day: "" }])} style={{ alignSelf: "flex-start" }}>Another bill</button>
          </div>
        </div>
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <button className="btn dark" type="button" disabled={busy} onClick={run}>{busy ? "Running…" : "Run it against my checks"}</button>
          <button className="btn" type="button" disabled={busy} onClick={save}>Save this scenario</button>
          {savedNote && <Said text={savedNote.text} tone={savedNote.tone} />}
          {saved && saved.items.length > 0 && (
            <span style={{ fontSize: 12, color: "var(--ink-2)", display: "inline-flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
              Saved: {saved.items.map((s) => <button key={s.id} className="btn" type="button" onClick={() => load(s)} style={{ padding: "4px 10px", minHeight: 32 }}>{s.name} · {shortDate(s.spec.move_date)}</button>)}
            </span>
          )}
        </div>
        <Said text={error} tone="error" />
      </section>

      {result && (
        <section className="card" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}><h2 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>Each paycheck with the move</h2><span style={{ fontSize: 12, color: "var(--muted)" }}>what each paycheck has to pay, and how much more than today</span></div>
          <div style={{ overflowX: "auto" }}>
          <table className="ledger" style={{ fontSize: 13 }}>
            <thead><tr><th>Paycheck</th><th style={{ textAlign: "right" }}>Brings in</th><th style={{ textAlign: "right" }}>Has to pay</th><th style={{ textAlign: "right" }}>Change</th><th>Biggest bills</th></tr></thead>
            <tbody>
              {result.windows.slice(0, 9).map((w) => (
                <tr key={w.start} style={{ background: w.delta > 0 ? "var(--warning-soft)" : undefined }}>
                  <td>{w.before_payday ? "Before the next paycheck" : shortDate(w.start)}<div style={{ fontSize: 11, color: "var(--muted)" }}>through {shortDate(w.end)}</div></td>
                  <td className="num" style={{ textAlign: "right" }}>{w.before_payday ? money(result.cash) : money(w.paycheck)}</td>
                  <td className="num" style={{ textAlign: "right" }}>{money(w.total)}</td>
                  <td className="num" style={{ textAlign: "right", fontWeight: 800, color: w.delta > 0 ? "var(--critical-ink)" : w.delta < 0 ? "var(--safe-ink)" : "var(--muted)" }}>{w.delta === 0 ? "—" : `${w.delta > 0 ? "+" : ""}${money(w.delta)}`}</td>
                  <td style={{ fontSize: 12 }}>{w.items.slice(0, 4).map((i) => `${i.name} ${money(i.amount)}${i.scenario ? " (new)" : ""}`).join(" · ")}</td>
                </tr>
              ))}
            </tbody>
          </table>
          </div>
        </section>
      )}

      {readyError && !ready && <Failed error={readyError} what="what has to be true first" onRetry={() => loadReadiness(new URLSearchParams({ move_date: moveDate }))} />}
      {ready && (
        <section className="card" style={{ padding: "16px 20px", display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
            <h2 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>What has to be true first</h2>
            <span style={{ fontSize: 12, color: ready.ready ? "var(--safe-ink)" : "var(--ink-2)", fontWeight: 700 }}>{ready.met} of {ready.of} met</span>
          </div>
          {ready.items.map((i) => (
            <div key={i.key} style={{ display: "grid", gridTemplateColumns: "auto minmax(0, 1fr) auto", gap: 10, alignItems: "start", fontSize: 13, borderTop: "1px solid var(--surface-2)", paddingTop: 6 }}>
              <StatusPill status={i.met ? "good" : "warn"}>{i.met ? "Met" : "Not yet"}</StatusPill>
              <div style={{ minWidth: 0 }}>
                <div><strong>{i.label}</strong> <span style={{ color: "var(--ink-2)" }}>· {typeof i.value === "number" ? money(i.value) : i.value} · target {typeof i.target === "number" ? money(i.target) : i.target}{i.projected && !i.met ? ` · on track for ${shortDate(i.projected)}` : ""}</span></div>
                {i.note && <div style={{ fontSize: 12, color: "var(--muted)" }}>{i.note}</div>}
              </div>
              {i.key === "reconciled" && !i.met ? <span /> : <Link to={i.link} aria-label={`Go to: ${i.label}`} className="btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", minHeight: 32, padding: "2px 10px", fontSize: 12, whiteSpace: "nowrap" }}>Go →</Link>}
            </div>
          ))}
          {ready.items.some((i) => i.key === "reconciled" && !i.met) && (
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <button className="btn" type="button" onClick={markChecked}>I checked one paycheck by hand and it matched the bank</button>
              <Said text={checkedNote} tone="error" />
            </div>
          )}
        </section>
      )}
      {result && <div style={{ fontSize: 11, color: "var(--muted)" }}>{result.not_advice}</div>}
    </div>
  );
}
