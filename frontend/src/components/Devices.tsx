// Signed-in browsers: each with when it was last used and from where; end one, or every one but this.
import { useState } from "react";
import { api, useApi } from "../api";

interface Row { id: number; created_at: string; last_seen_at: string | null; user_agent: string; ip: string; this: boolean }

function when(iso: string | null): string {
  if (!iso) return "not yet";
  const d = new Date(iso);
  return Number.isNaN(d.getTime()) ? iso : d.toLocaleString(undefined, { weekday: "short", month: "short", day: "numeric", hour: "numeric", minute: "2-digit" });
}

function device(ua: string): string {
  const os = /iPhone/.test(ua) ? "iPhone" : /iPad/.test(ua) ? "iPad" : /Android/.test(ua) ? "Android" : /Mac OS X/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : /Linux/.test(ua) ? "Linux" : "A device";
  const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "";
  return browser ? `${browser} on ${os}` : os;
}

export function Devices() {
  const { data, reload, error } = useApi<{ sessions: Row[] }>("/api/auth/sessions");
  const [confirming, setConfirming] = useState<number | "all" | null>(null);
  const [note, setNote] = useState<string | null>(null);
  async function end(body: { session_id?: number; all_others?: boolean }) {
    try {
      const r = await api<{ ended: number }>("/api/auth/sessions/end", { method: "POST", body: JSON.stringify(body) });
      setNote(`Signed out ${r.ended} ${r.ended === 1 ? "browser" : "browsers"}.`);
      setConfirming(null);
      reload();
    } catch (e) {
      setNote((e as Error).message);
    }
  }
  if (error) return null;
  const rows = data?.sessions ?? [];
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <h2 style={{ margin: 0, fontSize: 16, fontWeight: 800 }}>Signed-in browsers</h2>
      <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 13 }}>Every browser signed in as you. Signing out on one ends it for good; a new password signs out all of them.</p>
      <div className="card" style={{ padding: "6px 14px", overflowX: "auto" }}>
        <table className="ledger">
          <thead><tr><th>Browser</th><th>Last used</th><th>Signed in</th><th /></tr></thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.id}>
                <td>{device(r.user_agent)}{r.this && <strong style={{ marginLeft: 6, fontSize: 12 }}>· this one</strong>}<div style={{ fontSize: 11, color: "var(--muted)" }}>{r.ip}</div></td>
                <td style={{ whiteSpace: "nowrap" }}>{when(r.last_seen_at)}</td>
                <td style={{ whiteSpace: "nowrap" }}>{when(r.created_at)}</td>
                <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>
                  {!r.this && (confirming === r.id
                    ? <><button className="btn dark" type="button" onClick={() => end({ session_id: r.id })}>Sign it out</button> <button className="btn" type="button" onClick={() => setConfirming(null)}>Keep</button></>
                    : <button className="btn" type="button" onClick={() => setConfirming(r.id)}>Sign out</button>)}
                </td>
              </tr>
            ))}
            {rows.length === 0 && <tr><td colSpan={4} style={{ color: "var(--muted)" }}>Sign-in is off on this install (no password is set).</td></tr>}
          </tbody>
        </table>
      </div>
      {rows.filter((r) => !r.this).length > 0 && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          {confirming === "all"
            ? <><span style={{ fontSize: 13 }}>Every other browser will need the password again.</span><button className="btn dark" type="button" onClick={() => end({ all_others: true })}>Sign them all out</button><button className="btn" type="button" onClick={() => setConfirming(null)}>Keep them</button></>
            : <button className="btn" type="button" onClick={() => setConfirming("all")}>Sign out everywhere else</button>}
        </div>
      )}
      {note && <div style={{ fontSize: 13, color: "var(--ink-2)" }}>{note}</div>}
    </section>
  );
}
