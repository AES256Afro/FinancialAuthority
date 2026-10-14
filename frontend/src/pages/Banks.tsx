// Live bank links through Plaid (Trial plan) and Teller (developer tier): the bank's own login page opens in
// the vendor's window; the app receives a token and reads with it. Nothing here ever asks for a bank password.
import { useState } from "react";
import { api, useApi } from "../api";

interface LinkRow {
  id: number;
  provider: "plaid" | "teller";
  institution: string;
  needs_login: boolean;
  last_sync_at: string | null;
  last_error: string;
  accounts: { id: number; name: string; kind: string; balance: number | null }[];
}

interface BanksOut {
  providers: {
    plaid: { configured: boolean; environment: string; client_id: string; secret_set: boolean };
    teller: { configured: boolean; environment: string; application_id: string; certificate_set: boolean; private_key_set: boolean };
  };
  links: LinkRow[];
  can_store_secrets: boolean;
  public_url: string;
}

const scripts: Record<string, Promise<void>> = {};
function loadScript(src: string): Promise<void> {
  if (!scripts[src]) {
    scripts[src] = new Promise((resolve, reject) => {
      const el = document.createElement("script");
      el.src = src;
      el.async = true;
      el.onload = () => resolve();
      el.onerror = () => reject(new Error(`Could not load ${src}`));
      document.head.appendChild(el);
    });
  }
  return scripts[src];
}

type PlaidGlobal = { create: (opts: { token: string; onSuccess: (publicToken: string, metadata: { institution?: { name?: string } }) => void; onExit?: (err: unknown) => void }) => { open: () => void } };
type TellerGlobal = { setup: (opts: { applicationId: string; environment: string; products: string[]; onSuccess: (enrollment: { accessToken: string; enrollment: { id: string; institution: { name: string } } }) => void; onExit?: () => void }) => { open: () => void } };

function Field({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span className="lab">{label}</span>
      {children}
    </label>
  );
}

export function Banks({ onChanged }: { onChanged?: () => void }) {
  const { data, reload } = useApi<BanksOut>("/api/v1/banks");
  const [plaid, setPlaid] = useState({ client_id: "", secret: "", environment: "" });
  const [teller, setTeller] = useState({ application_id: "", certificate: "", private_key: "", environment: "" });
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState<string | null>(null);

  async function savePlaid() {
    setBusy("plaid-save");
    try {
      await api("/api/v1/banks/plaid/settings", { method: "PUT", body: JSON.stringify({ client_id: plaid.client_id || undefined, secret: plaid.secret || undefined, environment: plaid.environment || undefined }) });
      setPlaid({ ...plaid, secret: "" });
      setNote("Plaid settings saved; the secret is stored encrypted.");
      reload();
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function saveTeller() {
    setBusy("teller-save");
    try {
      await api("/api/v1/banks/teller/settings", { method: "PUT", body: JSON.stringify({ application_id: teller.application_id || undefined, certificate: teller.certificate || undefined, private_key: teller.private_key || undefined, environment: teller.environment || undefined }) });
      setTeller({ ...teller, certificate: "", private_key: "" });
      setNote("Teller settings saved; the certificate is stored encrypted.");
      reload();
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function connectPlaid(linkId?: number) {
    setBusy("plaid-connect");
    setNote(null);
    try {
      const made = await api<{ link_token: string }>("/api/v1/banks/plaid/link_token", { method: "POST", body: JSON.stringify({ link_id: linkId ?? null }) });
      await loadScript("https://cdn.plaid.com/link/v2/stable/link-initialize.js");
      const Plaid = (window as unknown as { Plaid?: PlaidGlobal }).Plaid;
      if (!Plaid) throw new Error("Plaid Link did not load");
      const handler = Plaid.create({
        token: made.link_token,
        onSuccess: async (publicToken, metadata) => {
          if (linkId) {
            const r = await api<{ summary: string }>(`/api/v1/banks/${linkId}/sync`, { method: "POST" });
            setNote(`Signed in again. ${r.summary}`);
          } else {
            const r = await api<{ link: LinkRow; sync: { summary: string } }>("/api/v1/banks/plaid/exchange", { method: "POST", body: JSON.stringify({ public_token: publicToken, institution: metadata.institution?.name ?? "" }) });
            setNote(`${r.link.institution || "The bank"} is linked. ${r.sync.summary}`);
          }
          reload();
          onChanged?.();
        },
        onExit: () => setBusy(null),
      });
      handler.open();
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function connectTeller() {
    if (!data) return;
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
        onSuccess: async (enrollment) => {
          const r = await api<{ link: LinkRow; sync: { summary: string } }>("/api/v1/banks/teller/enroll", { method: "POST", body: JSON.stringify({ access_token: enrollment.accessToken, enrollment_id: enrollment.enrollment.id, institution: enrollment.enrollment.institution?.name ?? "" }) });
          setNote(`${r.link.institution || "The bank"} is linked. ${r.sync.summary}`);
          reload();
          onChanged?.();
        },
        onExit: () => setBusy(null),
      });
      connect.open();
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(null);
    }
  }

  async function sync(id: number) {
    const r = await api<{ summary: string }>(`/api/v1/banks/${id}/sync`, { method: "POST" });
    setNote(r.summary);
    reload();
    onChanged?.();
  }

  async function remove(id: number) {
    await api(`/api/v1/banks/${id}`, { method: "DELETE" });
    reload();
    onChanged?.();
  }

  const p = data?.providers.plaid;
  const t = data?.providers.teller;
  return (
    <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
      <div>
        <strong>Banks, live</strong>
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>Transactions and balances straight from the bank, every four hours. Two free doors: Plaid's Trial plan (ten logins) and Teller's developer tier (a hundred). The bank's own login page opens in the vendor's window; this app only ever holds a read token, encrypted.</div>
      </div>
      {data && data.links.length > 0 && (
        <div style={{ overflowX: "auto" }}>
          <table className="ledger">
            <thead><tr><th>Bank</th><th>Through</th><th>Accounts</th><th>Last read</th><th></th></tr></thead>
            <tbody>
              {data.links.map((l) => (
                <tr key={l.id}>
                  <td><strong>{l.institution || "Bank"}</strong>{l.needs_login && <div style={{ fontSize: 11, color: "var(--critical)" }}>Needs a new sign-in</div>}</td>
                  <td style={{ color: "var(--ink-2)" }}>{l.provider === "plaid" ? "Plaid" : "Teller"}</td>
                  <td style={{ color: "var(--ink-2)" }}>{l.accounts.map((a) => `${a.name} (${a.kind})`).join(", ") || "none yet"}</td>
                  <td style={{ color: l.last_error ? "var(--critical)" : "var(--ink-2)" }}>{l.last_error || (l.last_sync_at ? l.last_sync_at.slice(0, 16).replace("T", " ") : "not yet")}</td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                    {l.needs_login && l.provider === "plaid" && <button className="btn" type="button" onClick={() => connectPlaid(l.id)}>Sign in again</button>}{" "}
                    <button className="btn" type="button" onClick={() => sync(l.id)}>Sync now</button> <button className="btn" type="button" onClick={() => remove(l.id)}>Remove</button>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {data && !data.can_store_secrets && <div style={{ fontSize: 13, color: "var(--critical)" }}>FA_DATA_KEY is empty, so tokens cannot be stored. Set it in the app's environment first.</div>}
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(300px, 1fr))", gap: 12 }}>
        <div style={{ border: "1px solid var(--hairline)", borderRadius: 12, padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <strong>Plaid</strong>
            <span style={{ fontSize: 12, color: p?.configured ? "var(--safe)" : "var(--muted)", fontWeight: 700 }}>{p?.configured ? `ready · ${p.environment}` : "not set up"}</span>
          </div>
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Free developer account at dashboard.plaid.com; the Trial plan gives ten bank logins, and removing one does not free its slot. Paste the client id and the secret here, never into a chat.</div>
          <Field label="Client id"><input className="input" value={plaid.client_id} onChange={(e) => setPlaid({ ...plaid, client_id: e.target.value })} placeholder={p?.client_id || ""} autoComplete="off" /></Field>
          <Field label={`Secret · ${p?.secret_set ? "set" : "not set"}`}><input className="input" type="password" value={plaid.secret} onChange={(e) => setPlaid({ ...plaid, secret: e.target.value })} placeholder={p?.secret_set ? "Paste a new one to replace it" : "Paste the secret"} autoComplete="new-password" /></Field>
          <Field label="Environment">
            <select className="input" value={plaid.environment || p?.environment || "sandbox"} onChange={(e) => setPlaid({ ...plaid, environment: e.target.value })}>
              <option value="sandbox">sandbox (test banks)</option>
              <option value="production">production (real banks)</option>
            </select>
          </Field>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
            <button className="btn dark" type="button" disabled={busy !== null} onClick={savePlaid}>Save</button>
            <button className="btn" type="button" disabled={!p?.configured || busy !== null} onClick={() => connectPlaid()}>{busy === "plaid-connect" ? "Opening…" : "Connect a bank through Plaid"}</button>
          </div>
        </div>
        <div style={{ border: "1px solid var(--hairline)", borderRadius: 12, padding: 12, display: "flex", flexDirection: "column", gap: 8 }}>
          <div style={{ display: "flex", alignItems: "center", gap: 8 }}>
            <strong>Teller</strong>
            <span style={{ fontSize: 12, color: t?.configured ? "var(--safe)" : "var(--muted)", fontWeight: 700 }}>{t?.configured ? `ready · ${t.environment}` : "not set up"}</span>
          </div>
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Free developer account at teller.io; the development environment reaches real banks for a hundred enrollments. Paste the application id and the application certificate it issues.</div>
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
            <button className="btn dark" type="button" disabled={busy !== null} onClick={saveTeller}>Save</button>
            <button className="btn" type="button" disabled={!t?.configured || busy !== null} onClick={connectTeller}>{busy === "teller-connect" ? "Opening…" : "Connect a bank through Teller"}</button>
          </div>
        </div>
      </div>
      {note && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>{note}</div>}
      <div style={{ fontSize: 12, color: "var(--muted)" }}>Also free: the bank's own email alerts, read from the mailbox the same quarter-hour, and the Rocket Money export below.</div>
    </section>
  );
}
