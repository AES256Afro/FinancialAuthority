// Shared money: the accounts marked shared (and the person's own), as a second person sees them. The picture first:
// one bar per account; then the next month of bills paid from them on a strip of days, spending by category, and
// the latest transactions. Every figure is the app's; this page only draws them. The owner (or a person with
// everything) picks whose view to look at, to see exactly what that person sees.
import { CalendarDays, Clock, User, Users } from "lucide-react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { useApi } from "../api";
import { Empty, Failed, Show } from "../components/State";
import { flow, money, parseIso, shortDate, when } from "../format";
import { KIND_WORD, ownerName, possessive, roleOf, type MePerson, type PeopleOut, type SharedAccount, type SharedBill, type SharedOut } from "../types-people";

const DAY_MS = 86_400_000;
const OWED_KINDS = new Set(["card", "loan", "bnpl", "advance", "medical"]);

const ROLE_SHORT: Record<string, string> = { everything: "everything", shared: "shared accounts only" };

/** Whose an account is, drawn: two people for shared, one for a person's own. Never colour alone. */
export function WhoTag({ shared, word }: { shared: boolean; word: string }) {
  const Icon = shared ? Users : User;
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 4, padding: "2px 8px", borderRadius: 999, fontSize: 11, fontWeight: 800, whiteSpace: "nowrap", flexShrink: 0, border: "1px solid var(--hairline)", background: shared ? "var(--paycheck-soft)" : "var(--surface-2)", color: shared ? "var(--paycheck-ink)" : "var(--ink-2)" }}>
      <Icon size={12} strokeWidth={2.2} aria-hidden="true" />
      {word}
    </span>
  );
}

const h2 = { margin: 0, fontSize: 15, fontWeight: 800 } as const;
const sub = { fontSize: 12, color: "var(--ink-2)" } as const;

// ---- the picture: one bar per account -------------------------------------------------------------------------

function AccountBars({ accounts, theirs }: { accounts: SharedAccount[]; theirs: string }) {
  // The longest bar is the largest balance; the others are drawn to the same scale.
  const widest = Math.max(1, ...accounts.map((a) => Math.abs(a.balance ?? 0)));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      {accounts.map((a) => {
        const owed = OWED_KINDS.has(a.kind);
        const fill = owed ? "var(--debt)" : "var(--safe)";
        const word = a.whose === "shared" ? "Shared" : theirs;
        const shown = a.balance === null ? "no balance yet" : money(a.balance);
        return (
          <div key={a.source} style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: "4px 10px", alignItems: "center" }}>
            <span style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", minWidth: 0 }}>
              <strong style={{ fontSize: 14, overflowWrap: "anywhere" }}>{a.name}</strong>
              <WhoTag shared={a.whose === "shared"} word={word} />
            </span>
            <span className={a.balance === null ? undefined : "num"} style={{ fontSize: a.balance === null ? 12 : 16, fontWeight: 800, textAlign: "right", color: a.balance === null ? "var(--muted)" : "var(--ink)" }}>{shown}</span>
            <div role="img" aria-label={`${a.name}, ${word}: ${shown}`} style={{ gridColumn: "1 / -1", position: "relative", height: 14, background: "var(--surface-2)", borderRadius: 4 }}>
              {a.balance !== null && <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${Math.max(1, (Math.abs(a.balance) / widest) * 100)}%`, background: fill, borderRadius: 4 }} />}
            </div>
            <span style={{ ...sub, gridColumn: "1 / -1" }}>
              {KIND_WORD[a.kind] ?? a.kind}{a.institution ? ` · ${a.institution}` : ""} · {a.balance_at ? `balance as of ${when(a.balance_at)}` : "the bank has not sent a balance yet"}
            </span>
          </div>
        );
      })}
      <div aria-hidden="true" style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span className="sw" style={{ background: "var(--safe)", margin: 0 }} />Money in the account</span>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span className="sw" style={{ background: "var(--debt)", margin: 0 }} />A card or loan: the balance it shows</span>
      </div>
    </div>
  );
}

// ---- the bills on a strip of days ------------------------------------------------------------------------------

const SW = 720;
const SH = 110;
const PAD = 44;
const TRACK = 80;
const LANES = [16, 34, 52];
const CHAR = 6.4; // about how wide one letter of an 11-point label is, so labels do not cover each other

function BillStrip({ from, until, bills }: { from: string; until: string; bills: SharedBill[] }) {
  const start = parseIso(from).getTime();
  const days = Math.max(1, Math.round((parseIso(until).getTime() - start) / DAY_MS));
  const xOf = (offset: number) => PAD + (offset / days) * (SW - PAD * 2);
  const x = (iso: string) => xOf(Math.round((parseIso(iso).getTime() - start) / DAY_MS));
  const dates = [...new Set(bills.map((b) => b.date))];
  const onDay = (d: string) => bills.filter((b) => b.date === d);
  // Each date's label goes in the first row above the strip where it fits.
  const ends = LANES.map(() => -Infinity);
  const placed = dates.map((d) => {
    const count = onDay(d).length;
    const text = count > 1 ? `${shortDate(d)} · ${count} bills` : shortDate(d);
    const width = text.length * CHAR;
    const cx = x(d);
    const left = Math.max(2, Math.min(SW - 2 - width, cx - width / 2));
    let lane = ends.findIndex((end) => left > end + 6);
    if (lane < 0) lane = ends.indexOf(Math.min(...ends));
    ends[lane] = left + width;
    return { d, cx, text, left, lane, count };
  });
  const weeks = Array.from({ length: Math.floor(days / 7) + 1 }, (_, i) => i * 7);
  const label = `The bills on the days ahead. ${dates.map((d) => `${shortDate(d)}: ${onDay(d).map((b) => `${b.name} ${money(b.amount)}`).join(", ")}`).join("; ")}.`;
  return (
    <div style={{ overflowX: "auto" }}>
      <svg viewBox={`0 0 ${SW} ${SH}`} width="100%" role="img" aria-label={label} style={{ display: "block", minWidth: 520, height: "auto" }}>
        <line x1={PAD} x2={SW - PAD} y1={TRACK} y2={TRACK} stroke="var(--hairline)" strokeWidth={6} strokeLinecap="round" />
        {weeks.map((n) => <line key={n} x1={xOf(n)} x2={xOf(n)} y1={TRACK - 7} y2={TRACK + 7} stroke="var(--muted)" strokeWidth={1} />)}
        {placed.map((p) => (
          <g key={p.d}>
            <line x1={p.cx} x2={p.cx} y1={LANES[p.lane] + 4} y2={TRACK - 9} stroke="var(--bill)" strokeWidth={1} strokeDasharray="2 2" />
            <text x={p.left} y={LANES[p.lane]} fontSize={11} fontWeight={700} fill="var(--ink)">{p.text}</text>
            <circle cx={p.cx} cy={TRACK} r={p.count > 1 ? 8 : 6} fill="var(--bill)" stroke="var(--surface)" strokeWidth={2} />
          </g>
        ))}
        <text x={PAD} y={SH - 4} fontSize={11} fill="var(--muted)" textAnchor="start">Today, {shortDate(from)}</text>
        <text x={SW - PAD} y={SH - 4} fontSize={11} fill="var(--muted)" textAnchor="end">{shortDate(until)}</text>
      </svg>
      <div style={{ ...sub, display: "flex", gap: 14, flexWrap: "wrap" }}>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span className="sw" style={{ background: "var(--bill)", margin: 0, borderRadius: 5 }} aria-hidden="true" />a bill paid that day</span>
        <span>each tick: a week</span>
      </div>
    </div>
  );
}

function Bills({ data }: { data: SharedOut }) {
  return (
    <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "baseline" }}>
        <h2 style={h2}>Bills paid from these accounts</h2>
        <span style={sub}>until {shortDate(data.bills_until)}: <strong className="num" style={{ color: "var(--ink)" }}>{money(data.bills_total)}</strong> in all</span>
      </div>
      {data.bills.length === 0 ? (
        <Empty card={false}>No bills are paid from these accounts before {shortDate(data.bills_until)}.</Empty>
      ) : (
        <>
          <BillStrip from={data.as_of} until={data.bills_until} bills={data.bills} />
          <div style={{ overflowX: "auto" }}>
            <table className="ledger">
              <thead><tr><th>Day</th><th>Bill</th><th>Paid from</th><th>How</th><th style={{ textAlign: "right" }}>Amount</th></tr></thead>
              <tbody>
                {data.bills.map((b) => (
                  <tr key={`${b.source}:${b.date}`}>
                    <td style={{ whiteSpace: "nowrap" }}>{shortDate(b.date)}</td>
                    <td><strong>{b.name}</strong></td>
                    <td style={{ color: "var(--ink-2)" }}>{b.account}</td>
                    <td style={{ color: "var(--ink-2)", whiteSpace: "nowrap" }}>{b.autopay ? "Paid automatically" : "Paid by hand"}</td>
                    <td className="num" style={{ textAlign: "right", fontWeight: 700 }}>{money(b.amount, { cents: true })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
        </>
      )}
    </section>
  );
}

// ---- spending by category, and the latest transactions --------------------------------------------------------

function Spending({ data }: { data: SharedOut }) {
  // The longest bar is the largest category; the others are drawn to the same scale.
  const widest = Math.max(1, ...data.spending.map((s) => s.spent));
  return (
    <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "baseline" }}>
        <h2 style={h2}>Spent from these accounts, by category</h2>
        <span style={sub}>the last 30 days: <strong className="num" style={{ color: "var(--ink)" }}>{money(data.spent_30d)}</strong> in all</span>
      </div>
      {data.spending.length === 0 ? (
        <Empty card={false}>Nothing was spent from these accounts in the last 30 days.</Empty>
      ) : (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {data.spending.map((s) => (
            <div key={s.category} style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: "2px 10px", alignItems: "center", fontSize: 13 }}>
              <span style={{ overflowWrap: "anywhere" }}>{s.category}</span>
              <strong className="num" style={{ textAlign: "right" }}>{money(s.spent)}</strong>
              <div role="img" aria-label={`${s.category}: ${money(s.spent)}`} style={{ gridColumn: "1 / -1", position: "relative", height: 10, background: "var(--surface-2)", borderRadius: 4 }}>
                <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: `${Math.max(1, (s.spent / widest) * 100)}%`, background: "var(--paycheck)", borderRadius: 4 }} />
              </div>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

const FIRST = 30;

function Recent({ data }: { data: SharedOut }) {
  const [all, setAll] = useState(false);
  const rows = all ? data.recent : data.recent.slice(0, FIRST);
  return (
    <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "baseline" }}>
        <h2 style={h2}>The latest transactions</h2>
        <span style={sub}>from these accounts, newest first. Money out is a plain amount; money in says “in”, like +$40.00 in.</span>
      </div>
      {data.recent.length === 0 ? (
        <Empty card={false}>No transactions from these accounts in the last 45 days.</Empty>
      ) : (
        <>
          <div style={{ overflowX: "auto" }}>
            <table className="ledger">
              <thead><tr><th>Day</th><th>Where</th><th>Category</th><th>Account</th><th style={{ textAlign: "right" }}>Amount</th></tr></thead>
              <tbody>
                {rows.map((t) => (
                  <tr key={t.source}>
                    <td style={{ whiteSpace: "nowrap" }}>{shortDate(t.date)}</td>
                    <td>
                      <span style={{ display: "inline-flex", alignItems: "center", gap: 6, flexWrap: "wrap" }}>
                        {t.merchant}
                        {t.pending && <span style={{ display: "inline-flex", alignItems: "center", gap: 3, fontSize: 11, fontWeight: 800, color: "var(--ink-2)" }}><Clock size={12} aria-hidden="true" />Pending</span>}
                      </span>
                    </td>
                    <td style={{ color: "var(--ink-2)" }}>{t.category || "Uncategorized"}</td>
                    <td style={{ color: "var(--ink-2)" }}>{t.account}</td>
                    <td className="num" style={{ textAlign: "right", whiteSpace: "nowrap" }}>{flow(t.amount, { cents: true })}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {data.recent.length > FIRST && (
            <div><button type="button" className="btn" onClick={() => setAll((a) => !a)} style={{ minHeight: 32, padding: "4px 12px" }}>{all ? `Show the first ${FIRST}` : `Show all ${data.recent.length}`}</button></div>
          )}
        </>
      )}
    </section>
  );
}

// ---- the page --------------------------------------------------------------------------------------------------

export function Shared({ me }: { me: MePerson | null }) {
  const role = roleOf(me);
  const [params, setParams] = useSearchParams();
  // The owner picks from the people added; a person with everything can look at their own view.
  const people = useApi<PeopleOut>(role === "owner" ? "/api/v1/people" : null);
  const choices: { id: number; label: string }[] =
    role === "owner"
      ? (people.data?.people ?? []).map((p) => ({ id: p.id, label: `${p.display_name} (${ROLE_SHORT[p.role] ?? p.role}${p.active ? "" : ", turned off"})` }))
      : role === "everything" && me?.person_id
        ? [{ id: me.person_id, label: `You, ${me.name}` }]
        : [];
  const asked = Number(params.get("person"));
  const personId = role !== "shared" && Number.isInteger(asked) && asked > 0 ? asked : null;
  const state = useApi<SharedOut>(personId ? `/api/v1/shared?person_id=${personId}` : "/api/v1/shared");
  const owner = ownerName(me?.owner || people.data?.owner);

  function pick(value: string) {
    const next = new URLSearchParams(params);
    if (value) next.set("person", value);
    else next.delete("person");
    setParams(next, { replace: true });
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 900 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Shared money</h1>
      {role !== "shared" && (
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "0 1 340px", minWidth: 0 }}>
            <span className="lab">See it as</span>
            <select className="input" value={personId ?? ""} onChange={(e) => pick(e.target.value)}>
              <option value="">Shared accounts only</option>
              {choices.map((c) => <option key={c.id} value={c.id}>{c.label}</option>)}
              {personId && !choices.some((c) => c.id === personId) && <option value={personId}>The person you chose</option>}
            </select>
          </label>
          {role === "owner" && <Link to="/settings/people" style={{ fontSize: 13, fontWeight: 700, minHeight: 32, display: "inline-flex", alignItems: "center" }}>People, and whose each account is</Link>}
          {people.error && !people.data && <Failed error={people.error} what="the people you added" onRetry={people.reload} card={false} />}
        </div>
      )}
      <Show state={state} what="the shared accounts">
        {(d) => {
          const name = d.person?.name ?? "";
          const theirs = role === "shared" ? "Yours" : name ? possessive(name) : "Theirs";
          return (
            <>
              <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)" }}>
                {role === "shared"
                  ? `The accounts ${owner} shares with you, and your own: their balances, the bills paid from them, and what was spent from them.`
                  : d.person
                    ? `Exactly what ${name} sees here after signing in: the accounts marked shared, and the ones marked ${possessive(name)}.`
                    : "The accounts marked shared, as someone who sees the shared accounts only would see them."}
                {d.person && d.person.role === "everything" && role !== "shared" ? ` ${name} has everything, so ${name} also sees the rest of the app.` : ""}
              </p>
              {d.note ? (
                role === "shared"
                  ? <Empty>{d.note} Ask {owner} to mark the accounts you share.</Empty>
                  : <Empty to="/settings/people" action="Mark accounts shared">{d.note}</Empty>
              ) : (
                <>
                  <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
                    <h2 style={h2}>The accounts</h2>
                    <AccountBars accounts={d.accounts} theirs={theirs} />
                    <div style={{ display: "flex", gap: 24, flexWrap: "wrap", alignItems: "baseline", paddingTop: 10, borderTop: "1px solid var(--surface-2)" }}>
                      <div><div className="lab">Cash in these accounts</div><div className="num" style={{ fontSize: 22, fontWeight: 800, color: "var(--safe-ink)" }}>{money(d.cash)}</div></div>
                      {d.cards_owed > 0 && <div><div className="lab">Owed on the cards</div><div className="num" style={{ fontSize: 22, fontWeight: 800, color: "var(--debt-ink)" }}>{money(d.cards_owed)}</div></div>}
                      <div style={{ ...sub, display: "inline-flex", alignItems: "center", gap: 6 }}><CalendarDays size={14} aria-hidden="true" />Each balance is the last one its bank sent.</div>
                    </div>
                  </section>
                  <Bills data={d} />
                  <Spending data={d} />
                  <Recent data={d} />
                </>
              )}
              <div style={{ fontSize: 12, color: "var(--muted)" }}>{d.not_advice}</div>
            </>
          );
        }}
      </Show>
    </div>
  );
}
