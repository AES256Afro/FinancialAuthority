// Transaction rules: when the merchant contains this, call it that, put it in a category, or hide it.
// A rule runs over the rows already there and over everything a connector adds later.
import { useId, useState } from "react";
import { api, useApi } from "../api";
import { ConfirmButton } from "./ConfirmButton";
import { Failed, Said } from "./State";

interface RuleRow {
  id: number;
  kind: "rename" | "categorise" | "hide";
  match_field: string;
  pattern: string;
  value: string;
}

const KIND_WORD = { rename: "call it", categorise: "put it in", hide: "hide it" };

export function LedgerRules({ onChanged }: { onChanged: () => void }) {
  const state = useApi<{ rules: RuleRow[] }>("/api/v1/ledger/rules");
  const { data, reload } = state;
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<RuleRow["kind"]>("rename");
  const [pattern, setPattern] = useState("");
  const [value, setValue] = useState("");
  const [note, setNote] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const id = useId();

  async function make(e: React.FormEvent) {
    e.preventDefault();
    try {
      const r = await api<{ changed: number }>("/api/v1/ledger/rules", { method: "POST", body: JSON.stringify({ kind, pattern, value, match_field: "merchant" }) });
      setNote({ text: `Rule kept; ${r.changed} row${r.changed === 1 ? "" : "s"} changed. New rows will follow it.`, tone: "info" });
      setPattern("");
      setValue("");
      reload();
      onChanged();
    } catch (err) {
      setNote({ text: (err as Error).message, tone: "error" });
    }
  }
  async function remove(ruleId: number) {
    await api(`/api/v1/ledger/rules/${ruleId}`, { method: "DELETE" });
    reload();
  }

  const rules = data?.rules ?? [];
  return (
    <div className="card" style={{ padding: "10px 14px", display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 13, fontWeight: 800 }}>Rules</h2>
        <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{!data ? "" : rules.length === 0 ? "none yet; a rule cleans a merchant once and keeps it clean" : `${rules.length} rule${rules.length === 1 ? "" : "s"}`}</span>
        <div style={{ flexGrow: 1 }} />
        <button className="btn" type="button" aria-expanded={open} style={{ minHeight: 32, padding: "4px 10px" }} onClick={() => setOpen(!open)}>{open ? "Close" : "Make a rule"}</button>
      </div>
      {state.error && !data && <Failed error={state.error} what="your rules" onRetry={reload} card={false} />}
      {open && (
        <form onSubmit={make} style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center", fontSize: 13 }}>
          <label htmlFor={`${id}-pattern`}>When the merchant contains</label>
          <input id={`${id}-pattern`} className="input" value={pattern} onChange={(e) => setPattern(e.target.value)} placeholder="AMZN Mktp" required style={{ minHeight: 34, padding: "4px 10px", flex: "1 1 140px" }} />
          <select className="input" value={kind} onChange={(e) => setKind(e.target.value as RuleRow["kind"])} style={{ minHeight: 34, padding: "4px 8px" }} aria-label="What to do">
            <option value="rename">call it</option>
            <option value="categorise">put it in</option>
            <option value="hide">hide it</option>
          </select>
          {kind !== "hide" && <input className="input" value={value} onChange={(e) => setValue(e.target.value)} placeholder={kind === "rename" ? "Amazon" : "Shopping"} required style={{ minHeight: 34, padding: "4px 10px", flex: "1 1 120px" }} aria-label={kind === "rename" ? "The name to show" : "The category"} />}
          <button className="btn dark" type="submit" style={{ minHeight: 34, padding: "4px 10px" }}>Keep the rule</button>
        </form>
      )}
      {note && <Said text={note.text} tone={note.tone} />}
      {rules.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 12 }}>
          {rules.map((r) => (
            <div key={r.id} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
              <span style={{ flex: "1 1 200px" }}>merchant contains <code>{r.pattern}</code> → {KIND_WORD[r.kind]}{r.value ? <> <strong>{r.value}</strong></> : null}</span>
              <ConfirmButton label="Remove" question="Remove this rule? Rows it already changed stay as they are." yes="Yes, remove it" no="Keep it" onConfirm={() => remove(r.id)} style={{ minHeight: 32, padding: "4px 10px", fontSize: 12 }} />
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
