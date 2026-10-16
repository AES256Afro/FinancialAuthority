// The devices signed in: each browser with a picture of what it runs on, when it was last used and from where;
// end one, or every one but this.
import { Monitor, Smartphone, Tablet } from "lucide-react";
import { useState } from "react";
import { api, useApi } from "../api";
import { when } from "../format";
import { ConfirmButton } from "./ConfirmButton";
import { Failed, Loading, Said } from "./State";

interface Row { id: number; created_at: string; last_seen_at: string | null; user_agent: string; ip: string; this: boolean }

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

export function Devices() {
  const state = useApi<{ sessions: Row[] }>("/api/auth/sessions");
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
  return (
    <section style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <h2 style={{ margin: 0, fontSize: 16, fontWeight: 800 }}>Devices signed in</h2>
      <p style={{ margin: 0, color: "var(--ink-2)", fontSize: 13 }}>Every phone and computer browser signed in as you. Signing one out ends it for good: it needs the password (or a passkey) again. A new password signs out all of them.</p>
      {error && !data && <Failed error={error} what="the devices signed in" onRetry={reload} />}
      {!data && !error && <Loading what="the devices signed in" />}
      {data && (
        <div className="card" style={{ padding: "6px 14px", overflowX: "auto" }}>
          <table className="ledger">
            <thead><tr><th>Device</th><th>Last used</th><th>Signed in</th><th><span className="sr-only">Sign out</span></th></tr></thead>
            <tbody>
              {rows.map((r) => (
                <tr key={r.id}>
                  <td>
                    <div style={{ display: "flex", gap: 8, alignItems: "flex-start" }}>
                      <DeviceIcon ua={r.user_agent} />
                      <div>{device(r.user_agent)}{r.this && <strong style={{ marginLeft: 6, fontSize: 12 }}>· this one</strong>}<div style={{ fontSize: 11, color: "var(--muted)" }}>{r.ip}</div></div>
                    </div>
                  </td>
                  <td style={{ whiteSpace: "nowrap" }}>{r.last_seen_at ? when(r.last_seen_at) : "not yet"}</td>
                  <td style={{ whiteSpace: "nowrap" }}>{when(r.created_at)}</td>
                  <td style={{ textAlign: "right" }}>
                    {!r.this && <ConfirmButton label="Sign out" question="Sign this browser out for good?" yes="Sign it out" no="Keep" onConfirm={() => end({ session_id: r.id })} />}
                  </td>
                </tr>
              ))}
              {rows.length === 0 && <tr><td colSpan={4} style={{ color: "var(--ink-2)" }}>Sign-in is off on this install (no password is set).</td></tr>}
            </tbody>
          </table>
        </div>
      )}
      {rows.filter((r) => !r.this).length > 0 && (
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <ConfirmButton label="Sign out everywhere else" question="Every other browser will need the password again." yes="Sign them all out" no="Keep them" onConfirm={() => end({ all_others: true })} />
        </div>
      )}
      {note && <Said text={note} />}
    </section>
  );
}
