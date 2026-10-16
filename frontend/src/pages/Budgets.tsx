// Budgets: an amount for each kind of spending, for each paycheck. First the picture (one bar per category: what
// is spent against what is allowed, with a tick where spending would be by today), then the words. Every figure is
// the app's own (GET /api/v1/budgets); the bars only place those figures on a line.
import { useRef, useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { ConfirmButton } from "../components/ConfirmButton";
import { Empty, Said, Show } from "../components/State";
import { StatusPill } from "../components/Status";
import { money, parseMoney, shortDate } from "../format";
import type { BudgetItem, BudgetPlan } from "../types";

const AMOUNT_HINT = "Type an amount like 120 or 1200.50";
const TITLE = { margin: 0, fontSize: 15, fontWeight: 700 } as const;
const SMALL_BTN = { minHeight: 32, padding: "4px 10px" } as const;
// Status fills for the bars; each bar also carries its word and icon in a pill beside it.
const FILL: Record<BudgetItem["status"], string> = { good: "var(--safe)", warn: "var(--warning)", crit: "var(--critical)" };
const LEGEND_WORD: Record<BudgetItem["status"], string> = { good: "on pace", warn: "ahead of pace", crit: "over the budget" };
const HATCH ="repeating-linear-gradient(135deg, var(--critical) 0 4px, var(--critical-soft) 4px 8px)";

type Note = { text: string; tone: "info" | "error" } | null;

// Where a figure sits along one bar, as a share of the bar's width. Placing only: nothing here is shown as money.
function at(value: number, scale: number): string {
  return `${Math.min(100, Math.max(0, (value / scale) * 100))}%`;
}

// The days this paycheck covers, one block per day; the filled blocks are the days gone, today included.
function DayStrip({ plan }: { plan: BudgetPlan }) {
  const { span } = plan;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <div role="img" aria-label={`${span.days_gone} of ${span.days_total} days gone`} style={{ display: "flex", gap: 2, maxWidth: 560 }}>
        {Array.from({ length: span.days_total }, (_, i) => (
          <div key={i} style={{ flex: 1, height: 10, borderRadius: 2, background: i < span.days_gone ? "var(--paycheck)" : "var(--surface-2)" }} />
        ))}
      </div>
      <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
        {span.known ? (
          <>This paycheck: <strong style={{ color: "var(--ink)" }}>{shortDate(span.start)} to {shortDate(span.end)}</strong>, {span.days_gone} of {span.days_total} days gone. Next payday {shortDate(span.next_payday)}.</>
        ) : (
          <>No payday is set up yet, so this counts the last {span.days_total} days, {shortDate(span.start)} to {shortDate(span.end)}. <Link to="/paystubs#paycheck">Set up your pay</Link> to count from payday to payday.</>
        )}
      </div>
    </div>
  );
}

// One category: the bar is what is spent; it fills its track when the budget is used up, and the striped part is
// spent over the budget. The black tick is where spending would be today if it were spread evenly.
function BudgetBar({ b }: { b: BudgetItem }) {
  const scale = Math.max(b.allowed, b.spent, 1);
  const over = b.spent > b.allowed;
  return (
    <div
      role="img"
      aria-label={`${b.category}: ${money(b.spent)} spent of ${money(b.allowed)}; spread evenly, by today it would be ${money(b.expected_by_now)}; ${b.word}`}
      style={{ position: "relative", height: 16, background: "var(--surface-2)", borderRadius: 4 }}
    >
      <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: at(over ? b.allowed : b.spent, scale), background: FILL[b.status], borderRadius: over ? "4px 0 0 4px" : 4 }} />
      {over && <div style={{ position: "absolute", left: at(b.allowed, scale), top: 0, bottom: 0, right: 0, background: HATCH, borderRadius: "0 4px 4px 0" }} />}
      <div style={{ position: "absolute", left: at(b.expected_by_now, scale), top: -4, bottom: -4, width: 2, marginLeft: -1, background: "var(--ink)", borderRadius: 1 }} />
    </div>
  );
}

function Legend() {
  const item = { display: "inline-flex", alignItems: "center", gap: 6, fontSize: 12, color: "var(--ink-2)" } as const;
  return (
    <div style={{ display: "flex", gap: 14, flexWrap: "wrap" }}>
      <span style={item}>
        The bar is what is spent so far:
        {(["good", "warn", "crit"] as const).map((s) => <span key={s} style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><span aria-hidden="true" className="sw" style={{ background: FILL[s], margin: 0 }} />{LEGEND_WORD[s]}</span>)}
      </span>
      <span style={item}><span aria-hidden="true" style={{ display: "inline-block", width: 2, height: 14, background: "var(--ink)", borderRadius: 1 }} />Where spending would be today, if spread evenly over the paycheck</span>
      <span style={item}><span aria-hidden="true" className="sw" style={{ background: HATCH, margin: 0 }} />Spent over the budget</span>
    </div>
  );
}

function BudgetTable({ items }: { items: BudgetItem[] }) {
  const r = { textAlign: "right" } as const;
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="ledger">
        <thead>
          <tr><th>Category</th><th style={r}>Each paycheck</th><th style={r}>Carried in</th><th style={r}>Allowed</th><th style={r}>Spent</th><th style={r}>By today</th><th style={r}>Left</th><th>How it is going</th><th>Carries over</th></tr>
        </thead>
        <tbody>
          {items.map((b) => (
            <tr key={b.category}>
              <td>{b.category}</td>
              <td className="num" style={r}>{money(b.per_paycheck, { cents: true })}</td>
              <td className="num" style={r}>{money(b.carried, { cents: true })}</td>
              <td className="num" style={r}>{money(b.allowed, { cents: true })}</td>
              <td className="num" style={r}>{money(b.spent, { cents: true })}</td>
              <td className="num" style={r}>{money(b.expected_by_now, { cents: true })}</td>
              <td className="num" style={r}>{money(b.left, { cents: true })}</td>
              <td><StatusPill status={b.status}>{b.word}</StatusPill></td>
              <td>{b.rollover === "next" ? "Yes" : "No"}</td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

// Still to spend in the budgets beside safe to spend, on one scale, so "do they fit" is seen before it is read.
function FitBars({ plan }: { plan: BudgetPlan }) {
  const scale = Math.max(plan.totals.left_unspent, plan.safe_to_spend, 1);
  const rows = [
    { label: "Still to spend in your budgets", value: plan.totals.left_unspent, fill: "var(--bill)" },
    { label: "Safe to spend before payday", value: plan.safe_to_spend, fill: plan.safe_to_spend < 0 ? "var(--critical)" : "var(--safe)" },
  ];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8, maxWidth: 560 }}>
      {rows.map((row) => (
        <div key={row.label} style={{ display: "flex", flexDirection: "column", gap: 3 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 13 }}>
            <span>{row.label}</span>
            <strong className="num">{money(row.value)}</strong>
          </div>
          <div role="img" aria-label={`${row.label}: ${money(row.value)}`} style={{ position: "relative", height: 12, background: "var(--surface-2)", borderRadius: 4 }}>
            <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: at(row.value, scale), background: row.fill, borderRadius: 4 }} />
          </div>
        </div>
      ))}
    </div>
  );
}

export function Budgets() {
  const state = useApi<BudgetPlan>("/api/v1/budgets");
  const [table, setTable] = useState(false);
  const [busy, setBusy] = useState<string | null>(null);
  const [listSaid, setListSaid] = useState<Note>(null);
  const [suggestSaid, setSuggestSaid] = useState<Note>(null);
  const [formSaid, setFormSaid] = useState<Note>(null);
  const [category, setCategory] = useState("");
  const [amount, setAmount] = useState("");
  const [rollover, setRollover] = useState<"next" | "none">("next");
  const formRef = useRef<HTMLElement>(null);
  const amountRef = useRef<HTMLInputElement>(null);

  async function save(cat: string, perPaycheck: number, roll: "next" | "none") {
    await api<BudgetPlan>(`/api/v1/budgets/${encodeURIComponent(cat)}`, { method: "PUT", body: JSON.stringify({ per_paycheck: perPaycheck, rollover: roll }) });
    state.reload();
  }

  async function use(cat: string, perPaycheck: number, roll: "next" | "none", say: (n: Note) => void) {
    setBusy(cat);
    say(null);
    try {
      await save(cat, perPaycheck, roll);
      say({ text: `Saved: ${cat}, ${money(perPaycheck, { cents: true })} each paycheck.`, tone: "info" });
    } catch (e) {
      say({ text: `Couldn't save ${cat}: ${(e as Error).message}`, tone: "error" });
    } finally {
      setBusy(null);
    }
  }

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    const cat = category.trim();
    if (!cat) {
      setFormSaid({ text: "Type a category, like Groceries.", tone: "error" });
      return;
    }
    const value = parseMoney(amount);
    if (value === null || value < 0) {
      setFormSaid({ text: AMOUNT_HINT, tone: "error" });
      return;
    }
    setBusy("form");
    setFormSaid(null);
    try {
      await save(cat, value, rollover);
      setFormSaid({ text: `Saved: ${cat}, ${money(value, { cents: true })} each paycheck${rollover === "next" ? ", what is left carries over" : ""}.`, tone: "info" });
      setCategory("");
      setAmount("");
      setRollover("next");
    } catch (err) {
      setFormSaid({ text: `Couldn't save: ${(err as Error).message}`, tone: "error" });
    } finally {
      setBusy(null);
    }
  }

  // "Change" and "Make a budget" fill the form below and move to it.
  function startEdit(cat: string, perPaycheck?: number, roll?: "next" | "none") {
    setCategory(cat);
    setAmount(perPaycheck === undefined ? "" : String(perPaycheck));
    setRollover(roll ?? "next");
    setFormSaid(null);
    formRef.current?.scrollIntoView({ block: "start", behavior: "smooth" });
    window.setTimeout(() => amountRef.current?.focus(), 250);
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 760 }}>
      <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Budgets</h1>
        <p style={{ margin: 0, fontSize: 13, color: "var(--ink-2)" }}>An amount for each kind of spending, for each paycheck, and how much of it is spent so far. Budgets never move money and never change safe to spend.</p>
      </div>
      <Show state={state} what="your budgets">
        {(plan) => {
          const known = Array.from(new Set([...plan.items.map((i) => i.category), ...plan.suggestions.map((s) => s.category), ...plan.unbudgeted.top.map((u) => u.category)])).sort();
          const topMax = Math.max(1, ...plan.unbudgeted.top.map((u) => u.spent));
          return (
            <>
              {plan.items.length > 0 && !plan.fits && plan.fit_note && <StatusPill status="warn">{plan.fit_note}</StatusPill>}

              <section className="card" aria-labelledby="budgets-title" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "baseline" }}>
                  <h2 id="budgets-title" style={TITLE}>This paycheck's budgets</h2>
                  {plan.items.length > 0 && (
                    <button type="button" className="btn" aria-pressed={table} style={{ marginLeft: "auto", ...SMALL_BTN }} onClick={() => setTable((t) => !t)}>{table ? "Show the picture" : "Show as a table"}</button>
                  )}
                </div>
                <DayStrip plan={plan} />
                {plan.items.length === 0 ? (
                  <Empty card={false}>No budgets yet: a budget is an amount you plan to spend on one kind of spending, like groceries, during each paycheck.</Empty>
                ) : table ? (
                  <BudgetTable items={plan.items} />
                ) : (
                  <>
                    <Legend />
                    <div style={{ display: "flex", flexDirection: "column", gap: 0 }}>
                      {plan.items.map((b, i) => (
                        <div key={b.category} style={{ display: "flex", flexDirection: "column", gap: 6, padding: "12px 0", borderTop: i === 0 ? 0 : "1px solid var(--surface-2)" }}>
                          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
                            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 700, flex: "1 1 auto", minWidth: 0 }}>{b.category}</h3>
                            <StatusPill status={b.status}>{b.word}</StatusPill>
                          </div>
                          <BudgetBar b={b} />
                          <div className="num" style={{ fontSize: 13, display: "flex", gap: 12, flexWrap: "wrap" }}>
                            <span><strong>{money(b.spent)}</strong> spent of {money(b.allowed)}</span>
                            <span>{b.left >= 0 ? <><strong>{money(b.left)}</strong> left</> : "nothing left"}</span>
                            <span style={{ color: "var(--ink-2)" }}>spread evenly, by today: {money(b.expected_by_now)}</span>
                          </div>
                          <div style={{ fontSize: 12, color: "var(--muted)" }}>
                            {money(b.per_paycheck)} each paycheck{b.carried > 0 ? ` and ${money(b.carried)} carried from the last paycheck` : ""}. {b.rollover === "next" ? "What is left carries into the next paycheck." : "What is left does not carry over."}
                          </div>
                          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
                            {b.suggested > 0 && b.suggested !== b.per_paycheck && (
                              <button type="button" className="btn" style={SMALL_BTN} disabled={busy === b.category} title={`What you spent on ${b.category} in an average paycheck over the last eight weeks`} onClick={() => use(b.category, b.suggested, b.rollover, setListSaid)}>
                                Use {money(b.suggested, { cents: true })} (your usual)
                              </button>
                            )}
                            <button type="button" className="btn" style={SMALL_BTN} onClick={() => startEdit(b.category, b.per_paycheck, b.rollover)}>Change</button>
                            <ConfirmButton
                              label="Remove"
                              style={SMALL_BTN}
                              question={`Remove the ${b.category} budget? Its spending stays in your transactions.`}
                              yes="Yes, remove it"
                              no="Keep it"
                              onConfirm={async () => {
                                await api<BudgetPlan>(`/api/v1/budgets/${encodeURIComponent(b.category)}`, { method: "DELETE" });
                                setListSaid({ text: `Removed the ${b.category} budget.`, tone: "info" });
                                state.reload();
                              }}
                            />
                          </div>
                        </div>
                      ))}
                    </div>
                  </>
                )}
                <Said text={listSaid?.text ?? null} tone={listSaid?.tone} />
              </section>

              {plan.items.length > 0 && (
                <section className="card" aria-labelledby="together-title" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                    <h2 id="together-title" style={TITLE}>All budgets together</h2>
                    <StatusPill status={plan.fits ? "good" : "warn"}>{plan.fits ? "They fit inside safe to spend" : "More than is safe to spend"}</StatusPill>
                  </div>
                  <FitBars plan={plan} />
                  <div className="num" style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 13 }}>
                    <span>Allowed <strong>{money(plan.totals.allowed)}</strong></span>
                    <span>Spent <strong>{money(plan.totals.spent)}</strong></span>
                    <span>Left <strong>{money(plan.totals.left)}</strong></span>
                  </div>
                </section>
              )}

              {plan.suggestions.length > 0 && (
                <section className="card" aria-labelledby="suggest-title" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
                  <h2 id="suggest-title" style={TITLE}>Suggested budgets</h2>
                  <div style={{ fontSize: 13, color: "var(--ink-2)" }}>What you spent on each in an average paycheck over the last eight weeks. One tap makes it a budget; what is left carries into the next paycheck, and you can change that below.</div>
                  <div style={{ display: "flex", flexDirection: "column" }}>
                    {plan.suggestions.map((s, i) => (
                      <div key={s.category} style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap", padding: "8px 0", borderTop: i === 0 ? 0 : "1px solid var(--surface-2)" }}>
                        <span style={{ flex: "1 1 160px", minWidth: 0, fontSize: 14 }}>{s.category}</span>
                        <button type="button" className="btn dark" style={SMALL_BTN} disabled={busy === s.category} onClick={() => use(s.category, s.per_paycheck, "next", setSuggestSaid)}>
                          Use {money(s.per_paycheck, { cents: true })}
                        </button>
                      </div>
                    ))}
                  </div>
                  <Said text={suggestSaid?.text ?? null} tone={suggestSaid?.tone} />
                </section>
              )}

              <section className="card" aria-labelledby="unbudgeted-title" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
                <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "baseline" }}>
                  <h2 id="unbudgeted-title" style={TITLE}>Spending no budget covers</h2>
                  <strong className="num" style={{ marginLeft: "auto" }}>{money(plan.unbudgeted.spent)} this paycheck so far</strong>
                </div>
                {plan.unbudgeted.top.length === 0 ? (
                  <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Every purchase this paycheck so far falls under a budget.</div>
                ) : (
                  <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
                    {plan.unbudgeted.top.map((u) => (
                      <div key={u.category} style={{ display: "grid", gridTemplateColumns: "minmax(0, 1fr) auto", gap: "4px 10px", alignItems: "center", fontSize: 13 }}>
                        <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={u.category}>{u.category}</span>
                        <strong className="num" style={{ textAlign: "right" }}>{money(u.spent)}</strong>
                        <div role="img" aria-label={`${u.category}: ${money(u.spent)}`} style={{ position: "relative", height: 10, background: "var(--surface-2)", borderRadius: 4 }}>
                          <div style={{ position: "absolute", left: 0, top: 0, bottom: 0, width: at(u.spent, topMax), background: "var(--bill)", borderRadius: 4 }} />
                        </div>
                        <button type="button" className="btn" style={SMALL_BTN} onClick={() => startEdit(u.category)}>Make a budget</button>
                      </div>
                    ))}
                  </div>
                )}
              </section>

              <section ref={formRef} id="budget-form" className="card" aria-labelledby="form-title" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10, scrollMarginTop: 80 }}>
                <h2 id="form-title" style={TITLE}>Add or change a budget</h2>
                <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Pick the category from the list so it matches your transactions. Saving a category that already has a budget changes it.</div>
                <form onSubmit={submit} style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
                    <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 200px", minWidth: 0 }}>
                      <span className="lab">Category</span>
                      <input className="input" list="budget-categories" value={category} onChange={(e) => setCategory(e.target.value)} autoComplete="off" />
                      <datalist id="budget-categories">{known.map((c) => <option key={c} value={c} />)}</datalist>
                    </label>
                    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                      <span className="lab">Amount for each paycheck</span>
                      <input ref={amountRef} className="input num" style={{ width: "min(100%, 160px)" }} value={amount} onChange={(e) => setAmount(e.target.value)} inputMode="decimal" />
                    </label>
                  </div>
                  <fieldset style={{ border: 0, padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 4 }}>
                    <legend className="lab" style={{ padding: 0, marginBottom: 4 }}>Carry what is left into the next paycheck</legend>
                    <div style={{ display: "flex", gap: 16, flexWrap: "wrap" }}>
                      <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, minHeight: 36 }}>
                        <input type="radio" name="budget-rollover" checked={rollover === "next"} onChange={() => setRollover("next")} style={{ width: 18, height: 18 }} />
                        Yes, add what is left to the next paycheck's budget
                      </label>
                      <label style={{ display: "inline-flex", alignItems: "center", gap: 6, fontSize: 13, minHeight: 36 }}>
                        <input type="radio" name="budget-rollover" checked={rollover === "none"} onChange={() => setRollover("none")} style={{ width: 18, height: 18 }} />
                        No, start each paycheck fresh
                      </label>
                    </div>
                  </fieldset>
                  <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
                    <button type="submit" className="btn dark" disabled={busy === "form"}>{busy === "form" ? "Saving…" : "Save the budget"}</button>
                    <Said text={formSaid?.text ?? null} tone={formSaid?.tone} />
                  </div>
                </form>
              </section>

              <div style={{ fontSize: 12, color: "var(--muted)" }}>{plan.not_advice} Spending leaves out transfers, card payments, income and rows you ignored; pending purchases count the day they are made.</div>
            </>
          );
        }}
      </Show>
    </div>
  );
}
