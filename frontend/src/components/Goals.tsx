// Goals: save up or pay down, with the pace per paycheck the engine computes to land on the date.
import { useState } from "react";
import { api, useApi } from "../api";
import { money, shortDate } from "../format";

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

export function Goals({ onChanged }: { onChanged?: () => void }) {
  const { data, reload } = useApi<GoalsOut>("/api/v1/goals");
  const [adding, setAdding] = useState(false);
  const [name, setName] = useState("");
  const [target, setTarget] = useState("");
  const [when, setWhen] = useState("");
  const [fund, setFund] = useState<Record<number, string>>({});

  async function add(e: React.FormEvent) {
    e.preventDefault();
    await api("/api/v1/goals", { method: "POST", body: JSON.stringify({ name, target: Number(target), target_date: when || null }) });
    setName("");
    setTarget("");
    setWhen("");
    setAdding(false);
    reload();
    onChanged?.();
  }
  async function put(id: number, amount: string) {
    if (!amount) return;
    await api(`/api/v1/goals/${id}/fund`, { method: "POST", body: JSON.stringify({ amount: Number(amount) }) });
    setFund({ ...fund, [id]: "" });
    reload();
    onChanged?.();
  }
  async function remove(id: number) {
    await api(`/api/v1/goals/${id}`, { method: "DELETE" });
    reload();
    onChanged?.();
  }

  if (!data) return null;
  return (
    <section id="goals" className="card" style={{ padding: "12px 16px", display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10 }}>
        <strong style={{ fontSize: 14 }}>Goals</strong>
        {data.items.length > 0 && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{money(data.per_paycheck)} a paycheck together{data.safe_to_spend.after_goals !== undefined ? ` · leaves ${money(data.safe_to_spend.after_goals)} safe to spend` : ""}</span>}
        <div style={{ flexGrow: 1 }} />
        <button className="btn" type="button" style={{ minHeight: 32, padding: "4px 10px" }} onClick={() => setAdding(!adding)}>{adding ? "Cancel" : "Add a goal"}</button>
      </div>
      {adding && (
        <form onSubmit={add} style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
          <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="Emergency fund" required style={{ flex: "1 1 140px", minHeight: 34, padding: "4px 10px" }} aria-label="Goal name" />
          <input className="input num" inputMode="decimal" value={target} onChange={(e) => setTarget(e.target.value)} placeholder="$ target" required style={{ width: 110, minHeight: 34, padding: "4px 10px" }} aria-label="Target" />
          <input className="input" type="date" value={when} onChange={(e) => setWhen(e.target.value)} style={{ minHeight: 34, padding: "4px 10px" }} aria-label="By when" />
          <button className="btn dark" type="submit" style={{ minHeight: 34, padding: "4px 10px" }}>Save</button>
        </form>
      )}
      {data.items.length === 0 && !adding && <div style={{ fontSize: 13, color: "var(--muted)" }}>No goals yet. A goal takes its share from each paycheck before safe-to-spend, so the plan keeps it.</div>}
      {data.items.map((g) => {
        const pct = Math.min(100, (g.funded / Math.max(1, g.target)) * 100);
        return (
          <div key={g.id} style={{ display: "flex", flexDirection: "column", gap: 4, borderTop: "1px solid var(--surface-2)", paddingTop: 8 }}>
            <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap", fontSize: 13 }}>
              <strong>{g.name}</strong>
              <span style={{ color: "var(--ink-2)" }}>{money(g.funded)} of {money(g.target)}{g.target_date ? ` by ${shortDate(g.target_date)}` : ""}</span>
              <span style={{ color: g.on_track ? "var(--safe)" : "var(--warning)", fontWeight: 700 }}>{g.remaining <= 0 ? "Done" : g.on_track ? `${money(g.per_paycheck)} a paycheck, on track` : `${money(g.per_paycheck)} a paycheck is short; ${money(g.needed_per_paycheck)} would land it`}</span>
            </div>
            <div style={{ height: 8, background: "var(--surface-2)", borderRadius: 4, overflow: "hidden" }}>
              <div style={{ width: `${pct}%`, height: "100%", background: g.on_track ? "var(--safe)" : "var(--warning)" }} />
            </div>
            <div style={{ display: "flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>
              <input className="input num" inputMode="decimal" value={fund[g.id] ?? ""} onChange={(e) => setFund({ ...fund, [g.id]: e.target.value })} placeholder="$ put aside" style={{ width: 120, minHeight: 30, padding: "2px 8px", fontSize: 12 }} aria-label={`Put aside for ${g.name}`} />
              <button className="btn" type="button" style={{ minHeight: 30, padding: "2px 8px", fontSize: 12 }} onClick={() => put(g.id, fund[g.id] ?? "")}>Record</button>
              <div style={{ flexGrow: 1 }} />
              <button className="btn" type="button" style={{ minHeight: 30, padding: "2px 8px", fontSize: 12 }} onClick={() => remove(g.id)}>Remove</button>
            </div>
          </div>
        );
      })}
    </section>
  );
}
