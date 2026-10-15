// Outlook: savings and investments carried forward from today's balances and the contributions on record,
// at rates you set as assumptions. Three scenarios, nominal and in today's dollars, a retirement line when
// you name the year. The engine computed every number; a rate is a guess, and the page says so.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { money } from "../format";

interface YearRow {
  year: number;
  balance: number;
  contributed: number;
  growth: number;
  real: number;
}

interface Scenario {
  id: "low" | "mid" | "high";
  word: string;
  rate: number;
  years: YearRow[];
  milestones: YearRow[];
  at_retirement: (YearRow & { monthly_at_withdrawal_rate: number; monthly_real: number }) | null;
}

interface Outlook {
  as_of: string;
  assumptions: { savings_apy: number; returns: { low: number; mid: number; high: number }; inflation: number; years: number[]; years_to_retirement: number | null; extra_savings_per_paycheck: number; investment_contribution_per_paycheck: number | null; employer_match_per_paycheck: number; withdrawal_rate: number };
  paychecks_per_year: number;
  savings: { now: number; apy: number; per_paycheck: number; goals_per_paycheck: number; extra_per_paycheck: number; per_month: number; months: { month: string; balance: number }[]; milestones: { months: number; balance: number; contributed: number; interest: number }[]; accounts: { id: number; name: string; balance: number | null }[] };
  investments: { now: number; per_paycheck: number; own_per_paycheck: number; employer_match_per_paycheck: number; contribution_source: "paystubs" | "assumption" | "none"; paystub_lines: { name: string; last: number }[]; per_month: number; horizon_years: number; milestone_years: number[]; years_to_retirement: number | null; withdrawal_rate: number; scenarios: Scenario[]; accounts: { id: number; name: string; balance: number | null }[] };
  not_advice: string;
}

const TONE: Record<Scenario["id"], string> = { low: "var(--debt)", mid: "var(--paycheck)", high: "var(--safe)" };
const pct = (r: number) => `${(r * 100).toFixed(r * 100 % 1 === 0 ? 0 : 1)}%`;

function Chart({ scenarios, retireIn, real }: { scenarios: Scenario[]; retireIn: number | null; real: boolean }) {
  const w = 680;
  const h = 260;
  const pad = { l: 56, r: 70, t: 14, b: 28 };
  const horizon = scenarios[0]?.years.length ?? 1;
  const max = Math.max(1, ...scenarios.flatMap((s) => s.years.map((y) => (real ? y.real : y.balance))));
  const x = (year: number) => pad.l + ((w - pad.l - pad.r) * year) / horizon;
  const y = (v: number) => pad.t + (h - pad.t - pad.b) * (1 - v / max);
  const ticks = [0.25, 0.5, 0.75, 1].map((f) => f * max);
  const yearTicks = [0, ...[5, 10, 15, 20, 25, 30, 35, 40, 45, 50, 55, 60].filter((t) => t <= horizon)];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" role="img" aria-label="Investment balance by year, three scenarios" style={{ display: "block", maxWidth: 760 }}>
      {ticks.map((t) => (
        <g key={t}>
          <line x1={pad.l} x2={w - pad.r} y1={y(t)} y2={y(t)} stroke="var(--hairline)" />
          <text x={pad.l - 6} y={y(t) + 4} textAnchor="end" fontSize={10} fill="var(--muted)" className="num">{t >= 1e6 ? `$${(t / 1e6).toFixed(1)}M` : `$${Math.round(t / 1000)}k`}</text>
        </g>
      ))}
      {yearTicks.map((t) => <text key={t} x={x(t)} y={h - 8} textAnchor="middle" fontSize={10} fill="var(--muted)">{t === 0 ? "now" : `${t}y`}</text>)}
      {retireIn && retireIn <= horizon && (
        <g>
          <line x1={x(retireIn)} x2={x(retireIn)} y1={pad.t} y2={h - pad.b} stroke="var(--ink-2)" strokeDasharray="4 4" />
          <text x={x(retireIn)} y={pad.t + 10} textAnchor="middle" fontSize={10} fill="var(--ink-2)" fontWeight={700}>retirement</text>
        </g>
      )}
      {scenarios.map((s) => {
        const points = [{ year: 0, v: real ? s.years[0].contributed - (s.years[0].contributed - (s.years[0].balance - s.years[0].growth)) : s.years[0].balance - s.years[0].growth }, ...s.years.map((r) => ({ year: r.year, v: real ? r.real : r.balance }))];
        const d = points.map((p, i) => `${i === 0 ? "M" : "L"}${x(p.year).toFixed(1)},${y(p.v).toFixed(1)}`).join(" ");
        const last = points[points.length - 1];
        return (
          <g key={s.id}>
            <title>{`${s.word} at ${pct(s.rate)}: ${money(last.v)} after ${horizon} years${real ? " in today's dollars" : ""}`}</title>
            <path d={d} fill="none" stroke={TONE[s.id]} strokeWidth={2} />
            <circle cx={x(last.year)} cy={y(last.v)} r={3.5} fill={TONE[s.id]} stroke="var(--surface)" strokeWidth={2} />
            <text x={x(last.year) + 8} y={y(last.v) + 4} fontSize={11} fontWeight={700} fill="var(--ink-2)" className="num">{money(last.v)}</text>
          </g>
        );
      })}
      <line x1={pad.l} x2={w - pad.r} y1={y(0)} y2={y(0)} stroke="var(--ink-2)" />
    </svg>
  );
}

function SavingsLine({ months }: { months: { month: string; balance: number }[] }) {
  const w = 680;
  const h = 120;
  const pad = { l: 56, r: 70, t: 10, b: 22 };
  const max = Math.max(1, ...months.map((m) => m.balance));
  const x = (i: number) => pad.l + ((w - pad.l - pad.r) * i) / (months.length - 1);
  const y = (v: number) => pad.t + (h - pad.t - pad.b) * (1 - v / max);
  const d = months.map((m, i) => `${i === 0 ? "M" : "L"}${x(i).toFixed(1)},${y(m.balance).toFixed(1)}`).join(" ");
  const last = months[months.length - 1];
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" role="img" aria-label="Savings balance by month" style={{ display: "block", maxWidth: 760 }}>
      <title>{`${money(months[0].balance)} now to ${money(last.balance)} in ${months.length - 1} months`}</title>
      <path d={`${d} L${x(months.length - 1).toFixed(1)},${y(0)} L${pad.l},${y(0)} Z`} fill="var(--safe-soft)" />
      <path d={d} fill="none" stroke="var(--safe)" strokeWidth={2} />
      {[0, 12, 24, 36].filter((i) => i < months.length).map((i) => <text key={i} x={x(i)} y={h - 6} textAnchor="middle" fontSize={10} fill="var(--muted)">{i === 0 ? "now" : `${i}m`}</text>)}
      <circle cx={x(months.length - 1)} cy={y(last.balance)} r={3.5} fill="var(--safe)" stroke="var(--surface)" strokeWidth={2} />
      <text x={x(months.length - 1) + 8} y={y(last.balance) + 4} fontSize={11} fontWeight={700} fill="var(--ink-2)" className="num">{money(last.balance)}</text>
      <line x1={pad.l} x2={w - pad.r} y1={y(0)} y2={y(0)} stroke="var(--ink-2)" />
    </svg>
  );
}

interface WhereOption {
  id: string;
  rank: number;
  name: string;
  in_per_paycheck: number;
  match_per_paycheck: number;
  rate: number;
  value: number;
  contributed: number;
  after_tax: number;
  guaranteed: boolean;
  reachable: string;
  tax: string;
  note: string;
  action: string;
  link: string;
  exists: boolean;
  milestones: { year: number; after_tax: number }[];
}

interface Where {
  amount_per_paycheck: number;
  take_home_per_paycheck: number;
  percent: number | null;
  basis: { kind: string; per_paycheck: number | null; note: string } | null;
  milestone_years: number[];
  paychecks_per_year?: number;
  years: number;
  scenario: string;
  rate: number;
  options: WhereOption[];
  best: string | null;
  summary: string;
  not_advice: string;
}

function WhereToPut({ retireIn }: { retireIn: number | null }) {
  const [mode, setMode] = useState<"dollars" | "percent">("dollars");
  const [amount, setAmount] = useState("50");
  const [percent, setPercent] = useState("20");
  const [basis, setBasis] = useState<"net" | "gross">("net");
  const [years, setYears] = useState(retireIn ? String(retireIn) : "10");
  const [scenario, setScenario] = useState("mid");
  const [asked, setAsked] = useState({ mode: "dollars" as "dollars" | "percent", amount: "50", percent: "20", basis: "net" as "net" | "gross", years: retireIn ? String(retireIn) : "10", scenario: "mid" });
  const query = asked.mode === "percent" ? `percent=${encodeURIComponent(asked.percent || "0")}&basis=${asked.basis}` : `amount=${encodeURIComponent(asked.amount || "0")}`;
  const { data, error } = useApi<Where>(`/api/v1/projections/where?${query}&years=${asked.years}&scenario=${asked.scenario}`);
  const best = data?.options[0]?.after_tax ?? 1;
  return (
    <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
      <form
        onSubmit={(e) => {
          e.preventDefault();
          setAsked({ mode, amount, percent, basis, years, scenario });
        }}
        style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}
      >
        <strong style={{ fontSize: 15 }}>If I put</strong>
        <select className="input" value={mode} onChange={(e) => setMode(e.target.value as "dollars" | "percent")} aria-label="Dollars or a share of the paycheck" style={{ minHeight: 38 }}>
          <option value="dollars">dollars</option>
          <option value="percent">a share of my pay</option>
        </select>
        {mode === "dollars" ? (
          <>
            <input className="input num" inputMode="decimal" value={amount} onChange={(e) => setAmount(e.target.value)} style={{ width: 90 }} aria-label="Dollars a paycheck" />
            <span style={{ fontSize: 14 }}>a paycheck somewhere for</span>
          </>
        ) : (
          <>
            <input className="input num" inputMode="decimal" value={percent} onChange={(e) => setPercent(e.target.value)} style={{ width: 70 }} aria-label="Percent of the paycheck" />
            <span style={{ fontSize: 14 }}>% of my</span>
            <select className="input" value={basis} onChange={(e) => setBasis(e.target.value as "net" | "gross")} aria-label="Take-home or gross pay" style={{ minHeight: 38 }}>
              <option value="net">take-home pay</option>
              <option value="gross">gross pay</option>
            </select>
            <span style={{ fontSize: 14 }}>each paycheck somewhere for</span>
          </>
        )}
        <select className="input" value={years} onChange={(e) => setYears(e.target.value)} aria-label="Years" style={{ minHeight: 38 }}>
          {[1, 3, 5, 10, 15, 20, 25, 30, ...(retireIn && ![1, 3, 5, 10, 15, 20, 25, 30].includes(retireIn) ? [retireIn] : [])].sort((a, b) => a - b).map((y) => <option key={y} value={y}>{y} years{retireIn === y ? " (retirement)" : ""}</option>)}
        </select>
        <span style={{ fontSize: 14 }}>at the</span>
        <select className="input" value={scenario} onChange={(e) => setScenario(e.target.value)} aria-label="Return scenario" style={{ minHeight: 38 }}>
          <option value="low">cautious</option>
          <option value="mid">middle</option>
          <option value="high">strong</option>
        </select>
        <span style={{ fontSize: 14 }}>return, where does it do the most?</span>
        <button className="btn dark" type="submit">Compare</button>
      </form>
      {error && <div style={{ fontSize: 13, color: "var(--critical)" }}>{error.message}</div>}
      {data && (
        <>
          <div style={{ fontSize: 13, color: "var(--ink-2)" }}>{data.summary}</div>
          {data.basis && (
            <div style={{ fontSize: 12, color: data.basis.per_paycheck === null ? "var(--warning)" : "var(--ink-2)" }}>
              {data.basis.per_paycheck === null
                ? <>{data.basis.note}. <Link to="/confirm">Needs you</Link> · <Link to="/paystubs">Paystubs</Link></>
                : <>Your {data.basis.kind === "gross" ? "gross" : "take-home"} pay on file is {money(data.basis.per_paycheck, { cents: true })} a paycheck ({data.basis.note}), {data.paychecks_per_year ?? 24} paychecks a year{data.basis.kind === "gross" ? `; ${money(data.take_home_per_paycheck)} of take-home for the after-tax places` : ""}.</>}
            </div>
          )}
          <style>{`.where-row { display: grid; grid-template-columns: 28px minmax(0, 1fr) auto; gap: 10px; align-items: start; } @media (max-width: 640px) { .where-row { grid-template-columns: 28px minmax(0, 1fr); } .where-row > a { grid-column: 2; justify-self: start; } }`}</style>
          <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
            {data.options.map((o) => (
              <div key={o.id} className="where-row" style={{ borderTop: "1px solid var(--surface-2)", paddingTop: 8 }}>
                <div className="num" style={{ fontWeight: 800, fontSize: 16, color: o.rank === 1 ? "var(--safe)" : "var(--muted)" }}>{o.rank}</div>
                <div style={{ minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
                  <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap" }}>
                    <strong style={{ fontSize: 14 }}>{o.name}</strong>
                    <span style={{ fontSize: 11, color: "var(--ink-2)" }}>
                      {money(o.in_per_paycheck)} in{o.match_per_paycheck > 0 ? ` + ${money(o.match_per_paycheck)} match` : ""} · {(o.rate * 100).toFixed(1)}% · <span style={{ color: o.guaranteed ? "var(--safe-ink)" : "var(--bill-ink)", fontWeight: 700 }}>{o.guaranteed ? "guaranteed" : "not guaranteed"}</span>
                    </span>
                    <span className="num" style={{ marginLeft: "auto", fontWeight: 800, fontSize: 15 }}>{money(o.after_tax)}</span>
                  </div>
                  <div style={{ position: "relative", height: 14, background: "var(--surface-2)", borderRadius: 4, overflow: "hidden" }} title={`${money(o.after_tax)} kept of ${money(o.value)}; ${money(o.contributed)} put in`}>
                    <div style={{ width: `${Math.max(2, (o.after_tax / best) * 100)}%`, height: "100%", background: o.guaranteed ? "var(--safe)" : "var(--paycheck)" }} />
                  </div>
                  <div style={{ fontSize: 11, color: "var(--ink-2)", lineHeight: 1.4 }}>
                    kept after tax, of {money(o.value)} · reachable {o.reachable} · {o.tax}{o.note ? ` · ${o.note}` : ""}
                  </div>
                </div>
                <Link to={o.link} className="btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", minHeight: 34, padding: "4px 10px", fontSize: 12, whiteSpace: "nowrap" }}>{o.action} →</Link>
              </div>
            ))}
          </div>
          {(data.milestone_years?.length ?? 0) > 1 && (
            <div style={{ overflowX: "auto" }}>
              <table className="ledger" style={{ fontSize: 12 }}>
                <thead><tr><th>Kept after tax</th>{(data.milestone_years ?? []).map((y) => <th key={y} style={{ textAlign: "right" }}>after {y} {y === 1 ? "year" : "years"}</th>)}</tr></thead>
                <tbody>
                  {data.options.map((o) => (
                    <tr key={o.id}>
                      <td style={{ fontWeight: o.rank === 1 ? 800 : 500 }}>{o.name}</td>
                      {(o.milestones ?? []).map((m) => <td key={m.year} className="num" style={{ textAlign: "right", fontWeight: o.rank === 1 ? 800 : 500 }}>{money(m.after_tax)}</td>)}
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          <div style={{ fontSize: 11, color: "var(--muted)" }}>{data.not_advice}</div>
        </>
      )}
    </section>
  );
}

export function Outlook() {
  const { data, reload, error } = useApi<Outlook>("/api/v1/projections");
  const [real, setReal] = useState(false);
  const [form, setForm] = useState({ savings_apy: "", low: "", mid: "", high: "", inflation: "", years_to_retirement: "", extra_savings: "", contribution: "", match: "", withdrawal: "" });
  const [note, setNote] = useState<string | null>(null);
  useEffect(() => {
    if (data) {
      const a = data.assumptions;
      setForm({
        savings_apy: (a.savings_apy * 100).toString(), low: (a.returns.low * 100).toString(), mid: (a.returns.mid * 100).toString(), high: (a.returns.high * 100).toString(),
        inflation: (a.inflation * 100).toString(), years_to_retirement: a.years_to_retirement?.toString() ?? "", extra_savings: a.extra_savings_per_paycheck ? String(a.extra_savings_per_paycheck) : "",
        contribution: a.investment_contribution_per_paycheck !== null ? String(a.investment_contribution_per_paycheck) : "", match: a.employer_match_per_paycheck ? String(a.employer_match_per_paycheck) : "", withdrawal: (a.withdrawal_rate * 100).toString(),
      });
    }
  }, [data]);

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const num = (s: string) => (s.trim() === "" ? null : Number(s));
    const body: Record<string, unknown> = {
      savings_apy: num(form.savings_apy) === null ? null : Number(form.savings_apy) / 100,
      returns: { low: num(form.low) === null ? null : Number(form.low) / 100, mid: num(form.mid) === null ? null : Number(form.mid) / 100, high: num(form.high) === null ? null : Number(form.high) / 100 },
      inflation: num(form.inflation) === null ? null : Number(form.inflation) / 100,
      withdrawal_rate: num(form.withdrawal) === null ? null : Number(form.withdrawal) / 100,
      extra_savings_per_paycheck: num(form.extra_savings) ?? 0,
      employer_match_per_paycheck: num(form.match) ?? 0,
    };
    if (form.years_to_retirement.trim() === "") body.clear_retirement = true;
    else body.years_to_retirement = Number(form.years_to_retirement);
    if (form.contribution.trim() === "") body.use_paystubs_for_contributions = true;
    else body.investment_contribution_per_paycheck = Number(form.contribution);
    try {
      await api("/api/v1/projections/assumptions", { method: "PUT", body: JSON.stringify(body) });
      setNote("Assumptions kept; the outlook is recomputed.");
      reload();
    } catch (err) {
      setNote((err as Error).message);
    }
  }

  const d = data;
  const field = (label: string, key: keyof typeof form, suffix: string, placeholder = "") => (
    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span className="lab">{label}</span>
      <div style={{ display: "flex", alignItems: "center", gap: 6 }}>
        <input className="input num" inputMode="decimal" value={form[key]} onChange={(e) => setForm({ ...form, [key]: e.target.value })} placeholder={placeholder} style={{ width: 110 }} />
        <span style={{ fontSize: 12, color: "var(--muted)" }}>{suffix}</span>
      </div>
    </label>
  );

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 980 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Outlook</h1>
        <span style={{ fontSize: 13, color: "var(--ink-2)" }}>Savings and investments carried forward at rates you set. A rate is a guess; the contributions and balances are real.</span>
      </div>
      {error && <div style={{ color: "var(--critical)" }}>{error.message}</div>}
      {d && (
        <>
          <WhereToPut retireIn={d.investments.years_to_retirement} />
          <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", gap: 18, flexWrap: "wrap", alignItems: "baseline" }}>
              <strong style={{ fontSize: 15 }}>Investments</strong>
              <span style={{ fontSize: 13, color: "var(--ink-2)" }}>
                {money(d.investments.now)} today, {money(d.investments.per_paycheck)} a paycheck
                {d.investments.contribution_source === "paystubs" ? ` from the paystubs (${d.investments.paystub_lines.map((l) => l.name).join(", ")})` : d.investments.contribution_source === "assumption" ? " as you set it" : " (no contribution on record)"}
                {d.investments.employer_match_per_paycheck > 0 ? `, ${money(d.investments.employer_match_per_paycheck)} of it the employer's match` : ""} · {d.paychecks_per_year} paychecks a year
              </span>
              <div style={{ flexGrow: 1 }} />
              <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12 }}><input type="checkbox" checked={real} onChange={(e) => setReal(e.target.checked)} /> in today's dollars ({pct(d.assumptions.inflation)} inflation)</label>
            </div>
            {d.investments.now === 0 && d.investments.per_paycheck === 0 ? (
              <div style={{ fontSize: 13, color: "var(--muted)" }}>Nothing to carry forward yet: add an investment account on <Link to="/investments">Investing</Link>, or set a contribution below.</div>
            ) : (
              <Chart scenarios={d.investments.scenarios} retireIn={d.investments.years_to_retirement} real={real} />
            )}
            <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)" }}>
              {d.investments.scenarios.map((s) => <span key={s.id} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span className="sw" style={{ background: TONE[s.id], margin: 0 }} />{s.word}, {pct(s.rate)} a year</span>)}
            </div>
            <div style={{ overflowX: "auto" }}>
              <table className="ledger">
                <thead><tr><th>After</th>{d.investments.scenarios.map((s) => <th key={s.id} style={{ textAlign: "right" }}>{s.word} ({pct(s.rate)})</th>)}<th style={{ textAlign: "right" }}>Put in by then</th></tr></thead>
                <tbody>
                  {d.investments.milestone_years.map((year) => {
                    const rows = d.investments.scenarios.map((s) => s.milestones.find((m) => m.year === year));
                    return (
                      <tr key={year}>
                        <td>{year} year{year === 1 ? "" : "s"}</td>
                        {rows.map((r, i) => <td key={i} className="num" style={{ textAlign: "right", fontWeight: 700 }}>{r ? money(real ? r.real : r.balance) : "—"}</td>)}
                        <td className="num" style={{ textAlign: "right", color: "var(--muted)" }}>{rows[0] ? money(rows[0].contributed) : "—"}</td>
                      </tr>
                    );
                  })}
                  {d.investments.years_to_retirement && (
                    <tr style={{ background: "var(--surface-2)" }}>
                      <td><strong>At retirement, {d.investments.years_to_retirement} years</strong><div style={{ fontSize: 11, color: "var(--muted)" }}>a month at a {pct(d.investments.withdrawal_rate)} withdrawal rate</div></td>
                      {d.investments.scenarios.map((s) => (
                        <td key={s.id} className="num" style={{ textAlign: "right", fontWeight: 700 }}>
                          {s.at_retirement ? money(real ? s.at_retirement.real : s.at_retirement.balance) : "—"}
                          <div style={{ fontSize: 11, color: "var(--ink-2)", fontWeight: 500 }}>{s.at_retirement ? `${money(real ? s.at_retirement.monthly_real : s.at_retirement.monthly_at_withdrawal_rate)} a month` : ""}</div>
                        </td>
                      ))}
                      <td className="num" style={{ textAlign: "right", color: "var(--muted)" }}>{d.investments.scenarios[0]?.at_retirement ? money(d.investments.scenarios[0].at_retirement.contributed) : "—"}</td>
                    </tr>
                  )}
                </tbody>
              </table>
            </div>
          </section>

          <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
            <div style={{ display: "flex", gap: 18, flexWrap: "wrap", alignItems: "baseline" }}>
              <strong style={{ fontSize: 15 }}>Savings</strong>
              <span style={{ fontSize: 13, color: "var(--ink-2)" }}>
                {money(d.savings.now)} today in {d.savings.accounts.length} savings account{d.savings.accounts.length === 1 ? "" : "s"}, {money(d.savings.per_paycheck)} a paycheck
                {d.savings.goals_per_paycheck > 0 ? ` (${money(d.savings.goals_per_paycheck)} from your goals${d.savings.extra_per_paycheck > 0 ? ` and ${money(d.savings.extra_per_paycheck)} more` : ""})` : d.savings.extra_per_paycheck > 0 ? " as you set it" : ""}
                , earning {pct(d.savings.apy)}
              </span>
            </div>
            <SavingsLine months={d.savings.months} />
            <div style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 13 }}>
              {d.savings.milestones.map((m) => (
                <div key={m.months}><div className="lab">{m.months} months</div><div className="num" style={{ fontWeight: 800, fontSize: 16 }}>{money(m.balance)}</div><div style={{ fontSize: 11, color: "var(--muted)" }}>{money(m.interest)} of it interest</div></div>
              ))}
            </div>
          </section>

          <form onSubmit={save} className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
            <div>
              <strong style={{ fontSize: 15 }}>Assumptions</strong>
              <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Yours to set. Leave the contribution empty to take it from the paystubs; leave retirement empty for no line.</div>
            </div>
            <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
              {field("Savings APY", "savings_apy", "% a year")}
              {field("Cautious return", "low", "% a year")}
              {field("Middle return", "mid", "% a year")}
              {field("Strong return", "high", "% a year")}
              {field("Inflation", "inflation", "% a year")}
              {field("Retirement in", "years_to_retirement", "years", "none")}
              {field("Investment contribution", "contribution", "a paycheck", "from paystubs")}
              {field("Employer match", "match", "a paycheck", "0")}
              {field("Extra savings", "extra_savings", "a paycheck", "0")}
              {field("Withdrawal rate", "withdrawal", "% a year")}
            </div>
            <div style={{ display: "flex", gap: 8, alignItems: "center" }}>
              <button className="btn dark" type="submit">Keep the assumptions</button>
              {note && <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{note}</span>}
            </div>
          </form>
          <div style={{ fontSize: 12, color: "var(--muted)" }}>{d.not_advice}</div>
        </>
      )}
    </div>
  );
}
