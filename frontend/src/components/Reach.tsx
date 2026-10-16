// Reach: notifications from the app itself on this device, the reminders you switch on, and a Siri Shortcut
// that reads the glance. On iPhone and iPad, web push works for the app added to the Home Screen (iOS 16.4 or
// later); an Apple Watch shows whatever the iPhone shows. Nothing here sends to anyone but you.
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { DEMO } from "../demo/shim";
import { when } from "../format";
import { ConfirmButton } from "./ConfirmButton";
import { Failed, Loading, Said } from "./State";

interface Devices {
  public_key: string;
  devices: { id: string; label: string; created: string | null; last_ok: string | null; last_error: string }[];
}

interface Reminders {
  settings: { bills_days: number; payday: boolean; statement: boolean; checkin: boolean; hour: number };
  today: { title: string; body: string; link: string }[];
}

type Note = { text: string; tone: "info" | "error" } | null;

/** An hour of the day in words: 0 is "12 am (midnight)", 7 is "7 am", 12 is "12 pm (noon)". */
export function hourWord(h: number): string {
  if (h === 0) return "12 am (midnight)";
  if (h === 12) return "12 pm (noon)";
  return h < 12 ? `${h} am` : `${h - 12} pm`;
}

export const HOURS = Array.from({ length: 24 }, (_, h) => h);

function keyBytes(base64: string): ArrayBuffer {
  const pad = "=".repeat((4 - (base64.length % 4)) % 4);
  const raw = atob((base64 + pad).replace(/-/g, "+").replace(/_/g, "/"));
  const out = new Uint8Array(new ArrayBuffer(raw.length));
  for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
  return out.buffer;
}

function deviceLabel(): string {
  const ua = navigator.userAgent;
  const ipad = /iPad/.test(ua) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  const kind = ipad ? "iPad" : /iPhone/.test(ua) ? "iPhone" : /Android/.test(ua) ? "Android" : /Mac/.test(ua) ? "Mac" : /Windows/.test(ua) ? "Windows" : "This device";
  const browser = /Edg\//.test(ua) ? "Edge" : /Chrome\//.test(ua) ? "Chrome" : /Firefox\//.test(ua) ? "Firefox" : /Safari\//.test(ua) ? "Safari" : "";
  return browser ? `${kind}, ${browser}` : kind;
}

function standalone(): boolean {
  return window.matchMedia?.("(display-mode: standalone)").matches || (navigator as Navigator & { standalone?: boolean }).standalone === true;
}

export function ThisDevice() {
  const dev = useApi<Devices>("/api/v1/push/devices");
  const [note, setNote] = useState<Note>(null);
  const [busy, setBusy] = useState(false);
  const supported = "serviceWorker" in navigator && "PushManager" in window && "Notification" in window;
  const apple = /iPhone|iPad|iPod/.test(navigator.userAgent) || (navigator.platform === "MacIntel" && navigator.maxTouchPoints > 1);
  async function turnOn() {
    if (!dev.data) return;
    setBusy(true);
    setNote(null);
    try {
      const permission = await Notification.requestPermission();
      if (permission !== "granted") {
        setNote({ text: "Notifications are blocked for this app. Allow them in the device's settings, then try again.", tone: "error" });
        return;
      }
      const reg = (await navigator.serviceWorker.getRegistration()) ?? (await navigator.serviceWorker.register("/sw.js"));
      await navigator.serviceWorker.ready;
      const sub = (await reg.pushManager.getSubscription()) ?? (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(dev.data.public_key) }));
      const json = sub.toJSON() as { endpoint: string; keys: Record<string, string> };
      await api("/api/v1/push/devices", { method: "POST", body: JSON.stringify({ endpoint: json.endpoint, keys: json.keys, label: deviceLabel() }) });
      setNote({ text: "This device will get the brief and the reminders.", tone: "info" });
      dev.reload();
    } catch (e) {
      setNote({ text: `Could not turn them on here: ${(e as Error).message}`, tone: "error" });
    } finally {
      setBusy(false);
    }
  }
  async function test() {
    setBusy(true);
    setNote(null);
    try {
      const r = await api<{ ok: boolean; detail?: string; reason?: string }>("/api/v1/push/test", { method: "POST" });
      setNote(r.ok ? { text: "Sent. It should arrive in a few seconds.", tone: "info" } : { text: `Not delivered: ${r.detail || r.reason}`, tone: "error" });
      dev.reload();
    } catch (e) {
      setNote({ text: (e as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }
  async function remove(id: string) {
    await api(`/api/v1/push/devices/${id}`, { method: "DELETE" });
    dev.reload();
  }
  return (
    <section className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
      <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>On this phone, tablet or computer</h2>
      <span style={{ fontSize: 13, color: "var(--ink-2)" }}>The app sends its own notifications: no ntfy needed. An Apple Watch shows whatever your iPhone shows.</span>
      {apple && !standalone() && (
        <ol style={{ margin: 0, paddingLeft: 20, fontSize: 13, display: "flex", flexDirection: "column", gap: 3 }}>
          <li>Open this page in Safari.</li>
          <li>Tap Share, then Add to Home Screen, then Add.</li>
          <li>Open the app from the Home Screen icon and come back to Settings, Notifications.</li>
          <li>Tap Turn on notifications here and allow them.</li>
        </ol>
      )}
      {!supported && !apple && <span style={{ fontSize: 13, color: "var(--warning-ink)" }}>This browser cannot receive web push. Chrome, Edge, Firefox and Safari can.</span>}
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
        <button type="button" className="btn dark" disabled={busy || DEMO || !supported || !dev.data || (apple && !standalone())} onClick={turnOn}>Turn on notifications here</button>
        <button type="button" className="btn" disabled={busy || DEMO || !dev.data?.devices.length} onClick={test}>{busy ? "Working…" : "Send a test"}</button>
        {note && <Said text={note.text} tone={note.tone} />}
      </div>
      {dev.error && !dev.data && <Failed error={dev.error} what="your devices" onRetry={dev.reload} card={false} />}
      {!dev.data && !dev.error && <Loading what="your devices" card={false} />}
      {dev.data && dev.data.devices.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {dev.data.devices.map((d) => (
            <div key={d.id} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, padding: "6px 10px", borderRadius: 8, background: "var(--surface-2)", flexWrap: "wrap" }}>
              <strong>{d.label}</strong>
              <span style={{ color: "var(--ink-2)" }}>{d.last_ok ? `last delivered ${when(d.last_ok)}` : "nothing sent yet"}{d.last_error ? ` · last try failed: ${d.last_error}` : ""}</span>
              <span style={{ marginLeft: "auto" }}>
                <ConfirmButton label="Remove" question={`Stop notifications on ${d.label}?`} yes="Yes, remove it" no="Keep it" onConfirm={() => remove(d.id)} style={{ minHeight: 32, padding: "3px 9px" }} />
              </span>
            </div>
          ))}
        </div>
      )}
    </section>
  );
}

export function ReminderSwitches() {
  const r = useApi<Reminders>("/api/v1/push/reminders");
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const title = <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>Reminders</h2>;
  if (!r.data) {
    return (
      <section className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
        {title}
        {r.error ? <Failed error={r.error} what="your reminders" onRetry={r.reload} card={false} /> : <Loading what="your reminders" card={false} />}
      </section>
    );
  }
  const s = r.data.settings;
  const put = async (patch: Partial<Reminders["settings"]>) => {
    setSaving(true);
    setNote(null);
    try {
      await api("/api/v1/push/reminders", { method: "PUT", body: JSON.stringify(patch) });
      setNote({ text: "Saved.", tone: "info" });
      r.reload();
    } catch (e) {
      setNote({ text: (e as Error).message, tone: "error" });
    } finally {
      setSaving(false);
    }
  };
  const Toggle = ({ k, label }: { k: "payday" | "statement" | "checkin"; label: string }) => (
    <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, minHeight: 32 }}>
      <input type="checkbox" checked={s[k]} disabled={saving || DEMO} onChange={(e) => put({ [k]: e.target.checked })} style={{ width: 18, height: 18 }} />{label}
    </label>
  );
  return (
    <section className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
      {title}
      <span style={{ fontSize: 13, color: "var(--ink-2)" }}>Each is sent once, from the hour you choose, to this device and every service set up on this page.</span>
      <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, flexWrap: "wrap" }}>
        A bill
        <select className="input" value={s.bills_days} disabled={saving || DEMO} onChange={(e) => put({ bills_days: Number(e.target.value) })} style={{ minHeight: 32, padding: "2px 8px" }} aria-label="Days before a bill">
          <option value={0}>never</option>
          {[1, 2, 3, 5, 7].map((n) => <option key={n} value={n}>{n} day{n === 1 ? "" : "s"} before</option>)}
        </select>
        it is due
      </label>
      <Toggle k="payday" label="Payday, with the moves on the payday sheet" />
      <Toggle k="statement" label="A card's statement day, when the statement would show more than 30% of the limit used" />
      <Toggle k="checkin" label="The weekly check-in, on Sundays" />
      <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, flexWrap: "wrap" }}>
        Send reminders from
        <select className="input" value={s.hour} disabled={saving || DEMO} onChange={(e) => put({ hour: Number(e.target.value) })} style={{ minHeight: 32, padding: "2px 8px" }}>
          {HOURS.map((h) => <option key={h} value={h}>{hourWord(h)}</option>)}
        </select>
      </label>
      {note && <Said text={note.text} tone={note.tone} />}
      {r.data.today.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 4, fontSize: 13 }}>
          <span className="lab">Due today</span>
          {r.data.today.map((t, i) => <Link key={i} to={t.link} style={{ color: "var(--ink)" }}>{t.title}</Link>)}
        </div>
      )}
    </section>
  );
}

export function ShortcutRecipe() {
  const g = useApi<{ text: string }>("/api/v1/glance");
  const url = `${window.location.origin}/api/v1/glance`;
  return (
    <section className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
      <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>Ask Siri, or glance at a widget or your watch</h2>
      {g.data && <div style={{ fontSize: 13, padding: "8px 10px", borderRadius: 8, background: "var(--surface-2)" }}>“{g.data.text}”</div>}
      <ol style={{ margin: 0, paddingLeft: 20, fontSize: 13, display: "flex", flexDirection: "column", gap: 4 }}>
        <li>On <Link to="/settings/clients">Clients</Link>, make a key. A key can only read. Copy it.</li>
        <li>In the Shortcuts app, make a new shortcut and add <strong>Get Contents of URL</strong>. Set the URL to the address below; under Headers add <code>Authorization</code> with the value <code>Bearer</code>, a space, and the key.</li>
        <li>Add <strong>Get Dictionary Value</strong> for the key <code>text</code>, then <strong>Speak Text</strong> (or Show Result).</li>
        <li>Name it <em>Money check</em>. Say "Hey Siri, money check"; add the Shortcuts widget to the Home Screen; on Apple Watch, add the Shortcuts complication and tap it.</li>
      </ol>
      <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
        <span className="lab">The glance address</span>
        <input className="input num" readOnly value={url} onFocus={(e) => e.target.select()} style={{ fontSize: 12 }} />
      </label>
      <span style={{ fontSize: 12, color: "var(--ink-2)" }}>The key can only look; it cannot confirm, approve or change anything, and you can revoke it on Clients at any time.</span>
    </section>
  );
}
