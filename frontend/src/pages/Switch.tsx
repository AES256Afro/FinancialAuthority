// Switching banks: the paycheck, the bills, the card payments and the subscriptions move from one account to
// another, each in its own window, so nothing is charged to an account with no money in it and nothing is
// forgotten. The picture leads: one row per item, the old account until its switch, the new one after. Every
// date and amount is the engine's; the app never moves money.
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { StatusPill } from "../components/Status";
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

const KIND_WORD: Record<string, string> = { income: "Paycheck", bill: "Bill", subscription: "Subscription", card_payment: "Card payment", plan: "Pay-later", advance: "Advance app", wallet: "Wallet", found: "Found in the ledger", transfer: "Transfer" };

function StartForm({ list, onStarted }: { list: MoveList; onStarted: () => void }) {
  const spend = list.accounts.filter((a) => a.kind === "checking" || a.kind === "savings" || a.kind === "card");
  const [source, setSource] = useState<number>(spend.find((a) => a.kind === "checking")?.id ?? spend[0]?.id ?? 0);
  const [target, setTarget] = useState<string>("new");
  const [name, setName] = useState("");
  const [finish, setFinish] = useState("");
  const [note, setNote] = useState<string | null>(null);
  async function start() {
    try {
      await api("/api/v1/moves", { method: "POST", body: JSON.stringify({ source_account_id: source, target_account_id: target === "new" ? null : Number(target), target_name: target === "new" ? name : "", finish_by: finish || null }) });
      onStarted();
    } catch (e) {
      setNote((e as Error).message);
    }
  }
  return (
    <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12, maxWidth: 760 }}>
      <strong style={{ fontSize: 15 }}>Start a move</strong>
      <span style={{ fontSize: 13, color: "var(--ink-2)" }}>The app finds everything on the old account (the paycheck, every bill it pays, the cards it pays, pay-later plans, wallets like PayPal, advance apps, and recurring charges no register names), gives each a window to switch in, and confirms each switch from the ledger.</span>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(100%, 220px), 1fr))", gap: 10 }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Move from</span>
          <select className="input" value={source} onChange={(e) => setSource(Number(e.target.value))}>{spend.map((a) => <option key={a.id} value={a.id}>{a.name}{a.institution ? ` · ${a.institution}` : ""}</option>)}</select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Move to</span>
          <select className="input" value={target} onChange={(e) => setTarget(e.target.value)}>
            <option value="new">An account not in the app yet</option>
            {list.accounts.filter((a) => a.id !== source).map((a) => <option key={a.id} value={a.id}>{a.name}{a.institution ? ` · ${a.institution}` : ""}</option>)}
          </select>
        </label>
        {target === "new" && <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Its name</span><input className="input" value={name} maxLength={80} onChange={(e) => setName(e.target.value)} placeholder="Credit union checking" /></label>}
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}><span className="lab">Finish by (optional)</span><input className="input" type="date" value={finish} onChange={(e) => setFinish(e.target.value)} /></label>
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
        <button type="button" className="btn dark" disabled={!source || (target === "new" && !name.trim())} onClick={start}>Find everything to move</button>
        {note && <span style={{ fontSize: 13, color: "var(--critical)" }}>{note}</span>}
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
  for (let t = start; t <= end; t += 14 * 86400000) ticks.push(new Date(t).toISOString().slice(0, 10));
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
        <text x={firstNew + 4} y={H - 6} fontSize="10.5" fill={c.paycheck}>first paycheck in the new account</text>
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
              {drop && <text x={eff + 4} y={y + 13} fontSize="11" fontWeight="800" fill="var(--critical)">✕ left behind</text>}
            </g>
          );
        })}
      </svg>
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)" }}>
        <span><span className="sw" style={{ background: c.bill, opacity: 0.55 }} />on {p.source.name}</span>
        <span><span className="sw" style={{ background: "var(--muted)", opacity: 0.45 }} />on {p.target.name}, planned</span>
        <span><span className="sw" style={{ background: c.paycheck }} />switched</span>
        <span><span className="sw" style={{ background: c.safe }} />confirmed by the ledger</span>
        <span>dashed box: the window to switch in</span>
      </div>
    </div>
  );
}

function ItemCard({ it, p, onChange }: { it: Item; p: Plan; onChange: (body: Record<string, unknown>) => void }) {
  const badge = it.missed ? <StatusPill status="crit">Still charging {p.source.name} ({monthDay(it.last_seen_old as string)})</StatusPill>
    : it.status === "confirmed" ? <StatusPill status="good">Confirmed {monthDay(it.confirmed_on as string)}</StatusPill>
    : it.status === "switched" ? <StatusPill status="good">Switched {monthDay(it.switched_on as string)}, waiting for the first charge</StatusPill>
    : it.overdue ? <StatusPill status="warn">Past its window</StatusPill>
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
        {it.decision === "move" && it.status === "todo" && <button type="button" className="btn dark" style={{ minHeight: 32, padding: "4px 10px" }} onClick={() => onChange({ status: "switched" })}>I switched it</button>}
        {it.status === "switched" && <button type="button" className="btn" style={{ minHeight: 32, padding: "4px 10px" }} onClick={() => onChange({ status: "todo" })}>Not yet</button>}
        {it.kind !== "income" && it.status !== "confirmed" && (
          <select className="input" value={it.decision} onChange={(e) => onChange({ decision: e.target.value })} aria-label={`What to do with ${it.name}`} style={{ minHeight: 32, padding: "2px 8px", fontSize: 12 }}>
            <option value="move">Move it</option>
            <option value="keep">Keep it on {p.source.name}</option>
            <option value="drop">Drop it</option>
            <option value="undecided">Decide later</option>
          </select>
        )}
      </div>
    </div>
  );
}

function MovePlan({ id }: { id: number }) {
  const plan = useApi<Plan>(`/api/v1/moves/${id}`);
  const [p, setP] = useState<Plan | null>(null);
  const data = p ?? plan.data;
  if (plan.error) return <div style={{ color: "var(--critical)" }}>{plan.error.message}</div>;
  if (!data) return <div style={{ color: "var(--muted)" }}>Reading the ledger…</div>;
  const pr = data.progress;
  async function change(itemId: number, body: Record<string, unknown>) {
    setP(await api<Plan>(`/api/v1/moves/${id}/items/${itemId}`, { method: "PUT", body: JSON.stringify(body) }));
  }
  async function patch(body: Record<string, unknown>) {
    setP(await api<Plan>(`/api/v1/moves/${id}`, { method: "PUT", body: JSON.stringify(body) }));
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
          {seg(pr.confirmed, c.safe, "Confirmed")}{seg(pr.switched, c.paycheck, "Switched")}{seg(pr.todo, "var(--muted)", "To do")}{seg(pr.undecided, "var(--warning)", "To decide")}
        </div>
        <div className="num" style={{ fontSize: 13, display: "flex", gap: 14, flexWrap: "wrap" }}>
          <span><strong>{pr.confirmed}</strong> confirmed</span><span><strong>{pr.switched}</strong> switched</span><span><strong>{pr.todo}</strong> to do</span>
          <span><strong>{pr.undecided}</strong> to decide</span><span><strong>{pr.kept}</strong> kept on {data.source.name}</span><span><strong>{pr.dropped}</strong> left behind</span>
        </div>
        {data.alerts.length > 0 && (
          <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
            {data.alerts.map((a, i) => <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}><StatusPill status={a.severity === "crit" ? "crit" : a.severity === "warn" ? "warn" : "good"}>{a.severity === "info" ? "Next" : undefined}</StatusPill>{a.text}</div>)}
          </div>
        )}
        <Timeline p={data} />
      </section>

      <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
        <strong style={{ fontSize: 15 }}>The money through the move</strong>
        <span style={{ fontSize: 13, color: "var(--ink-2)" }}>Each pay window: what lands in and leaves each account with the switches as planned, and what to move between them so neither runs short.</span>
        <div style={{ overflowX: "auto" }}>
          <table className="ledger">
            <thead><tr><th>Window</th><th style={{ textAlign: "right" }}>{data.source.name}: in, out, left</th><th style={{ textAlign: "right" }}>{data.target.name}: in, out, left</th><th>Move between them</th></tr></thead>
            <tbody>
              {data.windows.map((w) => (
                <tr key={w.start}>
                  <td style={{ whiteSpace: "nowrap" }}>{monthDay(w.start)} to {monthDay(w.end)}{w.paycheck_in ? <div style={{ fontSize: 11, color: "var(--muted)" }}>paycheck into {w.paycheck_in === "target" ? data.target.name : data.source.name}</div> : null}</td>
                  <td className="num" style={{ textAlign: "right" }}>{money(w.source.in)} · {money(w.source.out)} · <strong style={{ color: w.source.end < 0 ? "var(--critical)" : "inherit" }}>{money(w.source.end)}</strong></td>
                  <td className="num" style={{ textAlign: "right" }}>{money(w.target.in)} · {money(w.target.out)} · <strong style={{ color: w.target.end < 0 ? "var(--critical)" : "inherit" }}>{money(w.target.end)}</strong></td>
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
            <strong style={{ fontSize: 15 }}>{ph.title}</strong>
            <span style={{ fontSize: 12, color: "var(--muted)" }}>{ph.text}</span>
          </div>
          {ph.steps?.map((s, i) => (
            <div key={i} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
              <StatusPill status={s.done ? "good" : "warn"}>{s.done ? "Done" : "To do"}</StatusPill>
              <span>{s.text}</span>
              {s.link && !s.done && <Link to={s.link} style={{ marginLeft: "auto" }}>Open</Link>}
            </div>
          ))}
          {ph.key === "paycheck" && <Where id="direct_deposit" />}
          {ph.key === "ready" && (
            <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, flexWrap: "wrap" }}>
              The new debit card arrived on
              <input className="input" type="date" value={data.move.debit_card_on ?? ""} onChange={(e) => patch({ debit_card_on: e.target.value || null })} style={{ minHeight: 32 }} />
            </label>
          )}
          {ph.key === "ready" && !data.target.in_app && <AttachTarget moveId={id} onDone={(np) => setP(np)} />}
          <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(100%, 420px), 1fr))", gap: 10 }}>
            {ph.items.map((it) => <ItemCard key={it.id} it={it} p={data} onChange={(b) => change(it.id, b)} />)}
          </div>
        </section>
      ) : null))}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <button type="button" className="btn" onClick={async () => setP(await api<Plan>(`/api/v1/moves/${id}/refresh`, { method: "POST" }))}>Look for anything new</button>
        {data.close.ready && <button type="button" className="btn dark" onClick={() => patch({ status: "done" })}>Mark the move done</button>}
      </div>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{data.not_advice}</div>
    </div>
  );
}

function AttachTarget({ moveId, onDone }: { moveId: number; onDone: (p: Plan) => void }) {
  const list = useApi<MoveList>("/api/v1/moves");
  const [pick, setPick] = useState("");
  if (!list.data) return null;
  return (
    <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 13 }}>
      Already in the app?
      <select className="input" value={pick} onChange={(e) => setPick(e.target.value)} style={{ minHeight: 32 }}>
        <option value="">Pick the new account…</option>
        {list.data.accounts.map((a) => <option key={a.id} value={a.id}>{a.name}{a.institution ? ` · ${a.institution}` : ""}</option>)}
      </select>
      <button type="button" className="btn" disabled={!pick} onClick={async () => onDone(await api<Plan>(`/api/v1/moves/${moveId}`, { method: "PUT", body: JSON.stringify({ target_account_id: Number(pick) }) }))}>Use it</button>
    </div>
  );
}

export function Switch() {
  const list = useApi<MoveList>("/api/v1/moves");
  if (list.error) return <div style={{ color: "var(--critical)" }}>{list.error.message}</div>;
  if (!list.data) return <div style={{ color: "var(--muted)" }}>Opening…</div>;
  const active = list.data.moves.find((m) => m.status === "moving");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 1200 }}>
      <div>
        <h1 style={{ margin: "0 0 4px", fontSize: 20, fontWeight: 800 }}>Switching banks</h1>
        <p style={{ margin: 0, color: "var(--ink-2)", maxWidth: "80ch" }}>Moving the paycheck, the bills, the card payments and the subscriptions to another account, one at a time, each in its own window, so nothing is charged to an account with no money in it and nothing is forgotten. The app never moves money; it says what to switch and when, and the ledger confirms each switch.</p>
      </div>
      {active ? <MovePlan id={active.id} /> : <StartForm list={list.data} onStarted={list.reload} />}
      {list.data.moves.filter((m) => m.status !== "moving").length > 0 && (
        <div style={{ fontSize: 12, color: "var(--muted)" }}>Earlier moves: {list.data.moves.filter((m) => m.status !== "moving").map((m) => `${m.name} (${m.status})`).join(", ")}</div>
      )}
    </div>
  );
}
