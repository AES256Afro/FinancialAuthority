// Sign in: the password, then the code from the phone when the second step is on; or a passkey instead of both.
// The owner and anyone the owner added sign in here, each with their own username.
import { ArrowLeft, Check, FingerprintPattern } from "lucide-react";
import { useState } from "react";
import { ApiError, api } from "../api";
import { Said } from "../components/State";
import { passkeysSupported, passkeyWords, signInWithPasskey } from "../components/passkeys";

type Busy = "password" | "code" | "passkey" | null;

interface LoginAnswer { ok: boolean; user?: string; second_step?: string; ticket?: string; kind?: string; role?: string }

// Passkeys are offered only at an https address or on this computer; anywhere else the password is the way in.
function passkeyHomeWords(): string {
  const secure = window.location.protocol === "https:" || ["localhost", "127.0.0.1"].includes(window.location.hostname);
  return secure
    ? "No passkey is set up at this address yet. Sign in with your password, then add one on Settings › Sign-in and security."
    : "Use your password here; passkeys work at your https address.";
}

const field = { display: "flex", flexDirection: "column", gap: 4 } as const;

// Where the person is: the password is done, the code is next.
function Steps() {
  const dot = (done: boolean) => ({ display: "inline-grid", placeItems: "center", width: 22, height: 22, borderRadius: 11, fontSize: 12, fontWeight: 800, background: done ? "var(--safe-soft)" : "var(--ink)", color: done ? "var(--safe-ink)" : "var(--inverse)", flexShrink: 0 }) as const;
  return (
    <ol aria-label="Sign-in steps" style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", alignItems: "center", gap: 8, flexWrap: "wrap", fontSize: 13 }}>
      <li style={{ display: "inline-flex", alignItems: "center", gap: 6, color: "var(--safe-ink)", fontWeight: 700 }}>
        <span style={dot(true)}><Check size={14} strokeWidth={3} aria-hidden="true" /></span>Password: done
      </li>
      <li aria-hidden="true" style={{ width: 18, height: 2, background: "var(--hairline)" }} />
      <li style={{ display: "inline-flex", alignItems: "center", gap: 6, fontWeight: 800 }}>
        <span style={dot(false)}>2</span>Code from your phone: now
      </li>
    </ol>
  );
}

export function SignIn({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("");
  const [password, setPassword] = useState("");
  const [ticket, setTicket] = useState<string | null>(null);
  const [code, setCode] = useState("");
  const [recovery, setRecovery] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<Busy>(null);
  const canPasskey = passkeysSupported();

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy("password");
    setError(null);
    try {
      const r = await api<LoginAnswer>("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
      if (r.second_step === "code" && r.ticket) {
        setTicket(r.ticket);
        setCode("");
        setRecovery(false);
      } else {
        onDone();
      }
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(null);
    }
  }

  function backToPassword(message: string | null) {
    setTicket(null);
    setPassword("");
    setCode("");
    setError(message);
  }

  async function second(e: React.FormEvent) {
    e.preventDefault();
    if (!ticket) return;
    if (!code.trim()) {
      setError(recovery ? "Type one of your recovery codes first." : "Type the six digits your authenticator app shows now.");
      return;
    }
    setBusy("code");
    setError(null);
    try {
      await api("/api/auth/login/second", { method: "POST", body: JSON.stringify({ ticket, code: code.trim() }) });
      onDone();
    } catch (err) {
      const message = (err as Error).message;
      // The proof that the password was right lasts five minutes; after that the server asks for the password again.
      if (err instanceof ApiError && err.status === 401 && /password again/i.test(message)) backToPassword(message);
      else setError(message);
    } finally {
      setBusy(null);
    }
  }

  async function passkey() {
    setBusy("passkey");
    setError(null);
    try {
      await signInWithPasskey();
      onDone();
    } catch (err) {
      if (err instanceof ApiError && err.status === 409) setError(passkeyHomeWords());
      else setError(passkeyWords(err, "use"));
    } finally {
      setBusy(null);
    }
  }

  const head = (
    <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
      <img src={`${import.meta.env.BASE_URL}favicon.svg`} width={28} height={28} alt="" />
      <h1 style={{ margin: 0, fontSize: 18, fontWeight: 800 }}>FinancialAuthority</h1>
    </div>
  );
  const box = { padding: 24, width: "min(100%, 380px)", display: "flex", flexDirection: "column", gap: 12, boxSizing: "border-box" } as const;

  if (ticket) {
    return (
      <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 16, background: "var(--ground)" }}>
        <form onSubmit={second} className="card" style={box}>
          {head}
          <Steps />
          <label style={field}>
            <span className="lab">{recovery ? "Recovery code" : "Code from your phone"}</span>
            <input
              key={recovery ? "recovery" : "code"}
              className="input"
              value={code}
              onChange={(e) => setCode(e.target.value)}
              inputMode={recovery ? "text" : "numeric"}
              autoComplete={recovery ? "off" : "one-time-code"}
              autoCapitalize="off"
              spellCheck={false}
              autoFocus
              aria-describedby="code-hint"
              style={{ fontSize: 20, letterSpacing: ".12em", fontVariantNumeric: "tabular-nums" }}
            />
            <span id="code-hint" style={{ fontSize: 12, color: "var(--ink-2)" }}>
              The six digits your authenticator app shows now, or one of your recovery codes.
            </span>
          </label>
          <button type="button" onClick={() => { setRecovery((r) => !r); setCode(""); setError(null); }} style={{ alignSelf: "flex-start", background: "none", border: 0, padding: "6px 0", minHeight: 32, font: "inherit", fontSize: 13, fontWeight: 700, color: "var(--paycheck-ink)", cursor: "pointer", textDecoration: "underline" }}>
            {recovery ? "Use the six digits from the app instead" : "My phone is not at hand: use a recovery code"}
          </button>
          {error && <Said text={error} tone="error" />}
          <button className="btn dark" type="submit" disabled={busy !== null}>{busy === "code" ? "Checking…" : "Sign in"}</button>
          <button type="button" className="btn" onClick={() => backToPassword(null)} disabled={busy !== null} style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6 }}>
            <ArrowLeft size={14} aria-hidden="true" />Back to the password
          </button>
          <div style={{ fontSize: 12, color: "var(--ink-2)" }}>The code changes every 30 seconds. If it is not accepted, check that the time on your phone is right, then type the code it shows now. A recovery code works once.</div>
        </form>
      </div>
    );
  }

  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 16, background: "var(--ground)" }}>
      <form onSubmit={submit} className="card" style={box}>
        {head}
        <label style={field}>
          <span className="lab">Username</span>
          <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
        </label>
        <label style={field}>
          <span className="lab">Password</span>
          <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </label>
        {error && <Said text={error} tone="error" />}
        <button className="btn dark" type="submit" disabled={busy !== null}>{busy === "password" ? "Signing in…" : "Sign in"}</button>
        {canPasskey && (
          <>
            <div aria-hidden="true" style={{ display: "flex", alignItems: "center", gap: 8, fontSize: 12, color: "var(--muted)" }}>
              <span style={{ flex: 1, height: 1, background: "var(--hairline)" }} />or<span style={{ flex: 1, height: 1, background: "var(--hairline)" }} />
            </div>
            <button type="button" className="btn" onClick={passkey} disabled={busy !== null} style={{ display: "inline-flex", alignItems: "center", justifyContent: "center", gap: 6 }}>
              <FingerprintPattern size={16} aria-hidden="true" />{busy === "passkey" ? "Waiting for your passkey…" : "Sign in with a passkey"}
            </button>
          </>
        )}
        <div style={{ fontSize: 12, color: "var(--ink-2)", display: "flex", flexDirection: "column", gap: 6 }}>
          <span>The owner's password is the app's <code>FA_PASSWORD</code> setting. On BoxPilot it was made for you and is on the app's card; with Docker Compose it is in the <code>.env</code> file.</span>
          <span>Added by the owner? Use the username they gave you and the password you chose from your link. Forgot it, or lost your phone? Ask the owner for a new link.</span>
        </div>
      </form>
    </div>
  );
}
