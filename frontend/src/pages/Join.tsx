// Join: the person the owner added opens their one-time link here and chooses their own password; the owner never
// sees it. The link's secret part is after the #, so it never reaches a server log; this page sends it once, in a
// request body, never in an address, and never shows or logs it.
import { Eye, EyeOff, OctagonAlert } from "lucide-react";
import { useEffect, useState } from "react";
import { ApiError, api } from "../api";
import { Said } from "../components/State";
import { when } from "../format";
import type { JoinCheck } from "../types-people";

type Stage = { at: "checking" } | { at: "bad"; text: string; retry: boolean } | { at: "ready"; info: JoinCheck };

const field = { display: "flex", flexDirection: "column", gap: 4 } as const;
const box = { padding: 24, width: "min(100%, 440px)", display: "flex", flexDirection: "column", gap: 14, boxSizing: "border-box" } as const;
const page = { minHeight: "100vh", display: "grid", placeItems: "center", padding: 16, background: "var(--ground)" } as const;

function tokenFromLink(): string {
  return window.location.hash.replace(/^#/, "").trim();
}

function Brand() {
  return (
    <div style={{ display: "flex", alignItems: "center", gap: 10, fontSize: 14, fontWeight: 800 }}>
      <img src={`${import.meta.env.BASE_URL}favicon.svg`} width={24} height={24} alt="" />
      FinancialAuthority
    </div>
  );
}

export function Join() {
  const [token] = useState(tokenFromLink);
  const [stage, setStage] = useState<Stage>({ at: "checking" });
  const [tick, setTick] = useState(0);
  const [password, setPassword] = useState("");
  const [again, setAgain] = useState("");
  const [show, setShow] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  useEffect(() => {
    document.title = "Choose your password · FinancialAuthority";
  }, []);

  useEffect(() => {
    if (!token) {
      setStage({ at: "bad", text: "This page needs the whole link you were sent, including the part after the # sign. Open the link again from the message, or ask for a new one.", retry: false });
      return;
    }
    let alive = true;
    setStage({ at: "checking" });
    api<JoinCheck>("/api/auth/join/check", { method: "POST", body: JSON.stringify({ token }) })
      .then((info) => alive && setStage({ at: "ready", info }))
      .catch((e: ApiError) => alive && setStage({ at: "bad", text: e.message, retry: e.status === 0 }));
    return () => {
      alive = false;
    };
  }, [token, tick]);

  async function join(e: React.FormEvent, info: JoinCheck) {
    e.preventDefault();
    setError(null);
    if (password.length < info.min_length) return setError(`Choose a password of at least ${info.min_length} characters. It has ${password.length} now.`);
    if (info.username && password.toLowerCase().includes(info.username.toLowerCase())) return setError("Leave your username out of the password.");
    if (password !== again) return setError("The two passwords are not the same. Type it again in both boxes.");
    setBusy(true);
    try {
      const r = await api<{ ok: boolean; role: string }>("/api/auth/join", { method: "POST", body: JSON.stringify({ token, password }) });
      // A fresh page, and the link's address replaced so it is not left in this browser's history.
      window.location.replace(r.role === "shared" ? "/shared" : "/today");
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  if (stage.at === "checking") {
    return (
      <div style={page}>
        <div className="card" style={box} role="status" aria-live="polite">
          <Brand />
          <h1 style={{ margin: 0, fontSize: 18, fontWeight: 800 }}>Checking your link…</h1>
        </div>
      </div>
    );
  }

  if (stage.at === "bad") {
    return (
      <div style={page}>
        <div className="card" style={box}>
          <Brand />
          <h1 role="alert" style={{ margin: 0, fontSize: 16, fontWeight: 800, display: "flex", gap: 8, alignItems: "flex-start", color: "var(--critical-ink)" }}>
            <OctagonAlert size={18} aria-hidden="true" style={{ flexShrink: 0, marginTop: 2 }} />
            {stage.text}
          </h1>
          {stage.retry && <div><button type="button" className="btn" onClick={() => setTick((t) => t + 1)}>Try again</button></div>}
        </div>
      </div>
    );
  }

  const info = stage.info;
  const Toggle = show ? EyeOff : Eye;
  const long = password.length >= info.min_length;
  return (
    <div style={page}>
      <form onSubmit={(e) => join(e, info)} className="card" style={box}>
        <Brand />
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>{info.display_name}, choose your password</h1>
        <div style={{ padding: "10px 12px", borderRadius: 10, background: "var(--surface-2)", fontSize: 13, display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">What you will see</span>
          <span>{info.role_words}</span>
        </div>
        <label style={field}>
          <span className="lab">Your username</span>
          <input className="input" value={info.username} readOnly autoComplete="username" aria-describedby="join-username" />
          <span id="join-username" style={{ fontSize: 12, color: "var(--ink-2)" }}>You sign in with this username and the password you choose here.</span>
        </label>
        <label style={field}>
          <span className="lab">Password</span>
          <input className="input" type={show ? "text" : "password"} value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="new-password" autoCapitalize="off" spellCheck={false} autoFocus aria-describedby="join-rule" />
        </label>
        <button type="button" onClick={() => setShow((s) => !s)} aria-pressed={show} style={{ alignSelf: "flex-start", display: "inline-flex", alignItems: "center", gap: 6, background: "none", border: 0, padding: "6px 0", minHeight: 32, font: "inherit", fontSize: 13, fontWeight: 700, color: "var(--paycheck-ink)", cursor: "pointer" }}>
          <Toggle size={15} aria-hidden="true" />{show ? "Hide the passwords" : "Show the passwords"}
        </button>
        <div id="join-rule" style={{ fontSize: 12, color: "var(--ink-2)", display: "flex", flexDirection: "column", gap: 2 }}>
          <span style={{ fontWeight: 700, color: long ? "var(--safe-ink)" : "var(--ink-2)" }}>At least {info.min_length} characters ({password.length} so far).</span>
          <span>A few words in a row are easy to remember and hard to guess, for example four words that have nothing to do with each other. Leave your username out of it.</span>
        </div>
        <label style={field}>
          <span className="lab">The same password again</span>
          <input className="input" type={show ? "text" : "password"} value={again} onChange={(e) => setAgain(e.target.value)} autoComplete="new-password" autoCapitalize="off" spellCheck={false} />
        </label>
        {error && <Said text={error} tone="error" />}
        <button className="btn dark" type="submit" disabled={busy}>{busy ? "Setting your password…" : "Set my password and sign in"}</button>
        <div style={{ fontSize: 12, color: "var(--ink-2)" }}>
          Only you know this password; the person who added you never sees it. This link works once{info.expires_at ? `, until ${when(info.expires_at)}` : ""}.
        </div>
      </form>
    </div>
  );
}
