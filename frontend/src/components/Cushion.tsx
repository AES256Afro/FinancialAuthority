// The safety cushion (buffer): money kept in checking that the plan never spends. The owner sets its size here;
// safe to spend is worked out with it taken off.
import { ShieldCheck } from "lucide-react";
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { money, parseMoney } from "../format";
import { Said } from "./State";

export function Cushion({ onSaved }: { onSaved?: () => void }) {
  const { data, reload } = useApi<{ buffer: number }>("/api/v1/settings");
  const [text, setText] = useState("");
  const [said, setSaid] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  useEffect(() => {
    if (data) setText(String(data.buffer ?? 200));
  }, [data]);
  async function save() {
    const value = parseMoney(text);
    if (value === null || value < 0 || value > 100000) {
      setSaid({ text: "Type an amount like 200 or 350.", tone: "error" });
      return;
    }
    try {
      await api("/api/v1/settings/buffer", { method: "PUT", body: JSON.stringify({ value }) });
      setSaid({ text: `Kept. Safe to spend now keeps ${money(value)} back.`, tone: "info" });
      reload();
      onSaved?.();
    } catch (e) {
      setSaid({ text: (e as Error).message, tone: "error" });
    }
  }
  return (
    <section className="card" aria-labelledby="cushion-title" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 8 }}>
      <h2 id="cushion-title" style={{ margin: 0, fontSize: 14, fontWeight: 800, display: "flex", alignItems: "center", gap: 6 }}><ShieldCheck size={16} aria-hidden="true" />Safety cushion</h2>
      <div style={{ fontSize: 13, color: "var(--ink-2)", maxWidth: "70ch" }}>
        Money kept in checking that the plan never spends, for the surprise you did not see coming. Safe to spend is worked out with it already taken off. <Link to="/glossary#buffer">What the cushion is</Link>
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "flex-end", flexWrap: "wrap" }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">Cushion, in dollars</span>
          <input className="input" inputMode="decimal" value={text} onChange={(e) => setText(e.target.value)} style={{ width: 140 }} />
        </label>
        <button type="button" className="btn dark" onClick={save}>Save the cushion</button>
        {said && <Said text={said.text} tone={said.tone} />}
      </div>
    </section>
  );
}
