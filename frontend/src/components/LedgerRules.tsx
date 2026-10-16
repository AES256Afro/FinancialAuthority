// Transaction rules: when the merchant contains this, call it that, put it in a category, or hide it.
// A rule is tried first: the app shows how many rows it would change and what they would become, and only then is it
// made. A rule runs over the rows already there and over everything a connector adds later.
import { ArrowRight, Eye, EyeOff } from "lucide-react";
import { useId, useRef, useState, type CSSProperties } from "react";
import { api, useApi } from "../api";
import { money, shortDate } from "../format";
import { ConfirmButton } from "./ConfirmButton";
import { Failed, Said } from "./State";

interface RuleRow {
  id: number;
  kind: "rename" | "categorise" | "hide";
  match_field: string;
  pattern: string;
  value: string;
}

interface Preview {
  count: number;
  total: number;
  sample: { id: number; date: string; merchant: string; category: string; amount: number; becomes: { merchant?: string; category?: string; hidden?: boolean } }[];
  days: number;
}

const KIND_WORD = { rename: "call it", categorise: "put it in", hide: "hide it" };
const FIELD: CSSProperties = { display: "flex", flexDirection: "column", gap: 4 };
const SMALL: CSSProperties = { minHeight: 34, padding: "4px 10px" };

function span(days: number): string {
  return days === 365 ? "the last year" : `the last ${days} days`;
}

// One row as it is now, and as the rule would leave it; the part that changes is in bold.
function Before({ kind, row }: { kind: RuleRow["kind"]; row: Preview["sample"][number] }) {
  if (kind === "hide") return <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><Eye size={12} aria-hidden="true" /><strong>Shown</strong><span style={{ color: "var(--ink-2)" }}> · {row.merchant}</span></span>;
  if (kind === "rename") return <><strong>{row.merchant}</strong><span style={{ color: "var(--ink-2)" }}> · {row.category}</span></>;
  return <><span style={{ color: "var(--ink-2)" }}>{row.merchant} · </span><strong>{row.category}</strong></>;
}

function After({ kind, row }: { kind: RuleRow["kind"]; row: Preview["sample"][number] }) {
  if (kind === "hide") return <span style={{ display: "inline-flex", alignItems: "center", gap: 4 }}><EyeOff size={12} aria-hidden="true" /><strong>Hidden</strong><span style={{ color: "var(--ink-2)" }}> · {row.merchant}</span></span>;
  if (kind === "rename") return <><strong>{row.becomes.merchant}</strong><span style={{ color: "var(--ink-2)" }}> · {row.category}</span></>;
  return <><span style={{ color: "var(--ink-2)" }}>{row.merchant} · </span><strong>{row.becomes.category}</strong></>;
}

export function LedgerRules({ onChanged }: { onChanged: () => void }) {
  const state = useApi<{ rules: RuleRow[] }>("/api/v1/ledger/rules");
  const { data, reload } = state;
  const [open, setOpen] = useState(false);
  const [kind, setKind] = useState<RuleRow["kind"]>("rename");
  const [pattern, setPattern] = useState("");
  const [value, setValue] = useState("");
  const [preview, setPreview] = useState<Preview | null>(null);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const patternBox = useRef<HTMLInputElement>(null);
  const id = useId();

  // Any change to the rule makes the last try out of date.
  function edit(set: (v: string) => void) {
    return (v: string) => {
      set(v);
      setPreview(null);
    };
  }

  async function tryIt(e: React.FormEvent) {
    e.preventDefault();
    setNote(null);
    setBusy(true);
    try {
      setPreview(await api<Preview>("/api/v1/ledger/rules/preview", { method: "POST", body: JSON.stringify({ kind, pattern, value: kind === "hide" ? "" : value, match_field: "merchant" }) }));
    } catch (err) {
      setNote({ text: `Couldn't try it: ${(err as Error).message}`, tone: "error" });
    } finally {
      setBusy(false);
    }
  }
  async function make() {
    setBusy(true);
    try {
      const r = await api<{ changed: number }>("/api/v1/ledger/rules", { method: "POST", body: JSON.stringify({ kind, pattern, value: kind === "hide" ? "" : value, match_field: "merchant" }) });
      setNote({ text: `Rule made; ${r.changed} row${r.changed === 1 ? "" : "s"} changed. New rows will follow it.`, tone: "info" });
      setPattern("");
      setValue("");
      setPreview(null);
      reload();
      onChanged();
    } catch (err) {
      setNote({ text: `Couldn't make the rule: ${(err as Error).message}`, tone: "error" });
    } finally {
      setBusy(false);
    }
  }
  function changeIt() {
    setPreview(null);
    patternBox.current?.focus();
  }
  async function remove(ruleId: number) {
    await api(`/api/v1/ledger/rules/${ruleId}`, { method: "DELETE" });
    setNote({ text: "Rule removed. Rows it already changed stay as they are.", tone: "info" });
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
        <form onSubmit={tryIt} style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end", fontSize: 13 }}>
          <label style={{ ...FIELD, flex: "1 1 160px" }}>
            <span className="lab">When the merchant contains</span>
            <input ref={patternBox} id={`${id}-pattern`} className="input" value={pattern} onChange={(e) => edit(setPattern)(e.target.value)} placeholder="AMZN Mktp" required style={{ ...SMALL, padding: "4px 10px" }} />
          </label>
          <label style={FIELD}>
            <span className="lab">Then</span>
            <select className="input" value={kind} onChange={(e) => { setKind(e.target.value as RuleRow["kind"]); setPreview(null); }} style={{ ...SMALL, padding: "4px 8px" }}>
              <option value="rename">call it</option>
              <option value="categorise">put it in</option>
              <option value="hide">hide it</option>
            </select>
          </label>
          {kind !== "hide" && (
            <label style={{ ...FIELD, flex: "1 1 140px" }}>
              <span className="lab">{kind === "rename" ? "The name to show" : "The category"}</span>
              <input className="input" value={value} onChange={(e) => edit(setValue)(e.target.value)} placeholder={kind === "rename" ? "Amazon" : "Shopping"} required style={SMALL} />
            </label>
          )}
          <button className="btn dark" type="submit" disabled={busy} style={SMALL}>{busy && !preview ? "Trying…" : "Try it first"}</button>
        </form>
      )}
      {open && preview && (
        <div role="region" aria-label="What the rule would change" style={{ border: "1px solid var(--hairline)", borderRadius: 10, padding: 10, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
            <span className="num" style={{ fontSize: 26, fontWeight: 800, lineHeight: 1 }}>{preview.count}</span>
            <span style={{ fontSize: 13 }}>
              row{preview.count === 1 ? "" : "s"} from {span(preview.days)} would change{preview.count > 0 ? `, ${money(preview.total, { cents: true })} in all` : ""}.
            </span>
          </div>
          {preview.count === 0 && (
            <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
              Nothing in {span(preview.days)} has a merchant containing “{pattern}”. The rule would still follow new rows. Check the spelling against a merchant in the list below.
            </div>
          )}
          {preview.sample.length > 0 && (
            <div style={{ overflowX: "auto" }}>
              <table className="ledger">
                <thead><tr><th>When</th><th>Now</th><th><span className="sr-only">becomes</span></th><th>Would become</th><th style={{ textAlign: "right" }}>Amount</th></tr></thead>
                <tbody>
                  {preview.sample.map((r) => (
                    <tr key={r.id}>
                      <td style={{ whiteSpace: "nowrap" }}>{shortDate(r.date)}</td>
                      <td><Before kind={kind} row={r} /></td>
                      <td style={{ width: 20, color: "var(--muted)" }}><ArrowRight size={14} aria-hidden="true" /></td>
                      <td><After kind={kind} row={r} /></td>
                      <td className="num" style={{ textAlign: "right", whiteSpace: "nowrap" }}>{money(r.amount, { cents: true })}</td>
                    </tr>
                  ))}
                </tbody>
              </table>
            </div>
          )}
          {preview.count > preview.sample.length && <div style={{ fontSize: 12, color: "var(--muted)" }}>The newest {preview.sample.length} of the {preview.count} are shown.</div>}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <button className="btn dark" type="button" disabled={busy} onClick={make} style={SMALL}>{busy ? "Making it…" : "Make the rule"}</button>
            <button className="btn" type="button" disabled={busy} onClick={changeIt} style={SMALL}>Change it</button>
          </div>
        </div>
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
