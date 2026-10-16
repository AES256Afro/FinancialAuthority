// Settings › Sign-in and security: what protects the way in, drawn first; then the code from the phone (the second
// step), passkeys, and the devices signed in. Codes and keys are shown once and never kept in this page's memory
// longer than the step that shows them. A second person sees the same page for their own sign-in ("Your sign-in");
// the owner also sees, and can sign out, the devices of the people they added.
import { Copy, Download, FingerprintPattern, Info, KeyRound, Monitor, Smartphone, Tablet } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { when } from "../format";
import type { Status } from "../guide";
import { roleOf, type MePerson } from "../types-people";
import { ConfirmButton } from "../components/ConfirmButton";
import { makePasskey, passkeysSupported, passkeyWords } from "../components/passkeys";
import { Failed, Loading, Said, Show } from "../components/State";
import { StatusPill } from "../components/Status";

interface PasskeyRow { id: number; name: string; address: string; created_at: string; last_used_at: string | null }

interface Factors {
  totp: { enabled: boolean; recovery_left: number; since: string | null };
  passkeys: PasskeyRow[];
  passkeys_here: boolean;
  passkeys_note: string | null;
}

type Note = { ok: boolean; text: string } | null;

const RECOVERY_MADE = 10; // the server makes ten recovery codes each time the second step is turned on
const field = { display: "flex", flexDirection: "column", gap: 4 } as const;
const h2 = { margin: 0, fontSize: 16, fontWeight: 800 } as const;
const intro = { margin: 0, color: "var(--ink-2)", fontSize: 13 } as const;
const linkButton = { display: "inline-flex", alignItems: "center", gap: 6 } as const;

// One line of help, drawn like the Where lines under a field: an info mark, then the words.
function InfoLine({ children }: { children: ReactNode }) {
  return (
    <div style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 12, color: "var(--ink-2)" }}>
      <Info size={14} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1, color: "var(--muted)" }} />
      <span>{children}</span>
    </div>
  );
}

function CopyButton({ text, label }: { text: string; label: string }) {
  const [said, setSaid] = useState<Note>(null);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setSaid({ ok: true, text: "Copied." });
      window.setTimeout(() => setSaid(null), 2000);
    } catch {
      setSaid({ ok: false, text: "This browser would not copy; select the text and copy it by hand." });
    }
  }
  return (
    <span style={{ display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      <button type="button" className="btn" onClick={copy} style={{ ...linkButton, minHeight: 32, padding: "4px 10px" }}><Copy size={13} aria-hidden="true" />{label}</button>
      {said && <Said text={said.text} tone={said.ok ? "info" : "error"} />}
    </span>
  );
}

// ---- the picture: what protects sign-in now -------------------------------------------------------------------

function ShieldMark({ on, Icon }: { on: boolean; Icon: typeof KeyRound }) {
  return (
    <span aria-hidden="true" style={{ position: "relative", width: 48, height: 56, flexShrink: 0, display: "inline-block" }}>
      <svg viewBox="0 0 48 56" width={48} height={56} style={{ position: "absolute", inset: 0 }}>
        <path d="M24 3 L44 10 V27 C44 40 35 49 24 54 C13 49 4 40 4 27 V10 Z" fill={on ? "var(--safe-soft)" : "var(--surface-2)"} stroke={on ? "var(--safe)" : "var(--muted)"} strokeWidth={2.5} strokeDasharray={on ? undefined : "5 4"} strokeLinejoin="round" />
      </svg>
      <Icon size={20} strokeWidth={2} style={{ position: "absolute", left: 14, top: 16, color: on ? "var(--safe-ink)" : "var(--muted)" }} />
    </span>
  );
}

function Guard({ Icon, title, on, status, word, detail }: { Icon: typeof KeyRound; title: string; on: boolean; status: Status; word: string; detail: ReactNode }) {
  return (
    <li style={{ flex: "1 1 0", minWidth: 0, display: "flex", gap: 10, alignItems: "flex-start", padding: 12, borderRadius: 12, border: "1px solid var(--hairline)", background: "var(--surface)" }}>
      <ShieldMark on={on} Icon={Icon} />
      <div style={{ display: "flex", flexDirection: "column", alignItems: "flex-start", gap: 5, minWidth: 0 }}>
        <h3 style={{ margin: 0, fontSize: 14, fontWeight: 800 }}>{title}</h3>
        <StatusPill status={status}>{word}</StatusPill>
        <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{detail}</span>
      </div>
    </li>
  );
}

function Joiner({ word }: { word: string }) {
  return <li style={{ alignSelf: "center", fontSize: 12, fontWeight: 800, color: "var(--ink-2)", textAlign: "center", padding: "0 4px" }}>{word}</li>;
}

function Guards({ f, authOn }: { f: Factors; authOn: boolean }) {
  const left = f.totp.recovery_left;
  const keys = f.passkeys.length;
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <h2 style={h2}>What protects sign-in now</h2>
      <ol aria-label="What protects sign-in" className="flex flex-col lg:flex-row" style={{ listStyle: "none", margin: 0, padding: 0, gap: 8, alignItems: "stretch" }}>
        <Guard
          Icon={KeyRound}
          title="Password"
          on={authOn}
          status={authOn ? "good" : "crit"}
          word={authOn ? "On" : "Off"}
          detail={authOn ? "Asked whenever a phone or computer signs in." : <>No password is set (<code>FA_PASSWORD</code>), so the app asks no one to sign in.</>}
        />
        <Joiner word="then" />
        <Guard
          Icon={Smartphone}
          title="Code from your phone"
          on={f.totp.enabled}
          status={!f.totp.enabled || left <= 3 ? "warn" : "good"}
          word={f.totp.enabled ? (left <= 3 ? `On · ${left} recovery ${left === 1 ? "code" : "codes"} left` : "On") : "Off"}
          detail={f.totp.enabled ? "Asked after the password: the six digits your authenticator app shows." : "The password alone signs in."}
        />
        <Joiner word="or instead" />
        <Guard
          Icon={FingerprintPattern}
          title="Passkey"
          on={keys > 0}
          status={keys > 0 ? "good" : "warn"}
          word={keys > 0 ? `${keys} ${keys === 1 ? "passkey" : "passkeys"}` : "None yet"}
          detail="Signs in on its own with your fingerprint, face or PIN, instead of the password and the code."
        />
      </ol>
    </section>
  );
}

// ---- the second step -------------------------------------------------------------------------------------------

// The server's QR code is an inline <svg> without its namespace or viewBox; an image needs both to draw and scale.
// It is shown as an image, never as markup, so nothing in it can run.
function qrSrc(svg: string): string {
  let s = svg.trim();
  if (!/^<svg\b[^>]*\sxmlns=/.test(s)) s = s.replace(/^<svg\b/, '<svg xmlns="http://www.w3.org/2000/svg"');
  const size = /^<svg\b[^>]*\swidth="([\d.]+)"[^>]*\sheight="([\d.]+)"/.exec(s);
  if (size && !/^<svg\b[^>]*\sviewBox=/.test(s)) s = s.replace(/^<svg\b/, `<svg viewBox="0 0 ${size[1]} ${size[2]}"`);
  return `data:image/svg+xml;utf8,${encodeURIComponent(s)}`;
}

function Step({ n, title, later = false, children }: { n: number; title: string; later?: boolean; children?: ReactNode }) {
  return (
    <li style={{ display: "flex", gap: 10, alignItems: "flex-start" }}>
      <span aria-hidden="true" style={{ flexShrink: 0, width: 24, height: 24, borderRadius: 12, display: "grid", placeItems: "center", fontSize: 12, fontWeight: 800, background: later ? "var(--surface-2)" : "var(--ink)", color: later ? "var(--muted)" : "var(--inverse)" }}>{n}</span>
      <div style={{ display: "flex", flexDirection: "column", gap: 8, minWidth: 0, flex: 1 }}>
        <h3 style={{ margin: "2px 0 0", fontSize: 14, fontWeight: 800, color: later ? "var(--muted)" : "var(--ink)" }}><span className="sr-only">Step {n}{later ? ", next" : ""}: </span>{title}</h3>
        {children}
      </div>
    </li>
  );
}

// Recovery codes left, one square each: filled while unused, empty once used.
function CodesLeft({ left }: { left: number }) {
  const total = Math.max(RECOVERY_MADE, left);
  const fill = left <= 3 ? "var(--warning)" : "var(--safe)";
  return (
    <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
      <div aria-hidden="true" style={{ display: "flex", gap: 4 }}>
        {Array.from({ length: total }, (_, i) => (
          <span key={i} style={{ width: 14, height: 14, borderRadius: 4, boxSizing: "border-box", background: i < left ? fill : "transparent", border: i < left ? 0 : "2px solid var(--hairline)" }} />
        ))}
      </div>
      <span style={{ fontSize: 13, fontWeight: 700 }}>{left} of {RECOVERY_MADE} recovery codes left</span>
    </div>
  );
}

function RecoveryCodes({ codes, note, onDone }: { codes: string[]; note: string; onDone: () => void }) {
  const [saved, setSaved] = useState(false);
  const [said, setSaid] = useState<Note>(null);
  const file = `FinancialAuthority recovery codes\nMade ${when(new Date().toISOString())}\nEach code signs in once, in place of the six digits from your phone.\n\n${codes.join("\n")}\n`;
  function download() {
    try {
      const url = URL.createObjectURL(new Blob([file], { type: "text/plain" }));
      const a = document.createElement("a");
      a.href = url;
      a.download = "financialauthority-recovery-codes.txt";
      document.body.appendChild(a);
      a.click();
      a.remove();
      window.setTimeout(() => URL.revokeObjectURL(url), 1000);
      setSaid({ ok: true, text: "Saved as financialauthority-recovery-codes.txt in this device's Downloads folder. Move it somewhere safe, such as a password manager or a USB stick." });
    } catch {
      setSaid({ ok: false, text: "This browser would not save the file. Use Copy all, or write the codes down." });
    }
  }
  return (
    <div className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12, borderColor: "var(--warning)" }}>
      <StatusPill status="good">The second step is on</StatusPill>
      <h3 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>Save your ten recovery codes now</h3>
      <p style={{ ...intro, color: "var(--ink)" }}>{note}</p>
      <ol style={{ margin: 0, padding: 0, listStyle: "none", display: "grid", gridTemplateColumns: "repeat(auto-fill, minmax(min(150px, 100%), 1fr))", gap: 6 }}>
        {codes.map((c, i) => (
          <li key={c} style={{ display: "flex", gap: 8, alignItems: "baseline", padding: "6px 10px", borderRadius: 8, background: "var(--surface-2)" }}>
            <span style={{ fontSize: 11, color: "var(--muted)", width: 18, textAlign: "right" }}>{i + 1}</span>
            <code style={{ fontSize: 15, fontWeight: 700, letterSpacing: ".04em" }}>{c}</code>
          </li>
        ))}
      </ol>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <CopyButton text={codes.join("\n")} label="Copy all" />
        <button type="button" className="btn" onClick={download} style={{ ...linkButton, minHeight: 32, padding: "4px 10px" }}><Download size={13} aria-hidden="true" />Download as a text file</button>
      </div>
      {said && <Said text={said.text} tone={said.ok ? "info" : "error"} />}
      <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, fontWeight: 700, minHeight: 32, cursor: "pointer" }}>
        <input type="checkbox" checked={saved} onChange={(e) => setSaved(e.target.checked)} style={{ width: 18, height: 18 }} />
        I have saved them somewhere safe
      </label>
      <div><button type="button" className="btn dark" disabled={!saved} onClick={onDone}>Done: hide the codes</button></div>
      {!saved && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Tick the box first. Once hidden, these codes are never shown again.</span>}
    </div>
  );
}

type Stage = { at: "idle" } | { at: "scan"; secret: string; uri: string; qr: string } | { at: "codes"; codes: string[]; note: string };

function SecondStep({ f, authOn, onChanged }: { f: Factors; authOn: boolean; onChanged: () => void }) {
  const [stage, setStage] = useState<Stage>({ at: "idle" });
  const [code, setCode] = useState("");
  const [offCode, setOffCode] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);

  async function start() {
    setBusy(true);
    setNote(null);
    try {
      const r = await api<{ secret: string; uri: string; qr_svg: string }>("/api/auth/totp/start", { method: "POST" });
      setStage({ at: "scan", secret: r.secret, uri: r.uri, qr: r.qr_svg });
      setCode("");
    } catch (e) {
      setNote({ ok: false, text: (e as Error).message });
    } finally {
      setBusy(false);
    }
  }

  async function confirm(e: React.FormEvent) {
    e.preventDefault();
    const digits = code.replace(/\s/g, "");
    if (!/^\d{6}$/.test(digits)) {
      setNote({ ok: false, text: "Type the six digits the app shows now for FinancialAuthority." });
      return;
    }
    setBusy(true);
    setNote(null);
    try {
      const r = await api<{ ok: boolean; recovery_codes: string[]; note: string }>("/api/auth/totp/confirm", { method: "POST", body: JSON.stringify({ code: digits }) });
      setStage({ at: "codes", codes: r.recovery_codes, note: r.note });
      setCode("");
      onChanged();
    } catch (err) {
      setNote({ ok: false, text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  // Asked first by ConfirmButton, which says a refusal beside the button.
  async function turnOff() {
    const c = offCode.trim();
    if (!c) throw new Error("Type the six digits your app shows now, or a recovery code, first.");
    await api("/api/auth/totp/disable", { method: "POST", body: JSON.stringify({ code: c }) });
    setOffCode("");
    setNote({ ok: true, text: "The second step is off. The password alone signs in now." });
    onChanged();
  }

  let body: ReactNode;
  if (stage.at === "codes") {
    body = <RecoveryCodes codes={stage.codes} note={stage.note} onDone={() => { setStage({ at: "idle" }); setNote({ ok: true, text: "The second step is on. Next time you sign in, the app asks for the code after the password." }); }} />;
  } else if (f.totp.enabled) {
    const left = f.totp.recovery_left;
    body = (
      <div className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
        <div><StatusPill status="good">{f.totp.since ? `On since ${when(f.totp.since)}` : "On"}</StatusPill></div>
        <CodesLeft left={left} />
        {left <= 3 && (
          <div>
            <StatusPill status="warn">Only {left} recovery {left === 1 ? "code" : "codes"} left. Turn the second step off and on again to get ten new ones.</StatusPill>
          </div>
        )}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ ...field, flex: "1 1 220px" }}>
            <span className="lab">Code to turn it off</span>
            <input className="input" value={offCode} onChange={(e) => setOffCode(e.target.value)} autoComplete="one-time-code" autoCapitalize="off" spellCheck={false} aria-describedby="off-hint" />
          </label>
          <ConfirmButton label="Turn off" question="Turn off the second step? The password alone will sign in again, and the recovery codes stop working." yes="Yes, turn it off" no="Keep it on" onConfirm={turnOff} />
        </div>
        <span id="off-hint" style={{ fontSize: 12, color: "var(--ink-2)" }}>The six digits your authenticator app shows now, or one of your recovery codes.</span>
      </div>
    );
  } else if (stage.at === "scan") {
    const grouped = stage.secret.match(/.{1,4}/g)?.join(" ") ?? stage.secret;
    body = (
      <form onSubmit={confirm} className="card" style={{ padding: 16 }}>
        <ol style={{ margin: 0, padding: 0, listStyle: "none", display: "flex", flexDirection: "column", gap: 16 }}>
          <Step n={1} title="On your phone: add FinancialAuthority to your authenticator app">
            <p style={{ ...intro, color: "var(--ink)" }}>Open the authenticator app on your phone. Choose Add (often a + button), then Scan a QR code, and point the phone's camera at this square.</p>
            <img src={qrSrc(stage.qr)} alt="QR code for your authenticator app" style={{ width: "min(200px, 100%)", height: "auto", borderRadius: 8, border: "1px solid var(--hairline)" }} />
            <p style={intro}>Setting this up on this same phone? <a href={stage.uri} style={{ fontWeight: 700 }}>Open it in the authenticator app</a>. No camera? Choose Enter a setup key in the app and type this key (time based):</p>
            <code style={{ fontSize: 17, fontWeight: 700, letterSpacing: ".08em", wordBreak: "break-word" }}>{grouped}</code>
            <div><CopyButton text={stage.secret} label="Copy the key" /></div>
          </Step>
          <Step n={2} title="On this screen: type the six digits the app now shows">
            <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
              <label style={{ ...field, flex: "0 1 220px" }}>
                <span className="lab">Code from your phone</span>
                <input className="input" value={code} onChange={(e) => setCode(e.target.value)} inputMode="numeric" autoComplete="one-time-code" maxLength={7} style={{ fontSize: 18, letterSpacing: ".12em" }} />
              </label>
              <button type="submit" className="btn dark" disabled={busy}>{busy ? "Checking…" : "Turn on the second step"}</button>
              <button type="button" className="btn" disabled={busy} onClick={() => { setStage({ at: "idle" }); setNote(null); }}>Stop</button>
            </div>
            <span style={{ fontSize: 12, color: "var(--ink-2)" }}>The digits change every 30 seconds; type the ones showing now. If they are refused, check that the time on your phone is right.</span>
          </Step>
          <Step n={3} title="Save your ten recovery codes (shown next)" later />
        </ol>
      </form>
    );
  } else {
    body = (
      <div className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
        <div><StatusPill status="warn">Off: the password alone signs in</StatusPill></div>
        <p style={{ ...intro, color: "var(--ink)" }}>
          You need your phone with an authenticator app on it: for example Google Authenticator, Microsoft Authenticator or Aegis (all free), or 1Password. Turning this on takes about two minutes: scan a square, type six digits, save ten recovery codes.
        </p>
        <div><button type="button" className="btn dark" disabled={busy} onClick={start}>{busy ? "Starting…" : "Turn on"}</button></div>
      </div>
    );
  }

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <h2 style={h2}>A code from your phone at sign-in</h2>
      <p style={intro}>With this on, the password alone no longer signs in: after it, the app asks for the six digits an authenticator app on your phone shows. The digits change every 30 seconds. Ten recovery codes are made for a day the phone is not at hand.</p>
      {!authOn && <InfoLine>Sign-in is off on this install (no <code>FA_PASSWORD</code> is set), so nothing asks for the code yet. Set the password first: <Link to="/settings/setup">Settings › Setting up</Link>.</InfoLine>}
      {body}
      {note && <Said text={note.text} tone={note.ok ? "info" : "error"} />}
    </section>
  );
}

// ---- passkeys --------------------------------------------------------------------------------------------------

function Passkeys({ f, onChanged }: { f: Factors; onChanged: () => void }) {
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const can = passkeysSupported();
  const here = typeof window !== "undefined" ? window.location.hostname : "";

  async function add(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setNote(null);
    try {
      const made = await makePasskey(name.trim() || "Passkey");
      setNote({ ok: true, text: `"${made.name}" is added. Next time, choose Sign in with a passkey on the sign-in page.` });
      setName("");
      onChanged();
    } catch (err) {
      setNote({ ok: false, text: passkeyWords(err, "make") });
    } finally {
      setBusy(false);
    }
  }

  // Asked first by ConfirmButton, which says a refusal beside the button.
  async function remove(p: PasskeyRow) {
    await api(`/api/auth/passkeys/${p.id}`, { method: "DELETE" });
    setNote({ ok: true, text: `"${p.name}" is removed and no longer signs in here. Your phone or computer may still list it until you delete it there.` });
    onChanged();
  }

  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <h2 style={h2}>Passkeys</h2>
      <p style={intro}>A passkey signs in with your phone's or computer's fingerprint, face or PIN, instead of the password and the code. Make one on each device you use. Each passkey works only at the address it was made at.</p>
      {!f.passkeys_here && f.passkeys_note && <InfoLine>{f.passkeys_note}</InfoLine>}
      <div className="card" style={{ padding: "6px 14px", overflowX: "auto" }}>
        <table className="ledger">
          <thead><tr><th>Name</th><th>Works at</th><th>Made</th><th>Last used</th><th><span className="sr-only">Remove</span></th></tr></thead>
          <tbody>
            {f.passkeys.map((p) => (
              <tr key={p.id}>
                <td><strong>{p.name}</strong></td>
                <td style={{ wordBreak: "break-word" }}>{p.address}{p.address === here && <span style={{ color: "var(--ink-2)" }}> (this address)</span>}</td>
                <td style={{ whiteSpace: "nowrap" }}>{when(p.created_at)}</td>
                <td style={{ whiteSpace: "nowrap" }}>{p.last_used_at ? when(p.last_used_at) : "not yet"}</td>
                <td style={{ textAlign: "right" }}>
                  <ConfirmButton label="Remove" question={`Remove "${p.name}"? It will no longer sign in here.`} yes="Yes, remove it" no="Keep it" onConfirm={() => remove(p)} />
                </td>
              </tr>
            ))}
            {f.passkeys.length === 0 && <tr><td colSpan={5} style={{ color: "var(--ink-2)" }}>No passkeys yet.</td></tr>}
          </tbody>
        </table>
      </div>
      {f.passkeys_here && can && (
        <form onSubmit={add} className="card" style={{ padding: 14, display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-end" }}>
          <label style={{ ...field, flex: "1 1 200px" }}>
            <span className="lab">Name it</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} placeholder="My phone" maxLength={60} />
          </label>
          <button type="submit" className="btn dark" disabled={busy} style={linkButton}><FingerprintPattern size={15} aria-hidden="true" />{busy ? "Follow your device's prompt…" : "Add a passkey"}</button>
          <span style={{ flexBasis: "100%", fontSize: 12, color: "var(--ink-2)" }}>On this device: your phone or computer asks for your fingerprint, face or PIN. The passkey stays on the device; the app keeps only its public half.</span>
        </form>
      )}
      {f.passkeys_here && !can && <InfoLine>This browser cannot make passkeys. Use a recent Chrome, Edge, Safari or Firefox.</InfoLine>}
      {note && <Said text={note.text} tone={note.ok ? "info" : "error"} />}
    </section>
  );
}

// ---- the devices signed in -------------------------------------------------------------------------------------

interface SessionRow { id: number; created_at: string; last_seen_at: string | null; user_agent: string; ip: string; this: boolean; who?: string; person_id?: number | null }

function device(ua: string): string {
  const os = /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) ? "iPad" : /Android/.test(ua) ? "Android" : /Mac OS X/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "A device";
  const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "";
  return browser ? `${browser} on ${os}` : os;
}

// A phone, a tablet or a computer, drawn, so the list is read at a glance.
function DeviceIcon({ ua }: { ua: string }) {
  const Icon = /iPhone|Android.*Mobile/.test(ua) ? Smartphone : /iPad|Android/.test(ua) ? Tablet : Monitor;
  return <Icon size={18} aria-hidden="true" style={{ flexShrink: 0, color: "var(--ink-2)", marginTop: 1 }} />;
}

function SignedIn({ owner }: { owner: boolean }) {
  const state = useApi<{ sessions: SessionRow[] }>("/api/auth/sessions");
  const { data, reload, error } = state;
  const [note, setNote] = useState<string | null>(null);
  // Asked first by ConfirmButton; a failure is said beside the button that asked.
  async function end(body: { session_id?: number; all_others?: boolean }) {
    setNote(null);
    const r = await api<{ ended: number }>("/api/auth/sessions/end", { method: "POST", body: JSON.stringify(body) });
    setNote(`Signed out ${r.ended} ${r.ended === 1 ? "browser" : "browsers"}.`);
    reload();
  }
  // Not signed in with a password (sign-in is off, or the demo): there is nothing to list.
  if (error && !data && (error.status === 401 || error.status === 404)) return null;
  const rows = data?.sessions ?? [];
  const whoShown = rows.some((r) => r.who && r.who !== "you");
  const whoWord = (r: SessionRow) => (!r.who || r.who === "you" ? "You" : r.who);
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <h2 style={h2}>Devices signed in</h2>
      <p style={intro}>
        {owner
          ? "Every phone and computer browser signed in: yours, and those of the people you added on Settings › People. Signing one out ends it for good: it needs the password (or a passkey) again. A new password signs out all of yours."
          : "Every phone and computer browser signed in as you. Signing one out ends it for good: it needs your password (or a passkey) again."}
      </p>
      {error && !data && <Failed error={error} what="the devices signed in" onRetry={reload} />}
      {!data && !error && <Loading what="the devices signed in" />}
      {data && (
        <div className="card" style={{ padding: "6px 14px", overflowX: "auto" }}>
          <table className="ledger">
            <thead><tr><th>Device</th>{whoShown && <th>Who</th>}<th>Last used</th><th>Signed in</th><th><span className="sr-only">Sign out</span></th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td>
                    <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                      <DeviceIcon ua={r.user_agent} />
                      <div>{device(r.user_agent)}{r.this && <strong style={{ marginLeft: 6, fontSize: 12 }}>· this one</strong>}<div style={{ fontSize: 11, color: "var(--muted)" }}>{r.ip}</div></div>
                    </div>
                  </td>
                  {whoShown && <td style={{ fontWeight: r.who === "you" ? 400 : 700 }}>{whoWord(r)}</td>}
                  <td style={{ whiteSpace: "nowrap" }}>{r.last_seen_at ? when(r.last_seen_at) : "not yet"}</td>
                  <td style={{ whiteSpace: "nowrap" }}>{when(r.created_at)}</td>
                  <td style={{ textAlign: "right" }}>
                    {!r.this && <ConfirmButton label="Sign out" question={r.who && r.who !== "you" ? `Sign out ${r.who} on this browser for good?` : "Sign this browser out for good?"} yes="Sign it out" no="Keep" onConfirm={() => end({ session_id: r.id })} />}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={whoShown ? 5 : 4} style={{ color: "var(--ink-2)" }}>Sign-in is off on this install (no password is set).</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      {rows.filter((r) => !r.this).length > 0 && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <ConfirmButton
            label="Sign out everywhere else"
            question={owner && whoShown ? "Every other browser will need to sign in again: yours, and those of the people you added." : "Every other browser will need the password again."}
            yes="Sign them all out"
            no="Keep them"
            onConfirm={() => end({ all_others: true })}
          />
        </div>
      )}
      {note && <Said text={note} />}
    </section>
  );
}

export function Security() {
  const factors = useApi<Factors>("/api/auth/factors");
  const me = useApi<MePerson>("/api/auth/me");
  const authOn = me.data ? me.data.auth_enabled : true;
  const role = roleOf(me.data);
  const person = Boolean(me.data) && role !== "owner";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 18, maxWidth: 900 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>{person ? "Your sign-in" : "Sign-in and security"}</h1>
      {person && me.data && (
        <InfoLine>
          You sign in as <strong>{me.data.name}</strong>. Everything on this page is yours alone. Forgot your password or lost your phone? Ask {me.data.owner || "the owner"} for a new link: it clears your password, the code and your passkeys, and you choose a new password.
        </InfoLine>
      )}
      <Show state={factors} what="your sign-in settings">
        {(f) => (
          <>
            <Guards f={f} authOn={authOn} />
            <SecondStep f={f} authOn={authOn} onChanged={factors.reload} />
            <Passkeys f={f} onChanged={factors.reload} />
          </>
        )}
      </Show>
      <SignedIn owner={!person} />
    </div>
  );
}
