// Settings › Privacy: a map of where your data goes, drawn first; then each model with what it sees and a switch,
// who can read the plan, Hide amounts, everything as one file, and, set apart, deleting everything.
import { Download, Eye, EyeOff, Server, Trash2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, useApi } from "../api";
import { useAppearance } from "../appearance";
import { DEMO } from "../demo/shim";
import type { Me } from "../types";
import { ownerName, roleOf, type MePerson } from "../types-people";
import { Failed, Said, Show } from "../components/State";
import { StatusPill } from "../components/Status";

interface ModelRow { role: string; what: string; on: boolean; provider: string; model: string | null; local: boolean; sees: string; where: string }
interface Leaf { service: string; on: boolean; gets: string; why: string }
interface PrivacyPage {
  models: ModelRow[];
  leaves: Leaf[];
  readers: { apps: number; keys: number };
  quiet: boolean;
  plaid_environment: string | null;
  export: { format: string; note: string };
  offbox: string | null;
}
interface Factors { totp: { enabled: boolean } }
interface Erased { ok: boolean; rows: number; restore_points: number; left: string[] }

const PHRASE = "delete everything";
const EXPORT_URL = "/api/v1/exports/file?format=json";
const h2 = { margin: 0, fontSize: 16, fontWeight: 800 } as const;
const intro = { margin: 0, color: "var(--ink-2)", fontSize: 13 } as const;
const field = { display: "flex", flexDirection: "column", gap: 4 } as const;
const ROLE_WORD: Record<string, string> = { advisor: "the advisor", reviewer: "the reviewer" };

// ---- the map: your server in the middle, an arrow out to every service that gets anything --------------------

function Branch({ on }: { on: boolean }) {
  return (
    <svg aria-hidden="true" width={26} height={12} viewBox="0 0 26 12" style={{ flexShrink: 0 }}>
      <line x1={0} y1={6} x2={on ? 19 : 24} y2={6} stroke={on ? "var(--paycheck)" : "var(--muted)"} strokeWidth={2} strokeDasharray={on ? undefined : "3 3"} />
      {on && <path d="M18 1 L25 6 L18 11 Z" fill="var(--paycheck)" />}
    </svg>
  );
}

function LeafCard({ leaf, plaidEnv }: { leaf: Leaf; plaidEnv: string | null }) {
  const on = leaf.on;
  return (
    <li style={{ display: "flex", alignItems: "center" }}>
      <Branch on={on} />
      <div style={{ flex: "1 1 auto", minWidth: 0, padding: "8px 10px", borderRadius: 10, border: on ? "1px solid var(--paycheck)" : "1px dashed var(--hairline)", background: on ? "var(--paycheck-soft)" : "var(--surface-2)", display: "flex", flexDirection: "column", gap: 3 }}>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <strong style={{ fontSize: 13, color: on ? "var(--ink)" : "var(--muted)" }}>{leaf.service}</strong>
          <span style={{ display: "inline-flex", alignItems: "center", gap: 4, fontSize: 11, fontWeight: 800, padding: "1px 7px", borderRadius: 999, background: on ? "var(--surface)" : "transparent", color: on ? "var(--paycheck-ink)" : "var(--muted)", border: on ? 0 : "1px solid var(--hairline)" }}>
            {on ? <Eye size={12} aria-hidden="true" /> : <EyeOff size={12} aria-hidden="true" />}{on ? "On: receives data" : "Off: gets nothing"}
          </span>
        </div>
        <span style={{ fontSize: 12, color: on ? "var(--ink-2)" : "var(--muted)" }}>
          {on ? leaf.gets : <>When on: {leaf.gets}</>}
          {on && leaf.service === "Plaid" && plaidEnv && <> Plaid's {plaidEnv} environment.</>}
        </span>
      </div>
    </li>
  );
}

function DataMap({ page }: { page: PrivacyPage }) {
  const out = page.leaves.filter((l) => l.on).length;
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <h2 style={h2}>Where your data goes</h2>
      <div className="card" style={{ padding: 14 }}>
        <div className="flex flex-col sm:flex-row" style={{ alignItems: "stretch" }}>
          <div className="sm:w-[200px] sm:self-center" style={{ flexShrink: 0, padding: 12, borderRadius: 12, border: "2px solid var(--ink)", background: "var(--surface)", display: "flex", flexDirection: "column", gap: 4 }}>
            <Server size={24} aria-hidden="true" />
            <strong style={{ fontSize: 15 }}>Your data</strong>
            <span style={{ fontSize: 12, color: "var(--ink-2)" }}>on your own server. Everything is kept here; only what the arrows show leaves it.</span>
          </div>
          {/* the stem: down from the box on a phone, across from it on a wider screen */}
          <div aria-hidden="true" className="ml-5 h-3 w-0.5 sm:ml-0 sm:h-0.5 sm:w-4 sm:self-center" style={{ background: "var(--muted)", flexShrink: 0 }} />
          <ul aria-label={`${out} of ${page.leaves.length} services receive data`} className="ml-5 sm:ml-0" style={{ listStyle: "none", padding: "6px 0", borderLeft: "2px solid var(--muted)", display: "flex", flexDirection: "column", gap: 8, flex: "1 1 auto", minWidth: 0 }}>
            {page.leaves.map((l) => <LeafCard key={l.service} leaf={l} plaidEnv={page.plaid_environment} />)}
          </ul>
        </div>
      </div>
      {page.offbox && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Sealed backup copies also go to a folder you chose, <code style={{ wordBreak: "break-all" }}>{page.offbox}</code> (<Link to="/settings/backups">Backups</Link>).</span>}
    </section>
  );
}

// ---- models, each with a switch -------------------------------------------------------------------------------

function Switch({ on, busy, label, onToggle }: { on: boolean; busy: boolean; label: string; onToggle: () => void }) {
  return (
    <button type="button" role="switch" aria-checked={on} aria-label={label} disabled={busy} onClick={onToggle} style={{ display: "inline-flex", alignItems: "center", gap: 8, minHeight: 32, background: "none", border: 0, padding: 0, font: "inherit", color: "var(--ink)", cursor: busy ? "default" : "pointer", opacity: busy ? 0.6 : 1 }}>
      <span aria-hidden="true" style={{ position: "relative", width: 44, height: 26, borderRadius: 13, boxSizing: "border-box", background: on ? "var(--safe)" : "var(--surface-2)", border: on ? "1px solid var(--safe)" : "1px solid var(--muted)", flexShrink: 0 }}>
        <span style={{ position: "absolute", top: 2, left: on ? 20 : 2, width: 20, height: 20, borderRadius: 10, background: on ? "var(--surface)" : "var(--muted)", transition: "left .15s" }} />
      </span>
      <span style={{ fontSize: 13, fontWeight: 800 }}>{busy ? "Saving…" : on ? "On" : "Off"}</span>
    </button>
  );
}

function Models({ page, onPage }: { page: PrivacyPage; onPage: (p: PrivacyPage) => void }) {
  const [busy, setBusy] = useState<string | null>(null);
  const [note, setNote] = useState<{ role: string; ok: boolean; text: string; models?: boolean } | null>(null);
  async function flip(m: ModelRow) {
    setBusy(m.role);
    setNote(null);
    try {
      const next = await api<PrivacyPage>(`/api/v1/privacy/models/${m.role}`, { method: "POST", body: JSON.stringify({ on: !m.on }) });
      onPage(next);
      const now = next.models.find((x) => x.role === m.role);
      setNote({ role: m.role, ok: true, text: m.on ? "Off. It receives nothing now." : `On, running ${now?.where ?? "on your own computer"}. Choose another model on Settings › Models.`, models: !m.on });
    } catch (e) {
      setNote({ role: m.role, ok: false, text: (e as Error).message, models: e instanceof ApiError && e.status === 409 });
    } finally {
      setBusy(null);
    }
  }
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <h2 style={h2}>Models</h2>
      <p style={intro}>The app does the maths; a model only explains it. Each switch turns one use of a model off or on.</p>
      <div className="card" style={{ padding: "4px 14px" }}>
        {page.models.map((m, i) => (
          <div key={m.role} style={{ display: "flex", gap: 12, alignItems: "flex-start", flexWrap: "wrap", padding: "12px 0", borderTop: i ? "1px solid var(--surface-2)" : 0 }}>
            <div style={{ flex: "1 1 260px", minWidth: 0, display: "flex", flexDirection: "column", gap: 4 }}>
              <h3 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>{m.what} <span style={{ fontWeight: 600, color: "var(--ink-2)" }}>({ROLE_WORD[m.role] ?? m.role})</span></h3>
              <span style={{ fontSize: 13 }}><span className="lab" style={{ marginRight: 6 }}>Sees</span>{m.sees}</span>
              <span style={{ fontSize: 13 }}><span className="lab" style={{ marginRight: 6 }}>Runs</span>{m.on ? `${m.where}${m.model ? `, ${m.model}` : ""}` : "nowhere: it is off"}</span>
            </div>
            <div style={{ flex: "0 1 auto", display: "flex", flexDirection: "column", gap: 4, alignItems: "flex-start", maxWidth: "100%" }}>
              <Switch on={m.on} busy={busy === m.role} label={m.what} onToggle={() => flip(m)} />
              {note?.role === m.role && <Said text={note.text} tone={note.ok ? "info" : "error"} />}
              {note?.role === m.role && note.models && <Link to="/settings/models" style={{ fontSize: 12, fontWeight: 700 }}>Open Settings › Models</Link>}
            </div>
          </div>
        ))}
      </div>
    </section>
  );
}

// ---- who reads, hidden amounts, one file ----------------------------------------------------------------------

function Tile({ value, word }: { value: number; word: string }) {
  return (
    <div style={{ flex: "1 1 120px", padding: "10px 12px", borderRadius: 10, background: "var(--surface-2)", display: "flex", flexDirection: "column" }}>
      <span style={{ fontSize: 26, fontWeight: 800, lineHeight: 1.1 }}>{value}</span>
      <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{word}</span>
    </div>
  );
}

function Readers({ page }: { page: PrivacyPage }) {
  const { apps, keys } = page.readers;
  return (
    <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
      <h2 style={h2}>Who else can read your plan</h2>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <Tile value={apps} word={apps === 1 ? "app signed in (such as Claude)" : "apps signed in (such as Claude)"} />
        <Tile value={keys} word={keys === 1 ? "key for a script" : "keys for scripts"} />
      </div>
      <span style={intro}>
        {apps === 0 && keys === 0 ? "Nothing outside this app can read your plan. " : "Each one can read your plan; one you gave the confirm choice can also confirm details on Needs you. None can approve a send or move money. "}
        <Link to="/settings/clients" style={{ fontWeight: 700 }}>See or revoke them on Apps and keys</Link>
      </span>
    </section>
  );
}

function Quiet({ page }: { page: PrivacyPage }) {
  const look = useAppearance();
  // The header's switch: once the app's appearance has loaded or changed here, it is the live state.
  const quiet = look.saved ? look.quiet : page.quiet;
  return (
    <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
      <h2 style={{ ...h2, display: "inline-flex", alignItems: "center", gap: 6 }}>{quiet ? <EyeOff size={18} aria-hidden="true" /> : <Eye size={18} aria-hidden="true" />}Hide amounts</h2>
      <div>
        <span style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "3px 9px", borderRadius: 999, background: "var(--surface-2)", color: "var(--ink)", fontSize: 12, fontWeight: 800 }}>
          {quiet ? <EyeOff size={14} aria-hidden="true" /> : <Eye size={14} aria-hidden="true" />}{quiet ? "On now: amounts are blurred" : "Off now: amounts show"}
        </span>
      </div>
      <span style={intro}>The Hide amounts button is in the header, at the top of every screen. When it is on, the amounts on screen (figures, sentences that hold one, and chart labels) are blurred until you tap one, which helps on a screen other people can see. Look over a screen once before you rely on it: a figure the app has not marked yet can still show.</span>
    </section>
  );
}

function OneFile({ page }: { page: PrivacyPage }) {
  return (
    <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8 }}>
      <h2 style={h2}>Everything as one file</h2>
      <span style={intro}>{page.export.note} It downloads to this phone or computer and holds your financial details, so keep it somewhere private.</span>
      <div>
        <a href={DEMO ? undefined : EXPORT_URL} download aria-disabled={DEMO} className="btn" style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 6, opacity: DEMO ? 0.5 : 1 }}>
          <Download size={14} aria-hidden="true" />Download everything (.json)
        </a>
      </div>
      <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Other formats and printable reports are on <Link to="/exports">Exports</Link>.</span>
    </section>
  );
}

// ---- deleting everything, set apart --------------------------------------------------------------------------

// Only the owner can erase everything (the server refuses anyone else); a second person is told so instead of
// being shown a form that fails.
function OwnerOnly({ owner }: { owner: string }) {
  return (
    <section className="card" aria-labelledby="erase-title" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 8, marginTop: 16 }}>
      <h2 id="erase-title" style={{ ...h2, display: "inline-flex", alignItems: "center", gap: 6 }}><Trash2 size={18} aria-hidden="true" />Delete everything</h2>
      <span style={intro}>Only {owner} can delete everything on this server. Your own sign-in, passkeys and devices are on <Link to="/settings/security">Your sign-in</Link>.</span>
    </section>
  );
}

function DeleteEverything() {
  const factors = useApi<Factors>("/api/auth/factors");
  const me = useApi<Me>("/api/auth/me");
  const [phrase, setPhrase] = useState("");
  const [password, setPassword] = useState("");
  const [code, setCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [done, setDone] = useState<Erased | null>(null);
  const matches = phrase.trim().toLowerCase() === PHRASE;
  const askPassword = me.data ? me.data.auth_enabled : true;
  // When the second step cannot be read, the code field is shown and may be left empty.
  const askCode = factors.data ? factors.data.totp.enabled : Boolean(factors.error);

  async function erase(e: React.FormEvent) {
    e.preventDefault();
    if (!matches) return;
    setBusy(true);
    setError(null);
    try {
      const r = await api<Erased>("/api/v1/privacy/erase", { method: "POST", body: JSON.stringify({ phrase: phrase.trim(), password, code: code.trim() }) });
      setDone(r);
      setPassword("");
      setCode("");
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  const frame = { padding: 16, display: "flex", flexDirection: "column", gap: 12, border: "2px solid var(--critical)", borderRadius: "var(--radius)", background: "var(--surface)", marginTop: 16 } as const;

  if (done) {
    return (
      <section style={frame} aria-labelledby="erase-title">
        <h2 id="erase-title" style={{ ...h2, display: "inline-flex", alignItems: "center", gap: 6 }}><Trash2 size={18} aria-hidden="true" />Everything is deleted</h2>
        <div><StatusPill status="good">Done</StatusPill></div>
        <span style={{ fontSize: 13 }}>{done.rows.toLocaleString("en-US")} rows and {done.restore_points.toLocaleString("en-US")} restore points were removed from this server.</span>
        <div>
          <span className="lab">What is left</span>
          <ul style={{ margin: "6px 0 0", paddingLeft: 18, display: "flex", flexDirection: "column", gap: 4, listStyle: "disc", fontSize: 13 }}>
            {done.left.map((l) => <li key={l}>{l}</li>)}
          </ul>
        </div>
        <div><button type="button" className="btn dark" onClick={() => window.location.assign(import.meta.env.BASE_URL)}>Reload the app</button></div>
      </section>
    );
  }

  return (
    <section style={frame} aria-labelledby="erase-title">
      <h2 id="erase-title" style={{ ...h2, display: "inline-flex", alignItems: "center", gap: 6, color: "var(--critical-ink)" }}><Trash2 size={18} aria-hidden="true" />Delete everything</h2>
      <div><StatusPill status="crit">Cannot be undone</StatusPill></div>
      <p style={{ margin: 0, fontSize: 13 }}>
        This removes every account, transaction, bill, message record, fact, plan, setting and restore point from this server, and disconnects each bank linked through Plaid. It keeps only what lets you sign in: this browser's session, your passkeys and the second step. Nothing comes back from it.
      </p>
      <p style={intro}>Want a copy first? Use Download everything above before you start.</p>
      <form onSubmit={erase} style={{ display: "flex", flexDirection: "column", gap: 10, maxWidth: 420 }}>
        <label style={field}>
          <span className="lab">Type the words "{PHRASE}"</span>
          <input className="input" value={phrase} onChange={(e) => setPhrase(e.target.value)} autoComplete="off" autoCapitalize="off" spellCheck={false} aria-describedby="erase-phrase" />
          <span id="erase-phrase" style={{ fontSize: 12, color: matches ? "var(--safe-ink)" : "var(--ink-2)", fontWeight: matches ? 700 : 400 }}>{matches ? "The words match." : "Two words, lower case, with one space."}</span>
        </label>
        {askPassword && (
          <label style={field}>
            <span className="lab">Your password</span>
            <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
          </label>
        )}
        {askCode && (
          <label style={field}>
            <span className="lab">Code from your phone</span>
            <input className="input" value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" aria-describedby="erase-code" />
            <span id="erase-code" style={{ fontSize: 12, color: "var(--ink-2)" }}>
              {factors.data ? "The six digits your authenticator app shows now, or one of your recovery codes." : "Only if the second step is on: the six digits your authenticator app shows now."}
            </span>
          </label>
        )}
        {factors.error && !factors.data && <Failed error={factors.error} what="whether the second step is on" onRetry={factors.reload} card={false} />}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <button type="submit" className="btn" disabled={!matches || busy || DEMO} style={{ display: "inline-flex", alignItems: "center", gap: 6, background: "var(--critical-ink)", color: "var(--surface)", borderColor: "var(--critical-ink)" }}>
            <Trash2 size={14} aria-hidden="true" />{busy ? "Deleting…" : "Delete everything"}
          </button>
          {!matches && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>The button works once the words match.</span>}
        </div>
        {error && <Said text={error} tone="error" />}
      </form>
    </section>
  );
}

export function Privacy({ me = null }: { me?: MePerson | null }) {
  const state = useApi<PrivacyPage>("/api/v1/privacy");
  const [latest, setLatest] = useState<PrivacyPage | null>(null);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18, maxWidth: 900 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Privacy</h1>
      <Show state={state} what="your privacy settings">
        {(fetched): ReactNode => {
          const page = latest ?? fetched;
          return (
            <>
              <DataMap page={page} />
              <Models page={page} onPage={setLatest} />
              <Readers page={page} />
              <Quiet page={page} />
              <OneFile page={page} />
            </>
          );
        }}
      </Show>
      {roleOf(me) === "owner" ? <DeleteEverything /> : <OwnerOnly owner={ownerName(me?.owner)} />}
    </div>
  );
}
