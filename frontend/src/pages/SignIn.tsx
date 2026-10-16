import { useState } from "react";
import { api } from "../api";
import { Said } from "../components/State";

export function SignIn({ onDone }: { onDone: () => void }) {
  const [username, setUsername] = useState("owner");
  const [password, setPassword] = useState("");
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  async function submit(e: React.FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      await api("/api/auth/login", { method: "POST", body: JSON.stringify({ username, password }) });
      onDone();
    } catch (err) {
      setError((err as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ minHeight: "100vh", display: "grid", placeItems: "center", padding: 16, background: "var(--ground)" }}>
      <form onSubmit={submit} className="card" style={{ padding: 24, width: "min(100%, 380px)", display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "center", gap: 10 }}>
          <img src={`${import.meta.env.BASE_URL}favicon.svg`} width={28} height={28} alt="" />
          <h1 style={{ margin: 0, fontSize: 18, fontWeight: 800 }}>FinancialAuthority</h1>
        </div>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">Username</span>
          <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">Password</span>
          <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </label>
        {error && <Said text={error} tone="error" />}
        <button className="btn dark" type="submit" disabled={busy}>{busy ? "Signing in…" : "Sign in"}</button>
        <div style={{ fontSize: 12, color: "var(--ink-2)" }}>The password is the app's <code>FA_PASSWORD</code> setting. On BoxPilot it was made for you and is on the app's card; with Docker Compose it is in the <code>.env</code> file.</div>
      </form>
    </div>
  );
}
