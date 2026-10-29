// Live bank links through Plaid (Trial plan) and Teller (developer tier): the bank's own login page opens in
// the vendor's window; the app receives a token and reads with it. Nothing here ever asks for a bank password.
import { Fragment, useEffect, useRef, useState } from "react";
import { Link, useSearchParams } from "react-router-dom";
import { AlertTriangle, CheckCircle2, OctagonAlert } from "lucide-react";
import { api, useApi } from "../api";
import { loadScript, openPlaidLink, resumePlaidLink } from "../plaid";
import { when } from "../format";
import { Where } from "../components/Where";
import { Freshness } from "../components/Freshness";
import { ConfirmButton } from "../components/ConfirmButton";
import { Failed, Loading, Said } from "../components/State";
import { StatusPill } from "../components/Status";

interface LinkRow {
  id: number;
  provider: "plaid" | "teller";
  institution: string;
  needs_login: boolean;
  last_sync_at: string | null;
  last_error: string;
  accounts: { id: number; name: string; kind: string; balance: number | null }[];
  /** Teller's enrollment, so a sign-in that lapsed can be repaired in place (left out by an older server). */
  enrollment_id?: string | null;
}

interface BanksOut {
  providers: {
    plaid: { configured: boolean; environment: string; client_id: string; secret_set: boolean; keys?: { result: string; message: string; environment: string } | null };
    teller: { configured: boolean; environment: string; application_id: string; certificate_set: boolean; private_key_set: boolean };
  };
  links: LinkRow[];
  can_store_secrets: boolean;
  public_url: string;
}

type TellerGlobal = { setup: (opts: { applicationId: string; environment: string; products: string[]; enrollmentId?: string; onSuccess: (enrollment: { accessToken: string; enrollment: { id: string; institution: { name: string } } }) => void; onExit?: () => void }) => { open: () => void } };

// Where a result is said: under the Plaid box, the Teller box, beside one bank link's buttons, or under the list.
type Note = { at: string; text: string; tone: "info" | "error" } | null;

/** What happens to a removed link's accounts: they stay counted (`keep`, the server's default) or stay as history
 * only (`history`: their rows kept, no longer counted). Linking the bank again takes either kind back. */
type KeepAs = "keep" | "history";
const KEEP_AS: { value: KeepAs; label: string; detail: string }[] = [
  { value: "keep", label: "Keep its accounts in the plan", detail: "Their balances stay as last read and keep counting; nothing new comes in. Linking this bank again picks them up." },
  { value: "history", label: "Keep them as history only (not counted)", detail: "Their past transactions are kept; their balances, and a card's debt, stop counting. Linking this bank again brings them back." },
];
const removeQuestion = (l: LinkRow) => (l.provider === "plaid" ? "Remove this bank link? Plaid does not give the trial login slot back." : "Remove this bank link? The app stops reading it; you can link it again.");

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span className="lab">{label}</span>
      {children}
    </label>
  );
}

export function Banks({ onChanged }: { onChanged?: () => void }) {
  const state = useApi<BanksOut>("/api/v1/banks");
  const { data, reload } = state;
  const [plaid, setPlaid] = useState({ client_id: "", secret: "", environment: "" });
  const [teller, setTeller] = useState({ application_id: "", certificate: "", private_key: "", environment: "" });
  const [note, setNote] = useState<Note>(null);
  const [busy, setBusy] = useState<string | null>(null);
  const [syncing, setSyncing] = useState<number | null>(null);
  // The link being removed and what its accounts become, asked in place before the link goes.
  const [removing, setRemoving] = useState<{ id: number; accounts: KeepAs } | null>(null);
  const [params, setParams] = useSearchParams();
  const wanted = { signin: params.get("signin"), sync: params.get("sync"), connect: params.get("connect") };
  const synced = useRef<string | null>(null);
  const say = (at: string, text: string, tone: "info" | "error" = "info") => setNote({ at, text, tone });
  const said = (at: string) => (note?.at === at ? <Said text={note.text} tone={note.tone} /> : null);
  useEffect(() => {
    // A next-step link lands here: a sync runs by itself; a sign-in or a connection is highlighted for the tap it needs.
    if (wanted.sync && data && synced.current !== wanted.sync) {
      synced.current = wanted.sync;
      sync(Number(wanted.sync)).then(() => setParams({}, { replace: true }));
    }
  }, [wanted.sync, data]);  // eslint-disable-line react-hooks/exhaustive-deps
  const resumed = useRef(false);
  useEffect(() => {
    // An OAuth bank sent the browser back here mid-sign-in: Plaid Link reopens and finishes with the same token.
    if (resumed.current) return;
    resumed.current = true;
    resumePlaidLink({ onDone: (m) => { say("plaid", m); reload(); onChanged?.(); setParams({}, { replace: true }); }, onExit: (m) => { if (m) say("plaid", m, "error"); setParams({}, { replace: true }); } })
      .then((did) => { if (did) setBusy("plaid-connect"); })
      .catch((e) => say("plaid", (e as Error).message, "error"))
      .finally(() => setBusy(null));
  }, []);  // eslint-disable-line react-hooks/exhaustive-deps
  const highlight = (on: boolean) => (on ? { outline: "3px solid var(--warning-ink)", outlineOffset: 2 } : {});

  async function savePlaid() {
    setBusy("plaid-save");
    setNote(null);
    try {
      await api("/api/v1/banks/plaid/settings", { method: "PUT", body: JSON.stringify({ client_id: plaid.client_id || undefined, secret: plaid.secret || undefined, environment: plaid.environment || undefined }) });
      setPlaid({ ...plaid, secret: "" });
      say("plaid", "Plaid settings saved; the secret is stored encrypted.");
      reload();
    } catch (e) {
      say("plaid", (e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  }

  async function saveTeller() {
    setBusy("teller-save");
    setNote(null);
    try {
      await api("/api/v1/banks/teller/settings", { method: "PUT", body: JSON.stringify({ application_id: teller.application_id || undefined, certificate: teller.certificate || undefined, private_key: teller.private_key || undefined, environment: teller.environment || undefined }) });
      setTeller({ ...teller, certificate: "", private_key: "" });
      say("teller", "Teller settings saved; the certificate is stored encrypted.");
      reload();
    } catch (e) {
      say("teller", (e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  }

  async function connectPlaid(linkId?: number, purpose: "bank" | "investments" = "bank") {
    const at = linkId ? `link-${linkId}` : "plaid";
    setBusy("plaid-connect");
    setNote(null);
    try {
      await openPlaidLink({ purpose, linkId, onNote: (m) => say(at, m), onDone: (m) => { say(at, m); reload(); onChanged?.(); }, onExit: (m) => { if (m) say(at, m, "error"); setBusy(null); } });
    } catch (e) {
      say(at, (e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  }

  // With a link's enrollment, Teller Connect opens that bank's sign-in again and the same link is repaired.
  async function connectTeller(link?: LinkRow) {
    if (!data) return;
    const at = link ? `link-${link.id}` : "teller";
    setBusy("teller-connect");
    setNote(null);
    try {
      await loadScript("https://cdn.teller.io/connect/connect.js");
      const TellerConnect = (window as unknown as { TellerConnect?: TellerGlobal }).TellerConnect;
      if (!TellerConnect) throw new Error("Teller Connect did not load");
      const connect = TellerConnect.setup({
        applicationId: data.providers.teller.application_id,
        environment: data.providers.teller.environment,
        products: ["transactions", "balance"],
        ...(link?.enrollment_id ? { enrollmentId: link.enrollment_id } : {}),
        onSuccess: async (enrollment) => {
          try {
            const r = await api<{ link: LinkRow; sync: { summary: string }; duplicate?: boolean; said?: string }>("/api/v1/banks/teller/enroll", { method: "POST", body: JSON.stringify({ access_token: enrollment.accessToken, enrollment_id: enrollment.enrollment.id, institution: enrollment.enrollment.institution?.name ?? link?.institution ?? "" }) });
            say(at, r.duplicate && r.said ? r.said : `${r.link.institution || "The bank"} is ${link ? "signed in again" : "linked"}. ${r.sync.summary}`);
            reload();
            onChanged?.();
          } catch (e) {
            say(at, (e as Error).message, "error");
          }
        },
        onExit: () => setBusy(null),
      });
      connect.open();
    } catch (e) {
      say(at, (e as Error).message, "error");
    } finally {
      setBusy(null);
    }
  }

  async function sync(id: number) {
    setSyncing(id);
    setNote(null);
    try {
      const r = await api<{ ok?: boolean; summary: string }>(`/api/v1/banks/${id}/sync`, { method: "POST" });
      say(`link-${id}`, r.summary, r.ok === false ? "error" : "info");
      reload();
      onChanged?.();
    } catch (e) {
      say(`link-${id}`, (e as Error).message, "error");
    } finally {
      setSyncing(null);
    }
  }

  // Asked first by ConfirmButton, which also says a failure beside its buttons.
  async function remove(l: LinkRow, accounts: KeepAs) {
    const r = await api<{ ok: boolean; accounts_retired?: number }>(`/api/v1/banks/${l.id}?accounts=${accounts}`, { method: "DELETE" });
    setRemoving(null);
    const bank = l.institution || "The bank";
    const retired = r.accounts_retired ?? 0;
    say("links", l.accounts.length === 0
      ? `${bank} is removed.`
      : accounts === "history"
        ? `${bank} is removed. ${retired} ${retired === 1 ? "account is" : "accounts are"} kept as history: the transactions stay, and ${retired === 1 ? "it no longer counts" : "they no longer count"}.`
        : `${bank} is removed. Its accounts stay in the plan as last read; linking the bank again picks them up.`);
    reload();
    onChanged?.();
  }

  const p = data?.providers.plaid;
  const t = data?.providers.teller;
  return (
    <section id="banks" className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12, scrollMarginTop: 16 }}>
      <div>
        <h2 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>Banks, live</h2>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Transactions and balances straight from the bank, as often as you choose below. Two free ways to connect: Plaid's Trial plan (ten logins) and Teller's developer tier (a hundred). The bank's own login page opens in the vendor's window; this app only ever holds a read token, encrypted.</div>
      </div>
      {state.error && !data && <Failed error={state.error} what="your bank links" onRetry={reload} card={false} />}
      {!data && !state.error && <Loading what="your bank links" card={false} />}
      {data && data.links.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table className="ledger">
            <thead><tr><th>Bank</th><th>Through</th><th>Accounts</th><th>Last read</th><th><span className="sr-only">Sync or remove</span></th></tr></thead>
            <tbody>
              {data.links.map((l) => (
                <Fragment key={l.id}>
                <tr>
                  <td><strong>{l.institution || "Bank"}</strong>{l.needs_login && <div style={{ marginTop: 4 }}><StatusPill status="crit">Needs a new sign-in</StatusPill></div>}</td>
                  <td style={{ color: "var(--ink-2)" }}>{l.provider === "plaid" ? "Plaid" : "Teller"}</td>
                  <td style={{ color: "var(--ink-2)" }}>{l.accounts.map((a) => `${a.name} (${a.kind})`).join(", ") || "none yet"}</td>
                  <td>
                    {l.last_error
                      ? <span style={{ display: "inline-flex", gap: 4, alignItems: "flex-start", color: "var(--critical-ink)" }}><OctagonAlert size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }} /><span><strong>Last read failed.</strong> {l.last_error}</span></span>
                      : <span style={{ color: "var(--ink-2)", whiteSpace: "nowrap" }}>{l.last_sync_at ? when(l.last_sync_at) : "not yet"}</span>}
                  </td>
                  <td style={{ textAlign: "right" }}>
                    <span style={{ display: "inline-flex", gap: 6, flexWrap: "wrap", justifyContent: "flex-end", alignItems: "flex-start" }}>
                      {l.needs_login && l.provider === "plaid" && <button className="btn dark" type="button" style={highlight(wanted.signin === String(l.id))} onClick={() => connectPlaid(l.id)}>Sign in again at {l.institution || "the bank"}</button>}
                      {l.needs_login && l.provider === "teller" && (l.enrollment_id
                        ? <button className="btn dark" type="button" disabled={!t?.configured || busy !== null} style={highlight(wanted.signin === String(l.id))} onClick={() => connectTeller(l)}>{busy === "teller-connect" ? "Opening…" : `Sign in again at ${l.institution || "the bank"}`}</button>
                        : <span style={{ ...highlight(wanted.signin === String(l.id)), fontSize: 12, color: "var(--ink-2)", maxWidth: 260, textAlign: "left", alignSelf: "center" }}>To sign in again: Remove this link, then press Connect a bank through Teller below and sign in to {l.institution || "the bank"}.</span>)}
                      <button className="btn" type="button" disabled={syncing !== null} onClick={() => sync(l.id)}>{syncing === l.id ? "Reading…" : "Sync now"}</button>
                      {l.accounts.length > 0
                        ? <button className="btn" type="button" aria-label={`Remove the link to ${l.institution || "the bank"}`} aria-expanded={removing?.id === l.id} onClick={() => setRemoving(removing?.id === l.id ? null : { id: l.id, accounts: "keep" })}>Remove</button>
                        : <ConfirmButton label="Remove" name={`Remove the link to ${l.institution || "the bank"}`} question={removeQuestion(l)} yes="Yes, remove it" no="Keep it" onConfirm={() => remove(l, "keep")} />}
                    </span>
                    {note?.at === `link-${l.id}` && <div style={{ marginTop: 4, textAlign: "left" }}>{said(`link-${l.id}`)}</div>}
                  </td>
                </tr>
                {removing?.id === l.id && (
                  <tr>
                    <td colSpan={5}>
                      <div style={{ display: "flex", flexDirection: "column", gap: 8, alignItems: "flex-start" }}>
                        <fieldset style={{ border: 0, padding: 0, margin: 0, display: "flex", flexDirection: "column", gap: 4, minWidth: 0 }}>
                          <legend className="lab" style={{ padding: 0, marginBottom: 4, overflowWrap: "anywhere" }}>Before {l.institution || "the bank"} is removed: what happens to {l.accounts.map((a) => a.name).join(", ")}</legend>
                          {KEEP_AS.map((k) => {
                            const on = removing.accounts === k.value;
                            return (
                              <label key={k.value} style={{ display: "flex", alignItems: "flex-start", gap: 8, minHeight: 32, padding: "6px 10px", borderRadius: 10, cursor: "pointer", fontSize: 13, border: `1px solid ${on ? "var(--ink)" : "var(--hairline)"}`, background: on ? "var(--surface-2)" : "var(--surface)" }}>
                                <input type="radio" name={`remove-${l.id}`} checked={on} onChange={() => setRemoving({ id: l.id, accounts: k.value })} style={{ width: 18, height: 18, margin: "1px 0 0", flexShrink: 0 }} />
                                <span style={{ display: "flex", flexDirection: "column", gap: 1, minWidth: 0 }}>
                                  <strong>{k.label}</strong>
                                  <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{k.detail}</span>
                                </span>
                              </label>
                            );
                          })}
                        </fieldset>
                        <ConfirmButton open label="Remove" question={removeQuestion(l)} yes="Yes, remove it" no="Keep the link" onCancel={() => setRemoving(null)} onConfirm={() => remove(l, removing.accounts)} />
                      </div>
                    </td>
                  </tr>
                )}
                </Fragment>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {said("links")}
      {data && !data.can_store_secrets && (
        <div role="alert" style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 13, color: "var(--critical-ink)" }}>
          <OctagonAlert size={15} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }} />
          <span><strong>FA_DATA_KEY is empty, so tokens cannot be stored.</strong> Set it in the app's environment first: <Link to="/settings/setup#secrets">Setting up › The app's own secrets</Link>.</span>
        </div>
      )}
      <Freshness />
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(300px, 100%), 1fr))", gap: 12 }}>
        <div style={{ border: "1px solid var(--hairline)", borderRadius: 12, padding: 12, display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>Plaid</h3>
            {p?.configured ? <StatusPill status="good">Ready · {p.environment}</StatusPill> : <span style={{ fontSize: 12, color: "var(--ink-2)", fontWeight: 700 }}>not set up</span>}
          </div>
          <Where id="plaid" fill={{ redirect: data?.public_url?.startsWith("https://") ? `${data.public_url.replace(/\/$/, "")}/settings/connectors` : "(needs the app's https address first: Settings › Setting up › Reach it from anywhere)" }} />
          {p?.keys && (
            <div style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 12, color: p.keys.result === "ok" ? "var(--safe-ink)" : "var(--warning-ink)" }}>
              {p.keys.result === "ok" ? <CheckCircle2 size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} /> : <AlertTriangle size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} />}
              <span><strong>{p.keys.result === "ok" ? "Keys accepted" : "Keys refused"}</strong> · {p.keys.message}</span>
            </div>
          )}
          <Field label="Client id"><input className="input" value={plaid.client_id} onChange={(e) => setPlaid({ ...plaid, client_id: e.target.value })} placeholder={p?.client_id || ""} autoComplete="off" /></Field>
          <Field label={`Secret · ${p?.secret_set ? "set" : "not set"}`}><input className="input" type="password" value={plaid.secret} onChange={(e) => setPlaid({ ...plaid, secret: e.target.value })} placeholder={p?.secret_set ? "Paste a new one to replace it" : "Paste the secret"} autoComplete="new-password" /></Field>
          <Field label="Environment">
            <select className="input" value={plaid.environment || p?.environment || "sandbox"} onChange={(e) => setPlaid({ ...plaid, environment: e.target.value })}>
              <option value="sandbox">sandbox (test banks)</option>
              <option value="production">production (real banks)</option>
            </select>
          </Field>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn dark" type="button" disabled={busy !== null} onClick={savePlaid}>{busy === "plaid-save" ? "Saving…" : "Save"}</button>
            <button className="btn" type="button" disabled={!p?.configured || busy !== null} style={highlight(wanted.connect === "plaid")} onClick={() => connectPlaid()}>{busy === "plaid-connect" ? "Opening…" : "Connect a bank through Plaid"}</button>
            <button className="btn" type="button" disabled={!p?.configured || busy !== null} onClick={() => connectPlaid(undefined, "investments")}>Connect a 401(k) or brokerage</button>
          </div>
          {said("plaid")}
        </div>
        <div style={{ border: "1px solid var(--hairline)", borderRadius: 12, padding: 12, display: "flex", flexDirection: "column", gap: 8, minWidth: 0 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap" }}>
            <h3 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>Teller</h3>
            {t?.configured ? <StatusPill status="good">Ready · {t.environment}</StatusPill> : <span style={{ fontSize: 12, color: "var(--ink-2)", fontWeight: 700 }}>not set up</span>}
          </div>
          <Where id="teller" />
          <Field label="Application id"><input className="input" value={teller.application_id} onChange={(e) => setTeller({ ...teller, application_id: e.target.value })} placeholder={t?.application_id || "app_…"} autoComplete="off" /></Field>
          <Field label={`Certificate (PEM) · ${t?.certificate_set ? "set" : "not set"}`}><textarea className="input" rows={3} value={teller.certificate} onChange={(e) => setTeller({ ...teller, certificate: e.target.value })} placeholder="-----BEGIN CERTIFICATE-----" style={{ fontFamily: "ui-monospace, monospace", fontSize: 11 }} /></Field>
          <Field label={`Private key (PEM) · ${t?.private_key_set ? "set" : "not set"}`}><textarea className="input" rows={3} value={teller.private_key} onChange={(e) => setTeller({ ...teller, private_key: e.target.value })} placeholder="-----BEGIN PRIVATE KEY-----" style={{ fontFamily: "ui-monospace, monospace", fontSize: 11 }} /></Field>
          <Field label="Environment">
            <select className="input" value={teller.environment || t?.environment || "development"} onChange={(e) => setTeller({ ...teller, environment: e.target.value })}>
              <option value="sandbox">sandbox (test banks)</option>
              <option value="development">development (real banks, free)</option>
              <option value="production">production</option>
            </select>
          </Field>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn dark" type="button" disabled={busy !== null} onClick={saveTeller}>{busy === "teller-save" ? "Saving…" : "Save"}</button>
            <button className="btn" type="button" disabled={!t?.configured || busy !== null} style={highlight(wanted.connect === "teller")} onClick={() => connectTeller()}>{busy === "teller-connect" ? "Opening…" : "Connect a bank through Teller"}</button>
          </div>
          {said("teller")}
        </div>
      </div>
      <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Also free: the bank's own email alerts, read from the mailbox every fifteen minutes, and the Rocket Money export below.</div>
    </section>
  );
}
