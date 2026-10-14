import { useState } from "react";
import { api } from "../api";

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
          <strong style={{ fontSize: 18 }}>FinancialAuthority</strong>
        </div>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">Username</span>
          <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoComplete="username" />
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">Password</span>
          <input className="input" type="password" value={password} onChange={(e) => setPassword(e.target.value)} autoComplete="current-password" />
        </label>
        {error && <div style={{ color: "var(--critical)", fontSize: 13 }}>{error}</div>}
        <button className="btn dark" type="submit" disabled={busy}>Sign in</button>
        <div style={{ fontSize: 12, color: "var(--muted)" }}>BoxPilot generated the password; it is on the app's card there.</div>
      </form>
    </div>
  );
}
