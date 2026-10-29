// Accounts: every balance with where it came from and how fresh it is, and net worth as what you have less
// what you owe. Below them, what you own beyond the accounts (a home, a car) at the value you give it. The engine's
// numbers; nothing computed here. Once someone else signs in too, each account is marked shared or whose it is,
// with a small control to change the mark.
import { useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { api, useApi, type ApiError, type Loaded } from "../api";
import { ConfirmButton } from "../components/ConfirmButton";
import { Empty, Failed, Loading, Said, useJumpToHash } from "../components/State";
import { StatusPill } from "../components/Status";
import { localToday, money, parseIso, parseMoney, shortDate, when } from "../format";
import { keyToMark, markKey, possessive, roleOf, type MePerson, type PeopleOut, type SharedOut } from "../types-people";
import { WhoTag } from "./Shared";

interface Item {
  id: number;
  name: string;
  institution?: string;
  kind: string;
  balance: number | null;
  balance_at?: string | null;
  age_days?: number | null;
  connector?: string;
  apr?: number | null;
  minimum?: number | null;
  source: string;
  other?: boolean;
  needs_update?: boolean;
  account_id?: number | null;
}

interface Overview {
  as_of: string;
  assets: { total: number; items: Item[] };
  debts: { total: number; items: Item[] };
  net: number;
  stale: string[];
  not_advice: string;
}

type AssetKind = "home" | "car" | "other";
interface Asset {
  id: number;
  name: string;
  kind: AssetKind;
  what: string;
  value_set: number;
  value_on: string;
  value_now: number;
  yearly_change: number | null;
  change_since: number;
  owed_against: number;
  equity: number;
  update_due: string;
  needs_update: boolean;
  note: string;
  source: string;
}

interface Assets {
  items: Asset[];
  total: number;
  owed_total: number;
  needs_update: string[];
}

type Note = { text: string; tone: "info" | "error" } | null;

const KIND_WORD: Record<string, string> = { checking: "checking", savings: "savings", cash: "cash", investment: "investment", card: "card", loan: "loan", bnpl: "pay-later plan", advance: "advance", medical: "medical" };
const ASSET_WORD: Record<AssetKind, string> = { home: "A home", car: "A car", other: "Something else" };
const CONNECTOR_WORD: Record<string, string> = { plaid: "Plaid", teller: "Teller", rocketmoney: "Rocket Money export", demo: "demo data", manual: "entered by hand", mail: "mail", owner: "the value you set" };
// Series colours from the validated palette, in its order; the status colours (green, amber, red) are never used here.
const TONES = ["var(--housing)", "var(--bill)", "var(--debt)", "var(--later)", "var(--saving)"];
const SMALL: CSSProperties = { minHeight: 32, padding: "4px 10px", fontSize: 12 };
const FIELD: CSSProperties = { display: "flex", flexDirection: "column", gap: 4 };
const DAYS_A_MONTH = 30.44;

function freshness(at: string | null | undefined, days: number | null | undefined): string {
  if (!at || days === null || days === undefined) return "no balance yet";
  if (days <= 0) return "today";
  if (days === 1) return "yesterday";
  return `${days} days ago`;
}

function jumpToOther() {
  document.getElementById("other")?.scrollIntoView({ block: "start", behavior: "smooth" });
}

// ---- whose each account is ---------------------------------------------------------------------------------------

interface Choice { key: string; word: string; shared: boolean }
interface Marks {
  keyOf: (accountId: number) => string;
  choices: Choice[];
  /** A person with everything sees the owner's accounts tagged as the owner's; the owner sees them untagged. */
  tagOwner: boolean;
  reload: () => void;
}

/** The account an overview row is: an account row, or a debt kept against an account. Plans, advances and what you
 * own beyond the accounts are not accounts, so they carry no mark. */
function accountIdOf(a: Item): number | null {
  if (a.other) return null;
  if (a.source.startsWith("account:")) return a.id;
  if (a.source.startsWith("debt:") && a.account_id) return a.account_id;
  return null;
}

/** The marks, once someone else signs in: the owner reads them with the people; a person with everything reads
 * which accounts are shared and which are theirs. */
function useMarks(): { marks: Marks | null; error: ApiError | null; retry: () => void } {
  const me = useApi<MePerson>("/api/auth/me");
  const role = roleOf(me.data);
  const pid = me.data?.person_id ?? null;
  const people = useApi<PeopleOut>(me.data && role === "owner" ? "/api/v1/people" : null);
  const sharedAll = useApi<SharedOut>(me.data && role === "everything" ? "/api/v1/shared" : null);
  const theirs = useApi<SharedOut>(me.data && role === "everything" && pid ? `/api/v1/shared?person_id=${pid}` : null);
  const reload = () => {
    people.reload();
    sharedAll.reload();
    theirs.reload();
  };
  // The demo and an older server have no people: the marks stay out of sight.
  const missing = (e: ApiError | null) => Boolean(e && (e.status === 404 || e.status === 401));
  const error = [people.error, sharedAll.error, theirs.error].find((e) => e && !missing(e)) ?? null;
  if (role === "owner" && people.data && people.data.people.length > 0) {
    const d = people.data;
    const keys = new Map(d.accounts.map((a) => [a.id, markKey(a)] as [number, string]));
    return {
      marks: {
        keyOf: (id) => keys.get(id) ?? "owner",
        choices: [{ key: "owner", word: "Yours", shared: false }, { key: "shared", word: "Shared", shared: true }, ...d.people.map((p) => ({ key: `p${p.id}`, word: possessive(p.display_name), shared: false }))],
        tagOwner: false,
        reload,
      },
      error,
      retry: reload,
    };
  }
  if (role === "everything" && pid && sharedAll.data && theirs.data) {
    const keys = new Map<number, string>();
    for (const a of theirs.data.accounts) if (a.whose === "theirs") keys.set(a.id, `p${pid}`);
    for (const a of sharedAll.data.accounts) keys.set(a.id, "shared");
    return {
      marks: {
        keyOf: (id) => keys.get(id) ?? "owner",
        choices: [{ key: "owner", word: possessive(me.data?.owner || "the owner"), shared: false }, { key: "shared", word: "Shared", shared: true }, { key: `p${pid}`, word: "Yours", shared: false }],
        tagOwner: true,
        reload,
      },
      error,
      retry: reload,
    };
  }
  return { marks: null, error, retry: reload };
}

function MarkTag({ id, marks }: { id: number; marks: Marks }) {
  const key = marks.keyOf(id);
  const choice = marks.choices.find((c) => c.key === key);
  if (!choice || (key === "owner" && !marks.tagOwner)) return null;
  return <> <WhoTag shared={choice.shared} word={choice.word} /></>;
}

// Marking an account shared, or someone else's, lets that sign-in see its balance and transactions, so the choice
// in the select is asked about first, in place. Marking it back as yours needs no question.
function MarkControl({ id, name, marks }: { id: number; name: string; marks: Marks }) {
  const key = marks.keyOf(id);
  const [busy, setBusy] = useState(false);
  const [asking, setAsking] = useState<string | null>(null);
  const [said, setSaid] = useState<Note>(null);
  const choiceOf = (k: string) => marks.choices.find((c) => c.key === k);
  async function put(next: string) {
    await api(`/api/v1/accounts/${id}/sharing`, { method: "PUT", body: JSON.stringify(keyToMark(next)) });
    setSaid({ tone: "info", text: `Kept: ${choiceOf(next)?.word ?? next}.` });
    marks.reload();
  }
  async function pick(next: string) {
    setSaid(null);
    if (next === key) return setAsking(null);
    if (choiceOf(next)?.word !== "Yours") return setAsking(next);
    setBusy(true);
    try {
      await put(next);
    } catch (err) {
      setSaid({ tone: "error", text: `Couldn't mark it: ${(err as Error).message}` });
    } finally {
      setBusy(false);
    }
  }
  const asked = asking ? choiceOf(asking) : null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: "flex-start" }}>
      <select className="input" aria-label={`Whose is ${name}`} value={asking ?? key} disabled={busy} onChange={(e) => pick(e.target.value)} style={{ minHeight: 32, padding: "4px 8px", fontSize: 12 }}>
        {marks.choices.map((c) => <option key={c.key} value={c.key}>{c.word}</option>)}
      </select>
      <ConfirmButton
        open={asking !== null}
        onCancel={() => setAsking(null)}
        label=""
        question={asked?.shared ? `Share ${name}? Everyone who signs in will see its balance and transactions.` : `Mark ${name} as ${asked?.word ?? "theirs"}? That person will see its balance and transactions when they sign in.`}
        yes={asked?.shared ? "Yes, share it" : "Yes, mark it"}
        no="Keep it as it is"
        onConfirm={async () => {
          if (!asking) return;
          await put(asking);
          setAsking(null);
        }}
      />
      <Said text={said?.text ?? null} tone={said?.tone} />
    </div>
  );
}

// The picture first: what you have against what you owe, on one scale, a piece per account as wide as its balance.
// The engine's totals set the two bars' lengths and its balances the pieces' widths; nothing is added up here.
function WhereBar({ data }: { data: Overview }) {
  const top = Math.max(1, data.assets.total, data.debts.total);
  const rows = [
    { word: "You have", total: data.assets.total, items: data.assets.items.filter((a) => (a.balance ?? 0) > 0), tone: "var(--paycheck)", ink: "var(--paycheck-ink)" },
    { word: "You owe", total: data.debts.total, items: data.debts.items.filter((a) => (a.balance ?? 0) > 0), tone: "var(--debt)", ink: "var(--debt-ink)" },
  ];
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {rows.map((r) => (
        <div key={r.word} style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <div style={{ display: "flex", justifyContent: "space-between", gap: 8, fontSize: 13 }}>
            <span className="lab">{r.word}</span>
            <strong className="num" style={{ color: r.ink }}>{money(r.total)}</strong>
          </div>
          <div role="img" aria-label={`${r.word} ${money(r.total)}: ${r.items.map((a) => `${a.name} ${money(a.balance ?? 0)}`).join("; ") || "nothing"}`} style={{ height: 26, background: "var(--surface-2)", borderRadius: 6, overflow: "hidden" }}>
            <div style={{ display: "flex", gap: 2, height: "100%", width: `${Math.max(0, Math.min(100, (r.total / top) * 100))}%` }}>
              {r.items.map((a, i) => (
                <div key={a.source} title={`${a.name}: ${money(a.balance ?? 0)}`} style={{ flex: `${a.balance ?? 0} 1 0`, minWidth: 3, background: r.tone, opacity: i % 2 ? 0.7 : 1 }} />
              ))}
            </div>
          </div>
        </div>
      ))}
      <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Both bars are on one scale. Each piece is one account or debt, as wide as its balance; the lists below name each one.</span>
    </div>
  );
}

function Group({ title, items, total, tone, marks }: { title: string; items: Item[]; total: number; tone: string; marks: Marks | null }) {
  const cols = marks ? 6 : 5;
  return (
    <section className="card" style={{ padding: "6px 14px", overflowX: "auto" }}>
      <table className="ledger">
        <thead><tr><th>{title}</th><th>Kind</th><th>From</th><th>Balance as of</th>{marks && <th>Whose</th>}<th style={{ textAlign: "right" }}>Balance</th></tr></thead>
        <tbody>
          {items.map((a) => {
            const id = accountIdOf(a);
            return (
              <tr key={a.source}>
                <td>
                  {a.other
                    ? <Link to={{ hash: "#other" }} onClick={jumpToOther} style={{ fontWeight: 700 }}>{a.name}</Link>
                    : <strong>{a.name}</strong>}
                  {a.institution ? <span style={{ color: "var(--muted)" }}> · {a.institution}</span> : null}
                  {marks && id !== null && <MarkTag id={id} marks={marks} />}
                </td>
                <td style={{ color: "var(--ink-2)" }}>{a.other ? (ASSET_WORD[a.kind as AssetKind] ?? a.kind).toLowerCase() : KIND_WORD[a.kind] ?? a.kind}</td>
                <td style={{ color: "var(--ink-2)" }}>{a.connector ? CONNECTOR_WORD[a.connector] ?? a.connector : "added in the app"}</td>
                <td style={{ color: "var(--ink-2)" }} title={a.balance_at ? when(a.balance_at) : undefined}>
                  {a.other && a.balance_at
                    ? <span style={{ display: "inline-flex", gap: 6, alignItems: "center", flexWrap: "wrap" }}>set {shortDate(a.balance_at.slice(0, 10))}{a.needs_update && <StatusPill status="warn">Update the value</StatusPill>}</span>
                    : a.connector ? freshness(a.balance_at, a.age_days) : "—"}
                </td>
                {marks && <td>{id !== null ? <MarkControl id={id} name={a.name} marks={marks} /> : <span style={{ color: "var(--muted)" }}>—</span>}</td>}
                <td className="num" style={{ textAlign: "right", fontWeight: 700 }}>{a.balance === null || a.balance === undefined ? "—" : money(a.balance)}</td>
              </tr>
            );
          })}
          {items.length === 0 && <tr><td colSpan={cols} style={{ color: "var(--ink-2)" }}>Nothing here yet. <Link to="/settings/connectors">Connect a bank on Settings › Connections</Link>.</td></tr>}
          <tr><td colSpan={cols - 1} style={{ fontWeight: 800 }}>Total</td><td className="num" style={{ textAlign: "right", fontWeight: 800, color: tone }}>{money(total)}</td></tr>
        </tbody>
      </table>
    </section>
  );
}

const striped = (tone: string) => `repeating-linear-gradient(135deg, ${tone} 0 3px, var(--surface) 3px 7px)`;

// The picture: one bar, a piece per asset as wide as what it is worth; within each piece, the solid part is yours and
// the striped part is what is owed against it. Widths are the engine's figures used as flex weights.
function AssetBar({ items, tones }: { items: Asset[]; tones: Map<number, string> }) {
  const shown = items.filter((a) => a.value_now > 0);
  if (shown.length === 0) return null;
  const owing = shown.some((a) => a.owed_against > 0);
  const label = shown.map((a) => `${a.name} ${money(a.value_now)}${a.owed_against > 0 ? `, ${money(a.owed_against)} owed against it` : ""}`).join("; ");
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div role="img" aria-label={`What each is worth now: ${label}`} style={{ display: "flex", gap: 2, height: 34, borderRadius: 8, overflow: "hidden" }}>
        {shown.map((a) => {
          const tone = tones.get(a.id) ?? "var(--muted)";
          return (
            <div key={a.id} title={`${a.name}: ${money(a.value_now)}${a.owed_against > 0 ? `, ${money(a.owed_against)} owed against it` : ""}`} style={{ flex: `${a.value_now} 1 0`, minWidth: 6, display: "flex" }}>
              {a.equity > 0 && <div style={{ flex: `${a.equity} 1 0`, background: tone }} />}
              {a.owed_against > 0 && <div style={{ flex: `${Math.min(a.owed_against, a.value_now)} 1 0`, background: striped(tone) }} />}
            </div>
          );
        })}
      </div>
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)" }}>
        {shown.map((a) => (
          <span key={a.id} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span className="sw" style={{ background: tones.get(a.id), margin: 0 }} aria-hidden="true" />
            {a.name} <span className="num" style={{ fontWeight: 700, color: "var(--ink)" }}>{money(a.value_now)}</span>
          </span>
        ))}
      </div>
      {owing && (
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, color: "var(--muted)" }}>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span className="sw" style={{ background: "var(--ink-2)", margin: 0 }} aria-hidden="true" />Solid: yours after what is owed</span>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span className="sw" style={{ background: striped("var(--ink-2)"), border: "1px solid var(--ink-2)", margin: 0 }} aria-hidden="true" />Striped: owed against it</span>
        </div>
      )}
    </div>
  );
}

function share(rate: number): string {
  return `${Math.round(Math.abs(rate) * 1000) / 10}%`;
}

function changeWords(a: Asset): string {
  if (a.change_since < 0) return `It has lost ${money(-a.change_since)} since you set it.`;
  if (a.change_since > 0) return `It has gained ${money(a.change_since)} since you set it.`;
  return "It is still the value you set.";
}

function rateWords(a: Asset): string {
  if (a.yearly_change === null || a.yearly_change === 0) return "It keeps that value until you change it.";
  return a.yearly_change < 0 ? `It loses ${share(a.yearly_change)} of its value a year, a little each day.` : `It gains ${share(a.yearly_change)} a year, a little each day.`;
}

/** The months between when a value was set and when the app asks again: a setting shown back, not money. */
function monthsBetween(from: string, to: string): number {
  const days = Math.round((parseIso(to).getTime() - parseIso(from).getTime()) / 86_400_000);
  return Math.max(1, Math.round(days / DAYS_A_MONTH));
}

function AssetForm({ asset, update, onSaved, onCancel }: { asset: Asset | null; update: boolean; onSaved: (text: string) => void; onCancel: () => void }) {
  const [f, setF] = useState(() =>
    asset
      ? {
          name: asset.name,
          kind: asset.kind,
          value: update ? "" : String(asset.value_set),
          value_on: update ? localToday() : asset.value_on,
          percent: asset.yearly_change === null ? "" : String(Math.round(-asset.yearly_change * 1000) / 10),
          months: String(monthsBetween(asset.value_on, asset.update_due)),
          owed: asset.owed_against ? String(asset.owed_against) : "",
          note: asset.note ?? "",
        }
      : { name: "", kind: "home" as AssetKind, value: "", value_on: localToday(), percent: "", months: "6", owed: "", note: "" },
  );
  const [said, setSaid] = useState<Note>(null);
  const [busy, setBusy] = useState(false);
  const set = (field: keyof typeof f) => (e: React.ChangeEvent<HTMLInputElement | HTMLSelectElement>) => setF({ ...f, [field]: e.target.value });

  function pickKind(kind: AssetKind) {
    // A car loses about 15% a year unless you say otherwise; that guess leaves when the kind changes back.
    const percent = kind === "car" && f.percent.trim() === "" ? "15" : f.kind === "car" && kind !== "car" && f.percent === "15" ? "" : f.percent;
    setF({ ...f, kind, percent });
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const fail = (text: string) => setSaid({ tone: "error", text });
    const value = parseMoney(f.value);
    const owed = f.owed.trim() === "" ? 0 : parseMoney(f.owed);
    const months = Number(f.months);
    const percent = f.percent.trim() === "" ? null : Number(f.percent);
    if (!f.name.trim()) return fail("Give it a name, like Our home or The car.");
    if (value === null || value < 0) return fail("Type its value like 120 or 1200.50.");
    if (owed === null || owed < 0) return fail("Type what is owed against it like 120 or 1200.50, or leave it empty.");
    if (!f.value_on) return fail("Pick the date that value is from.");
    if (percent !== null && (!Number.isFinite(percent) || percent < -50 || percent > 90)) return fail("Type how much it loses a year as a number from 0 to 90, or leave it empty. A minus number (down to -50) means it gains.");
    if (!Number.isInteger(months) || months < 1 || months > 36) return fail("Ask every 1 to 36 months, as a whole number.");
    setBusy(true);
    setSaid(null);
    const body = { name: f.name.trim(), kind: f.kind, value, value_on: f.value_on, yearly_change: percent === null ? null : -percent / 100, update_every_days: Math.round(months * DAYS_A_MONTH), owed, note: f.note.trim() };
    try {
      await api(asset ? `/api/v1/assets/${asset.id}` : "/api/v1/assets", { method: asset ? "PUT" : "POST", body: JSON.stringify(body) });
      onSaved(asset ? (update ? `Kept the new value of ${body.name}.` : `Kept the changes to ${body.name}.`) : `Added ${body.name}. Net worth counts it now.`);
    } catch (err) {
      setSaid({ tone: "error", text: `Couldn't keep it: ${(err as Error).message}` });
    } finally {
      setBusy(false);
    }
  }

  const title = asset ? (update ? `Update the value of ${asset.name}` : `Change ${asset.name}`) : "Add something you own";
  return (
    <form onSubmit={save} style={{ display: "flex", flexDirection: "column", gap: 10, padding: 12, borderRadius: 10, background: "var(--surface-2)" }}>
      <h3 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>{title}</h3>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label style={{ ...FIELD, flex: "2 1 180px" }}>
          <span className="lab">Name</span>
          <input className="input" value={f.name} onChange={set("name")} placeholder="Our home" maxLength={80} required autoFocus={!update} />
        </label>
        <label style={FIELD}>
          <span className="lab">What it is</span>
          <select className="input" value={f.kind} onChange={(e) => pickKind(e.target.value as AssetKind)}>
            {(Object.keys(ASSET_WORD) as AssetKind[]).map((k) => <option key={k} value={k}>{ASSET_WORD[k]}</option>)}
          </select>
        </label>
        <label style={{ ...FIELD, flex: "1 1 130px" }}>
          <span className="lab">What it is worth</span>
          <input className="input num" inputMode="decimal" value={f.value} onChange={set("value")} placeholder="15,000" required autoFocus={update} />
        </label>
        <label style={FIELD}>
          <span className="lab">As of</span>
          <input className="input" type="date" value={f.value_on} max={localToday()} onChange={set("value_on")} required />
        </label>
      </div>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "flex-end" }}>
        <label style={{ ...FIELD, flex: "1 1 150px" }}>
          <span className="lab">Loses value each year (%)</span>
          <input className="input num" inputMode="decimal" value={f.percent} onChange={set("percent")} placeholder={f.kind === "car" ? "15" : "0"} />
        </label>
        <label style={{ ...FIELD, flex: "1 1 150px" }}>
          <span className="lab">Ask me to update it every (months)</span>
          <input className="input num" inputMode="numeric" value={f.months} onChange={set("months")} required />
        </label>
        <label style={{ ...FIELD, flex: "1 1 150px" }}>
          <span className="lab">Owed against it</span>
          <input className="input num" inputMode="decimal" value={f.owed} onChange={set("owed")} placeholder="0" />
        </label>
      </div>
      <label style={FIELD}>
        <span className="lab">Note</span>
        <input className="input" value={f.note} onChange={set("note")} placeholder="Kelley Blue Book, private sale" maxLength={300} />
      </label>
      <div style={{ fontSize: 12, color: "var(--ink-2)" }}>
        {f.kind === "car" ? "A car usually loses about 15% a year; the app lowers its value a little each day from the date above. " : "Leave the yearly loss empty to keep the value as you set it. "}
        What is owed against it only shows how much is yours; the loan itself stays on <Link to="/debts">Debts</Link>.
      </div>
      <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
        <button className="btn dark" type="submit" disabled={busy} style={{ minHeight: 34, padding: "4px 12px" }}>{busy ? "Keeping…" : asset ? "Keep the changes" : "Add it"}</button>
        <button className="btn" type="button" onClick={onCancel} style={{ minHeight: 34, padding: "4px 12px" }}>Cancel</button>
        <Said text={said?.text ?? null} tone={said?.tone} />
      </div>
    </form>
  );
}

function OtherAssets({ state, onChanged }: { state: Loaded<Assets>; onChanged: () => void }) {
  const { data, error, reload } = state;
  // null: no form open; id null: adding; update: opened from "Update the value".
  const [form, setForm] = useState<{ id: number | null; update: boolean } | null>(null);
  const [said, setSaid] = useState<Note>(null);
  const items = data?.items ?? [];
  const tones = new Map(items.map((a, i) => [a.id, TONES[i % TONES.length]] as [number, string]));

  function saved(text: string) {
    setForm(null);
    setSaid({ tone: "info", text });
    onChanged();
  }
  async function remove(a: Asset) {
    await api(`/api/v1/assets/${a.id}`, { method: "DELETE" });
    setSaid({ tone: "info", text: `Removed ${a.name}. Net worth no longer counts it.` });
    onChanged();
  }

  return (
    <section id="other" className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12, scrollMarginTop: 12 }}>
      <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>Beyond the accounts</h2>
        <span style={{ fontSize: 13, color: "var(--ink-2)", flex: "1 1 260px" }}>A home, a car, or anything else worth something, at the value you give it. Net worth counts them.</span>
        {!(form && form.id === null) && <button className="btn" type="button" onClick={() => { setSaid(null); setForm({ id: null, update: false }); }} style={SMALL}>Add something</button>}
      </div>
      {error && !data && <Failed error={error} what="what you own beyond the accounts" onRetry={reload} card={false} />}
      {!data && !error && <Loading what="what you own beyond the accounts" card={false} />}
      {form && form.id === null && <AssetForm asset={null} update={false} onSaved={saved} onCancel={() => setForm(null)} />}
      {data && items.length === 0 && !form && <Empty card={false}>Nothing here yet. Add a home, a car or anything else worth something, and net worth counts it.</Empty>}
      {data && items.length > 0 && (
        <>
          <AssetBar items={items} tones={tones} />
          <div style={{ display: "flex", gap: 24, flexWrap: "wrap", alignItems: "baseline" }}>
            <div><div className="lab">Worth together now</div><div className="num" style={{ fontSize: 22, fontWeight: 800, color: "var(--safe-ink)" }}>{money(data.total)}</div></div>
            {data.owed_total > 0 && <div><div className="lab">Owed against them</div><div className="num" style={{ fontSize: 22, fontWeight: 800, color: "var(--debt-ink)" }}>{money(data.owed_total)}</div></div>}
            {data.needs_update.length > 0 && <StatusPill status="warn">Time for a new value: {data.needs_update.join(", ")}</StatusPill>}
          </div>
          <div style={{ display: "flex", flexDirection: "column" }}>
            {items.map((a) =>
              form?.id === a.id ? (
                <div key={a.id} style={{ padding: "10px 0", borderTop: "1px solid var(--surface-2)" }}>
                  <AssetForm asset={a} update={form.update} onSaved={saved} onCancel={() => setForm(null)} />
                </div>
              ) : (
                <div key={a.id} style={{ display: "flex", flexDirection: "column", gap: 4, padding: "10px 0", borderTop: "1px solid var(--surface-2)" }}>
                  <div style={{ display: "flex", alignItems: "baseline", gap: 8, flexWrap: "wrap" }}>
                    <span className="sw" style={{ background: tones.get(a.id), margin: 0 }} aria-hidden="true" />
                    <h3 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>{a.name}</h3>
                    <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{a.what}</span>
                    <div style={{ flexGrow: 1 }} />
                    <span className="num" style={{ fontSize: 20, fontWeight: 800 }}>{money(a.value_now)}</span>
                    <span style={{ fontSize: 12, color: "var(--muted)" }}>now</span>
                  </div>
                  <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
                    You set it at {money(a.value_set)} on {shortDate(a.value_on)}. {changeWords(a)} {rateWords(a)}
                  </div>
                  {a.owed_against > 0 && (
                    <div style={{ fontSize: 13, color: "var(--ink-2)" }}>
                      Owed against it <span className="num">{money(a.owed_against)}</span>; yours after that (<Link to="/glossary#equity">equity</Link>) <strong className="num" style={{ color: "var(--ink)" }}>{money(a.equity)}</strong>{a.equity < 0 ? ", so more is owed than it is worth" : ""}.
                    </div>
                  )}
                  {a.note && <div style={{ fontSize: 12, color: "var(--muted)" }}>Note: {a.note}</div>}
                  <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
                    {a.needs_update
                      ? (
                        <button type="button" onClick={() => { setSaid(null); setForm({ id: a.id, update: true }); }} style={{ background: "none", border: 0, padding: 0, minHeight: 32, font: "inherit", cursor: "pointer" }}>
                          <StatusPill status="warn">Update the value: it was set {shortDate(a.value_on)}</StatusPill>
                        </button>
                      )
                      : <span style={{ fontSize: 12, color: "var(--muted)" }}>The app asks for a new value on {shortDate(a.update_due)}.</span>}
                    <div style={{ flexGrow: 1 }} />
                    <button className="btn" type="button" onClick={() => { setSaid(null); setForm({ id: a.id, update: false }); }} style={SMALL}>Edit</button>
                    <ConfirmButton label="Remove" question={`Remove ${a.name}? Net worth stops counting it.`} yes="Yes, remove it" no="Keep it" onConfirm={() => remove(a)} style={SMALL} />
                  </div>
                </div>
              ),
            )}
          </div>
        </>
      )}
      <Said text={said?.text ?? null} tone={said?.tone ?? "info"} />
    </section>
  );
}

export function Accounts() {
  const { data, error, reload } = useApi<Overview>("/api/v1/accounts/overview");
  const assets = useApi<Assets>("/api/v1/assets");
  const { marks, error: marksError, retry: marksRetry } = useMarks();
  useJumpToHash(data && assets.data);

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 980 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Accounts</h1>
      {error && !data && <Failed error={error} what="your accounts" onRetry={reload} />}
      {!data && !error && <Loading what="your accounts" />}
      {data && (
        <>
          <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 14 }}>
            <WhereBar data={data} />
            <div style={{ display: "flex", gap: 24, flexWrap: "wrap", alignItems: "baseline" }}>
              <div><div className="lab"><Link to="/glossary#net-worth" style={{ color: "inherit" }}>Net worth</Link>: what you have less what you owe</div><div className="num" style={{ fontSize: 34, fontWeight: 800, letterSpacing: "-0.02em", lineHeight: 1.1, color: data.net < 0 ? "var(--critical-ink)" : "var(--ink)" }}>{money(data.net)}</div></div>
              {data.stale.length > 0 && <StatusPill status="warn">Balance may be out of date: {data.stale.join(", ")}</StatusPill>}
            </div>
          </section>
          {marksError && <Failed error={marksError} what="which accounts are shared" onRetry={marksRetry} />}
          {marks && <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Whose: a shared account is seen by everyone who signs in; an account marked for one person is seen by that person; anyone with everything sees every account. <Link to="/shared">See the shared accounts</Link>.</div>}
          <Group title="What you have" items={data.assets.items} total={data.assets.total} tone="var(--safe-ink)" marks={marks} />
          <Group title="What you owe" items={data.debts.items} total={data.debts.total} tone="var(--debt-ink)" marks={marks} />
        </>
      )}
      <OtherAssets state={assets} onChanged={() => { assets.reload(); reload(); }} />
      {data && <div style={{ fontSize: 12, color: "var(--muted)" }}>{data.not_advice} Live balances come from your bank connections; the rest from what you added in the app, from exports, and from the values you set beyond the accounts.</div>}
    </div>
  );
}
