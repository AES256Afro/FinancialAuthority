// Switching banks: the paycheck, the bills, the card payments and the subscriptions move from one account to
// another, each in its own window, so nothing is charged to an account with no money in it and nothing is
// forgotten. The picture leads: one row per item, the old account until its switch, the new one after. Every
// date and amount is the engine's; the app never moves money.
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { ConfirmButton } from "../components/ConfirmButton";
import { Failed, Loading, Said } from "../components/State";
import { SeverityTag, StatusPill } from "../components/Status";
import { money, monthDay, shortDate } from "../format";
import { palette } from "../looks";
import { getPrefs } from "../prefs";
import { Where } from "../components/Where";

interface Item {
  id: number; ref: string; kind: string; name: string; amount: number; due_day: number | null; decision: string; status: string;
  switched_on: string | null; confirmed_on: string | null; last_seen_old: string | null; last_seen_new: string | null;
  next_due: string | null; switch_after: string | null; switch_by: string | null; effective: string | null; missed: boolean; overdue: boolean; note: string; instruction: string; phase: string; owed?: number | null;
}
interface Window { start: string; end: string; paycheck_in: string | null; source: { in: number; out: number; end: number }; target: { in: number; out: number; end: number }; move_money: MoneyMove[] }
interface MoneyMove { amount: number; on: string; from: string; to: string; how?: "split" | "transfer"; need_by?: string; tight?: boolean; for?: string[] }
interface Plan {
  as_of: string;
  move: { id: number; name: string; status: string; started_on: string; finish_by: string | null; debit_card_on: string | null };
  source: { id: number; name: string; balance: number | null };
  target: { id: number | null; name: string; balance: number | null; in_app: boolean };
  first_new_payday: string;
  progress: { total: number; moving: number; confirmed: number; switched: number; todo: number; undecided: number; kept: number; dropped: number };
  phases: { key: string; title: string; text: string; items: Item[]; steps?: { text: string; done: boolean; link: string | null }[] }[];
  timeline: Item[];
  windows: Window[];
  alerts: { severity: "crit" | "warn" | "info"; text: string; ref: string }[];
  close: { ready: boolean; text: string };
  not_advice: string;
}
interface MoveList {
  moves: { id: number; name: string; status: string; source: string; target: string; moving: number; confirmed: number }[];
  accounts: { id: number; name: string; kind: string; institution: string }[];
}

const KIND_WORD: Record<string, string> = { income: "Paycheck", bill: "Bill", subscription: "Subscription", card_payment: "Card payment", plan: "Pay-later", advance: "Advance app", wallet: "Wallet", found: "Found in your transactions", transfer: "Transfer" };

function StartForm({ list, onStarted }: { list: MoveList; onStarted: () => void }) {
  const spend = list.accounts.filter((a) => a.kind === "checking" || a.kind === "savings" || a.kind === "card");
  const [source, setSource] = useState<number>(spend.find((a) => a.kind === "checking")?.id ?? spend[0]?.id ?? 0);
  const [target, setTarget] = useState<string>("new");
  const [name, setName] = useState("");
  const [finish, setFinish] = useState("");
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  async function start() {
    setBusy(true);
    setNote(null);
    try {
      await api("/api/v1/moves", { method: "POST", body: JSON.stringify({ source_account_id: source, target_account_id: target === "new" ? null : Number(target), target_name: target === "new" ? name : "", finish_by: finish || null }) });
      onStarted();
    } catch (e) {
      setNote(`Couldn't start: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }
  return (
    <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12, maxWidth: 760 }}>
      <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>Start switching banks</h2>
      <span style={{ fontSize: 13, color: "var(--ink-2)" }}>The app finds everything on the old account (the paycheck, every bill it pays, the cards it pays, pay-later plans, wallets like PayPal, advance apps, and repeating charges your bills list does not name), gives each the days to switch in, and confirms each switch from your transactions.</span>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 220px), 1fr))", gap: 10 }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Switch from</span>
          <select className="input" value={source} onChange={(e) => setSource(Number(e.target.value))}>{spend.map((a) => <option key={a.id} value={a.id}>{a.name}{a.institution ? ` · ${a.institution}` : ""}</option>)}</select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Switch to</span>
          <select className="input" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="new">An account not in the app yet</option>
            {list.accounts.filter((a) => a.id !== source).map((a) => <option key={a.id} value={a.id}>{a.name}{a.institution ? ` · ${a.institution}` : ""}</option>)}
          </select>
        </label>
        {target === "new" && <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Its name</span><input className="input" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder="Credit union checking" /></label>}
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Finish by (optional)</span><input className="input" type="date" value={finish} onChange={(e) => setFinish(e.target.value)} /></label>
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button type="button" className="btn dark" disabled={busy || !source || (target === "new" && !name.trim())} onClick={start}>{busy ? "Looking…" : "Find everything to switch"}</button>
        <Said text={note} tone="error" />
      </div>
    </section>
  );
}

function MoneyLine({ m }: { m: MoneyMove }) {
  if (m.how === "split") {
    return (
      <div className="num" style={{ fontSize: 13 }}>
        <strong>{money(m.amount, { cents: true })}</strong> of the {shortDate(m.on)} paycheck to {m.to}
        <div style={{ fontSize: 11, color: "var(--muted)" }}>a payroll split: {(m.for ?? []).join(", ") || "charges"} come out there within days of payday, too soon for a transfer</div>
      </div>
    );
  }
  return (
    <div className="num" style={{ fontSize: 13 }}>
      <strong>{money(m.amount, { cents: true })}</strong> {m.from} → {m.to}, start {shortDate(m.on)}
      {m.need_by && m.need_by !== m.on ? <div style={{ fontSize: 11, color: "var(--muted)" }}>needed there by {shortDate(m.need_by)}</div> : null}
    </div>
  );
}

function Timeline({ p }: { p: Plan }) {
  const c = palette(getPrefs().look);
  const rows = p.timeline;
  if (!rows.length) return null;
  const start = new Date(`${p.as_of}T12:00:00`).getTime();
  const last = Math.max(...rows.map((r) => new Date(`${r.effective}T12:00:00`).getTime()));
  const end = last + 14 * 86400000;
  const W = 860;
  const labelW = 200;
  const rowH = 24;
  const H = rows.length * rowH + 34;
  const x = (iso: string) => labelW + ((W - labelW - 12) * (new Date(`${iso}T12:00:00`).getTime() - start)) / Math.max(1, end - start);
  const firstNew = x(p.first_new_payday);
  const ticks: string[] = [];
  // Local dates, never the UTC date, so a tick never lands on tomorrow.
  const localIso = (t: number) => { const d = new Date(t); return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`; };
  for (let t = start; t <= end; t += 14 * 86400000) ticks.push(localIso(t));
  const colourOf = (r: Item) => (r.status === "confirmed" ? c.safe : r.status === "switched" ? c.paycheck : "var(--muted)");
  return (
    <div style={{ overflowX: "auto" }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Each item on the old account until its switch, then on the new one" style={{ display: "block", maxWidth: 1000, minWidth: 620, height: "auto" }}>
        {ticks.map((t) => (
          <g key={t}>
            <line x1={x(t)} x2={x(t)} y1={18} y2={H - 4} stroke="var(--hairline)" />
            <text x={x(t)} y={12} fontSize="10.5" textAnchor="middle" fill="var(--muted)">{monthDay(t)}</text>
          </g>
        ))}
        <line x1={firstNew} x2={firstNew} y1={16} y2={H - 4} stroke={c.paycheck} strokeDasharray="4 3" />
        <text x={firstNew + 4} y={H - 6} fontSize="10.5" fill="var(--paycheck-ink)">first paycheck in the new account</text>
        {rows.map((r, i) => {
          const y = 24 + i * rowH;
          const eff = x(r.effective as string);
          const drop = r.decision === "drop";
          return (
            <g key={r.id}>
              <title>{`${r.name}: ${drop ? "left behind" : `first charge on the new account ${shortDate(r.effective as string)}`}${r.switch_by ? `, switch by ${shortDate(r.switch_by)}` : ""}`}</title>
              <text x={labelW - 8} y={y + 12} fontSize="11.5" textAnchor="end" fill="var(--ink)">{r.name.length > 26 ? `${r.name.slice(0, 25)}…` : r.name}</text>
              <rect x={labelW} y={y + 3} width={Math.max(2, eff - labelW)} height={12} rx={3} fill={c.bill} opacity={0.55} />
              {r.switch_after && r.switch_by && <rect x={x(r.switch_after)} y={y + 1} width={Math.max(3, x(r.switch_by) - x(r.switch_after))} height={16} rx={3} fill="none" stroke="var(--ink)" strokeDasharray="2 2" />}
              {!drop && <rect x={eff} y={y + 3} width={Math.max(2, W - 12 - eff)} height={12} rx={3} fill={colourOf(r)} opacity={r.status === "todo" ? 0.45 : 0.9} />}
              {drop && <text x={eff + 4} y={y + 13} fontSize="11" fontWeight="800" fill="var(--critical-ink)">✕ left behind</text>}
            </g>
          );
        })}
      </svg>
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)" }}>
        <span><span className="sw" style={{ background: c.bill, opacity: 0.55 }} />on {p.source.name}</span>
        <span><span className="sw" style={{ background: "var(--muted)", opacity: 0.45 }} />on {p.target.name}, planned</span>
        <span><span className="sw" style={{ background: c.paycheck }} />switched</span>
        <span><span className="sw" style={{ background: c.safe }} />confirmed by your transactions</span>
        <span>dashed box: the days to switch in</span>
      </div>
    </div>
  );
}

function ItemCard({ it, p, onChange }: { it: Item; p: Plan; onChange: (body: Record<string, unknown>) => Promise<void> }) {
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function act(body: Record<string, unknown>) {
    setBusy(true);
    setError(null);
    try {
      await onChange(body);
    } catch (e) {
      setError(`Couldn't save that: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }
  const badge = it.missed ? <StatusPill status="crit">Still charging {p.source.name} ({shortDate(it.last_seen_old as string)})</StatusPill>
    : it.status === "confirmed" ? <StatusPill status="good">Confirmed {shortDate(it.confirmed_on as string)}</StatusPill>
    : it.status === "switched" ? <StatusPill status="good">Switched {shortDate(it.switched_on as string)}, waiting for the first charge</StatusPill>
    : it.overdue ? <StatusPill status="warn">Past its switch dates</StatusPill>
    : it.decision === "undecided" ? <StatusPill status="warn">Decide</StatusPill> : null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, padding: 12, borderRadius: 12, border: "1px solid var(--hairline)", background: "var(--surface)" }}>
      <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
        <strong>{it.name}</strong>
        <span className="num" style={{ fontSize: 13, color: "var(--ink-2)" }}>{money(it.amount, { cents: true })}{it.next_due ? ` · next ${shortDate(it.next_due)}` : ""}</span>
        <span className="lab">{KIND_WORD[it.kind] ?? it.kind}</span>
        <span style={{ marginLeft: "auto" }}>{badge}</span>
      </div>
      <div style={{ fontSize: 13, color: "var(--ink-2)" }}>{it.instruction}</div>
      {it.decision === "move" && it.switch_after && it.switch_by && it.status === "todo" && (
        <div className="num" style={{ fontSize: 12, fontWeight: 700 }}>Switch between {shortDate(it.switch_after)} and {shortDate(it.switch_by)}</div>
      )}
      <div style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
        {it.decision === "move" && it.status === "todo" && <button type="button" className="btn dark" disabled={busy} style={{ minHeight: 32, padding: "4px 10px" }} onClick={() => act({ status: "switched" })}>I switched it</button>}
        {it.status === "switched" && <button type="button" className="btn" disabled={busy} style={{ minHeight: 32, padding: "4px 10px" }} onClick={() => act({ status: "todo" })}>Not yet</button>}
        {it.kind !== "income" && it.status !== "confirmed" && (
          <select className="input" value={it.decision} disabled={busy} onChange={(e) => act({ decision: e.target.value })} aria-label={`What to do with ${it.name}`} style={{ minHeight: 32, padding: "2px 8px", fontSize: 12 }}>
            <option value="move">Move it</option>
            <option value="keep">Keep it on {p.source.name}</option>
            <option value="drop">Drop it</option>
            <option value="undecided">Decide later</option>
          </select>
        )}
        <Said text={error} tone="error" />
      </div>
    </div>
  );
}

function MovePlan({ id, onFinished }: { id: number; onFinished: () => void }) {
  const plan = useApi<Plan>(`/api/v1/moves/${id}`);
  const [p, setP] = useState<Plan | null>(null);
  const [cardNote, setCardNote] = useState<string | null>(null);
  const [lookNote, setLookNote] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const [looking, setLooking] = useState(false);
  const data = p ?? plan.data;
  if (plan.error && !data) return <Failed error={plan.error} what="the switch plan" onRetry={plan.reload} />;
  if (!data) return <Loading what="the switch plan from your transactions" />;
  const pr = data.progress;
  async function change(itemId: number, body: Record<string, unknown>) {
    setP(await api<Plan>(`/api/v1/moves/${id}/items/${itemId}`, { method: "PUT", body: JSON.stringify(body) }));
  }
  async function patch(body: Record<string, unknown>) {
    setP(await api<Plan>(`/api/v1/moves/${id}`, { method: "PUT", body: JSON.stringify(body) }));
  }
  async function setCardDay(value: string) {
    setCardNote(null);
    try {
      await patch({ debit_card_on: value || null });
    } catch (e) {
      setCardNote(`Couldn't save the date: ${(e as Error).message}`);
    }
  }
  async function lookAgain() {
    setLooking(true);
    setLookNote(null);
    try {
      setP(await api<Plan>(`/api/v1/moves/${id}/refresh`, { method: "POST" }));
      setLookNote({ tone: "info", text: "Looked again; the plan above is up to date." });
    } catch (e) {
      setLookNote({ tone: "error", text: `Couldn't look again: ${(e as Error).message}` });
    } finally {
      setLooking(false);
    }
  }
  const seg = (n: number, fill: string, label: string) => (n > 0 ? <div key={label} title={`${label}: ${n}`} style={{ flex: n, background: fill, minWidth: 6 }} /> : null);
  const c = palette(getPrefs().look);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14 }}>
      <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
          <h2 style={{ margin: 0, fontSize: 18, fontWeight: 800 }}>{data.source.name} → {data.target.name}</h2>
          <span style={{ fontSize: 13, color: "var(--ink-2)" }}>started {shortDate(data.move.started_on)}{data.move.finish_by ? ` · finish by ${shortDate(data.move.finish_by)}` : ""} · first paycheck there expected {shortDate(data.first_new_payday)}</span>
        </div>
        <div role="img" aria-label={`${pr.confirmed} confirmed, ${pr.switched} switched, ${pr.todo} to do, ${pr.undecided} to decide`} style={{ display: "flex", height: 14, borderRadius: 7, overflow: "hidden", gap: 2, background: "var(--surface-2)" }}>
          {seg(pr.confirmed, c.safe, "Confirmed")}{seg(pr.switched, c.paycheck, "Switched")}{seg(pr.todo, "var(--muted)", "To do")}{seg(pr.undecided, c.later, "To decide")}
        </div>
        <div className="num" style={{ fontSize: 13, display: "flex", gap: 14, flexWrap: "wrap" }}>
          <span><strong>{pr.confirmed}</strong> confirmed</span><span><strong>{pr.switched}</strong> switched</span><span><strong>{pr.todo}</strong> to do</span>
          <span><strong>{pr.undecided}</strong> to decide</span><span><strong>{pr.kept}</strong> kept on {data.source.name}</span><span><strong>{pr.dropped}</strong> left behind</span>
        </div>
        {data.alerts.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {data.alerts.map((a, i) => <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}><SeverityTag severity={a.severity} word={a.severity === "info" ? "Next" : undefined} /><span>{a.text}</span></div>)}
          </div>
        )}
        <Timeline p={data} />
      </section>

      <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>The money through the switch</h2>
        <span style={{ fontSize: 13, color: "var(--ink-2)" }}>Each paycheck: what comes into and goes out of each account with the switches as planned, what is left, and what to move between them so neither runs short.</span>
        <div style={{ overflowX: "auto" }}>
          <table className="ledger">
            <thead>
              <tr><th rowSpan={2} style={{ verticalAlign: "bottom" }}>Paycheck period</th><th colSpan={3} style={{ textAlign: "center", borderBottom: "1px solid var(--surface-2)" }}>{data.source.name}</th><th colSpan={3} style={{ textAlign: "center", borderBottom: "1px solid var(--surface-2)" }}>{data.target.name}</th><th rowSpan={2} style={{ verticalAlign: "bottom" }}>Move between them</th></tr>
              <tr><th style={{ textAlign: "right" }}>In</th><th style={{ textAlign: "right" }}>Out</th><th style={{ textAlign: "right" }}>Left</th><th style={{ textAlign: "right" }}>In</th><th style={{ textAlign: "right" }}>Out</th><th style={{ textAlign: "right" }}>Left</th></tr>
            </thead>
            <tbody>
              {data.windows.map((w) => (
                <tr key={w.start}>
                  <td style={{ whiteSpace: "nowrap" }}>{shortDate(w.start)} to {shortDate(w.end)}{w.paycheck_in ? <div style={{ fontSize: 11, color: "var(--muted)" }}>paycheck into {w.paycheck_in === "target" ? data.target.name : data.source.name}</div> : null}</td>
                  <td className="num" style={{ textAlign: "right" }}>{money(w.source.in)}</td>
                  <td className="num" style={{ textAlign: "right" }}>{money(w.source.out)}</td>
                  <td className="num" style={{ textAlign: "right", fontWeight: 800, color: w.source.end < 0 ? "var(--critical-ink)" : "inherit" }}>{money(w.source.end)}{w.source.end < 0 ? <div style={{ fontSize: 11 }}>short</div> : null}</td>
                  <td className="num" style={{ textAlign: "right" }}>{money(w.target.in)}</td>
                  <td className="num" style={{ textAlign: "right" }}>{money(w.target.out)}</td>
                  <td className="num" style={{ textAlign: "right", fontWeight: 800, color: w.target.end < 0 ? "var(--critical-ink)" : "inherit" }}>{money(w.target.end)}{w.target.end < 0 ? <div style={{ fontSize: 11 }}>short</div> : null}</td>
                  <td>{w.move_money.length ? w.move_money.map((m, i) => <MoneyLine key={i} m={m} />) : <span style={{ color: "var(--muted)" }}>nothing</span>}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>

      {data.phases.map((ph) => (ph.items.length || ph.steps?.length ? (
        <section key={ph.key} className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
            <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>{ph.title}</h2>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>{ph.text}</span>
          </div>
          {ph.steps?.map((s, i) => (
            <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
              <StatusPill status={s.done ? "good" : "warn"}>{s.done ? "Done" : "To do"}</StatusPill>
              <span>{s.text}</span>
              {s.link && !s.done && <Link to={s.link} aria-label={`Open: ${s.text}`} style={{ marginLeft: "auto" }}>Open</Link>}
            </div>
          ))}
          {ph.key === "paycheck" && <Where id="direct_deposit" />}
          {ph.key === "ready" && (
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, flexWrap: "wrap" }}>
                The new debit card arrived on
                <input className="input" type="date" value={data.move.debit_card_on ?? ""} onChange={(e) => setCardDay(e.target.value)} style={{ minHeight: 32 }} />
              </label>
              <Said text={cardNote} tone="error" />
            </div>
          )}
          {ph.key === "ready" && !data.target.in_app && <AttachTarget moveId={id} onDone={(np) => setP(np)} />}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 420px), 1fr))", gap: 10 }}>
            {ph.items.map((it) => <ItemCard key={it.id} it={it} p={data} onChange={(b) => change(it.id, b)} />)}
          </div>
        </section>
      ) : null))}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button type="button" className="btn" disabled={looking} onClick={lookAgain}>{looking ? "Looking…" : "Look for anything new"}</button>
        {data.close.ready && <ConfirmButton className="btn dark" label="Mark the switch finished" question="Mark this switch finished? It moves to Earlier switches." yes="Yes, it's finished" no="Not yet" onConfirm={async () => { await patch({ status: "done" }); onFinished(); }} />}
        {lookNote && <Said text={lookNote.text} tone={lookNote.tone} />}
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{data.not_advice}</div>
    </div>
  );
}

function AttachTarget({ moveId, onDone }: { moveId: number; onDone: (p: Plan) => void }) {
  const list = useApi<MoveList>("/api/v1/moves");
  const [pick, setPick] = useState("");
  const [error, setError] = useState<string | null>(null);
  if (list.error && !list.data) return <Failed error={list.error} what="your accounts" onRetry={list.reload} card={false} />;
  if (!list.data) return <Loading what="your accounts" card={false} />;
  async function use() {
    setError(null);
    try {
      onDone(await api<Plan>(`/api/v1/moves/${moveId}`, { method: "PUT", body: JSON.stringify({ target_account_id: Number(pick) }) }));
    } catch (e) {
      setError(`Couldn't use it: ${(e as Error).message}`);
    }
  }
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 13 }}>
      <label htmlFor={`attach-${moveId}`}>Already in the app?</label>
      <select id={`attach-${moveId}`} className="input" value={pick} onChange={(e) => setPick(e.target.value)} style={{ minHeight: 32 }}>
        <option value="">Pick the new account…</option>
        {list.data.accounts.map((a) => <option key={a.id} value={a.id}>{a.name}{a.institution ? ` · ${a.institution}` : ""}</option>)}
      </select>
      <button type="button" className="btn" disabled={!pick} onClick={use}>Use it</button>
      <Said text={error} tone="error" />
    </div>
  );
}

export function Switch() {
  const list = useApi<MoveList>("/api/v1/moves");
  if (list.error && !list.data) return <Failed error={list.error} what="Switch banks" onRetry={list.reload} />;
  if (!list.data) return <Loading what="Switch banks" />;
  const active = list.data.moves.find((m) => m.status === "moving");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 1200 }}>
      <div>
        <h1 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 800 }}>Switch banks</h1>
        <p style={{ margin: 0, color: "var(--ink-2)", maxWidth: "80ch" }}>Moving the paycheck, the bills, the card payments and the subscriptions to another account, one at a time, each in the days that suit its due date, so nothing is charged to an account with no money in it and nothing is forgotten. The app never moves money; it says what to switch and when, and your transactions confirm each switch.</p>
      </div>
      {active ? <MovePlan id={active.id} onFinished={list.reload} /> : <StartForm list={list.data} onStarted={list.reload} />}
      {list.data.moves.filter((m) => m.status !== "moving").length > 0 && (
        <div style={{ fontSize: 12, color: "var(--muted)" }}>Earlier switches: {list.data.moves.filter((m) => m.status !== "moving").map((m) => `${m.name} (${m.status})`).join(", ")}</div>
      )}
    </div>
  );
}
