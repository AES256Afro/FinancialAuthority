// Goals: save up or pay down, with the pace per paycheck the engine computes to land on the date.
import { useState } from "react";
import { api, useApi } from "../api";
import { money, parseMoney, shortDate } from "../format";
import { ConfirmButton } from "./ConfirmButton";
import { Failed, Loading, Said } from "./State";
import { StatusPill } from "./Status";

interface GoalItem {
  id: number;
  name: string;
  kind: "save" | "paydown";
  target: number;
  funded: number;
  remaining: number;
  target_date: string | null;
  paychecks_left: number | null;
  needed_per_paycheck: number;
  per_paycheck: number;
  on_track: boolean;
  done_by: string | null;
}

interface GoalsOut {
  per_paycheck: number;
  items: GoalItem[];
  safe_to_spend: { amount: number; after_goals?: number; through: string };
}

const AMOUNT_HINT = "Type an amount like 120 or 1200.50";

export function Goals({ onChanged }: { onChanged?: () => void }) {
  const state = useApi<GoalsOut>("/api/v1/goals");
  const { data, reload } = state;
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [when, setWhen] = useState("");
  const [fund, setFund] = useState<Record<number, string>>({});
  const [addSaid, setAddSaid] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const [fundSaid, setFundSaid] = useState<Record<number, { text: string; tone: "info" | "error" }>>({});

  async function add(e: React.FormEvent) {
    e.preventDefault();
    const amount = parseMoney(target);
    if (amount === null || amount <= 0) {
      setAddSaid({ text: AMOUNT_HINT, tone: "error" });
      return;
    }
    try {
      await api("/api/v1/goals", { method: "POST", body: JSON.stringify({ name, target: amount, target_date: when || null }) });
      setName("");
      setTarget("");
      setWhen("");
      setAdding(false);
      setAddSaid({ text: `Goal ${name} added.`, tone: "info" });
      reload();
      onChanged?.();
    } catch (err) {
      setAddSaid({ text: (err as Error).message, tone: "error" });
    }
  }
  async function put(g: GoalItem, text: string) {
    const amount = parseMoney(text);
    if (amount === null || amount <= 0) {
      setFundSaid({ ...fundSaid, [g.id]: { text: AMOUNT_HINT, tone: "error" } });
      return;
    }
    try {
      await api(`/api/v1/goals/${g.id}/fund`, { method: "POST", body: JSON.stringify({ amount }) });
      setFund({ ...fund, [g.id]: "" });
      setFundSaid({ ...fundSaid, [g.id]: { text: `Added ${money(amount, { cents: true })} to ${g.name}.`, tone: "info" } });
      reload();
      onChanged?.();
    } catch (err) {
      setFundSaid({ ...fundSaid, [g.id]: { text: (err as Error).message, tone: "error" } });
    }
  }
  async function remove(id: number) {
    await api(`/api/v1/goals/${id}`, { method: "DELETE" });
    reload();
    onChanged?.();
  }

  if (state.error && !data) return <Failed error={state.error} what="your goals" onRetry={reload} />;
  if (!data) return <Loading what="your goals" />;
  return (
    <section id="goals" className="card" style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 700 }}>Goals</h2>
        {data.items.length > 0 && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{money(data.per_paycheck)} a paycheck together{data.safe_to_spend.after_goals !== undefined ? ` · leaves ${money(data.safe_to_spend.after_goals)} safe to spend` : ""}</span>}
        <div style={{ flexGrow: 1 }} />
        <button className="btn" type="button" aria-expanded={adding} style={{ minHeight: 32, padding: "4px 10px" }} onClick={() => setAdding(!adding)}>{adding ? "Cancel" : "Add a goal"}</button>
      </div>
      {adding && (
        <form onSubmit={add} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 3, flex: "1 1 160px" }}>
            <span className="lab">Goal name</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Emergency fund" required style={{ minHeight: 34, padding: "4px 10px" }} />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 3, flex: "0 1 130px" }}>
            <span className="lab">Target amount</span>
            <input className="input num" inputMode="decimal" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="1200" required style={{ minHeight: 34, padding: "4px 10px" }} />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 3 }}>
            <span className="lab">By when (if you have a date)</span>
            <input className="input" type="date" value={when} onChange={(e) => setWhen(e.target.value)} style={{ minHeight: 34, padding: "4px 10px" }} />
          </label>
          <button className="btn dark" type="submit" style={{ minHeight: 34, padding: "4px 12px" }}>Save the goal</button>
        </form>
      )}
      {addSaid && <Said text={addSaid.text} tone={addSaid.tone} />}
      {data.items.length === 0 && !adding && <div style={{ fontSize: 13, color: "var(--muted)" }}>No goals yet. A goal takes its share from each paycheck before safe to spend is worked out, so the plan keeps it.</div>}
      {data.items.map((g) => {
        const pct = Math.min(100, (g.funded / Math.max(1, g.target)) * 100);
        const said = fundSaid[g.id];
        return (
          <div key={g.id} style={{ display: "flex", flexDirection: "column", gap: 6, borderTop: "1px solid var(--surface-2)", paddingTop: 8 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 13 }}>
              <strong>{g.name}</strong>
              <span style={{ color: "var(--ink-2)" }}>{money(g.funded)} of {money(g.target)}{g.target_date ? ` by ${shortDate(g.target_date)}` : ""}</span>
              {g.remaining <= 0 ? (
                <StatusPill status="good">Done</StatusPill>
              ) : g.on_track ? (
                <StatusPill status="good">On track: {money(g.per_paycheck)} a paycheck</StatusPill>
              ) : (
                <StatusPill status="warn">Behind: {money(g.needed_per_paycheck)} a paycheck would reach it</StatusPill>
              )}
            </div>
            <div style={{ height: 8, background: "var(--surface-2)", borderRadius: 4, overflow: "hidden" }} role="img" aria-label={`${money(g.funded)} of ${money(g.target)}`}>
              <div style={{ width: `${pct}%`, height: "100%", background: g.on_track || g.remaining <= 0 ? "var(--safe)" : "var(--warning)" }} />
            </div>
            <div style={{ display: "flex", gap: 6, alignItems: "flex-end", flexWrap: "wrap" }}>
              <label style={{ display: "flex", flexDirection: "column", gap: 3 }}>
                <span className="lab">Money you put aside</span>
                <input className="input num" inputMode="decimal" value={fund[g.id] ?? ""} onChange={(e) => setFund({ ...fund, [g.id]: e.target.value })} placeholder="50" style={{ width: 130, minHeight: 32, padding: "2px 8px", fontSize: 12 }} />
              </label>
              <button className="btn" type="button" style={{ minHeight: 32, padding: "2px 10px", fontSize: 12 }} onClick={() => put(g, fund[g.id] ?? "")}>Add</button>
              {said && <Said text={said.text} tone={said.tone} />}
              <div style={{ flexGrow: 1 }} />
              <ConfirmButton label="Remove" question={`Remove the goal ${g.name}?`} yes="Yes, remove it" no="Keep it" onConfirm={() => remove(g.id)} style={{ minHeight: 32, padding: "2px 10px", fontSize: 12 }} />
            </div>
          </div>
        );
      })}
    </section>
  );
}
