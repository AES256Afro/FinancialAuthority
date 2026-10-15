// Reach: notifications from the app itself on this device, the reminders you switch on, and a Siri Shortcut
// that reads the glance. On iPhone and iPad, web push works for the app added to the Home Screen (iOS 16.4 or
// later); an Apple Watch shows whatever the iPhone shows. Nothing here sends to anyone but you.
import { useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { DEMO } from "../demo/shim";

interface Devices {
  public_key: string;
  devices: { id: string; label: string; created: string | null; last_ok: string | null; last_error: string }[];
}

interface Reminders {
  settings: { bills_days: number; payday: boolean; statement: boolean; checkin: boolean; hour: number };
  today: { title: string; body: string; link: string }[];
}

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
  const [note, setNote] = useState<string | null>(null);
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
        setNote("Notifications are blocked for this app. Allow them in the device's settings, then try again.");
        return;
      }
      const reg = (await navigator.serviceWorker.getRegistration()) ?? (await navigator.serviceWorker.register("/sw.js"));
      await navigator.serviceWorker.ready;
      const sub = (await reg.pushManager.getSubscription()) ?? (await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: keyBytes(dev.data.public_key) }));
      const json = sub.toJSON() as { endpoint: string; keys: Record<string, string> };
      await api("/api/v1/push/devices", { method: "POST", body: JSON.stringify({ endpoint: json.endpoint, keys: json.keys, label: deviceLabel() }) });
      setNote("This device will get the brief and the reminders.");
      dev.reload();
    } catch (e) {
      setNote(`Could not turn them on here: ${(e as Error).message}`);
    } finally {
      setBusy(false);
    }
  }
  async function test() {
    const r = await api<{ ok: boolean; detail?: string; reason?: string }>("/api/v1/push/test", { method: "POST" });
    setNote(r.ok ? "Sent. It should arrive in a few seconds." : `Not delivered: ${r.detail || r.reason}`);
    dev.reload();
  }
  return (
    <section className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
      <strong style={{ fontSize: 15 }}>On this phone, tablet or computer</strong>
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
        <button type="button" className="btn" disabled={busy || DEMO || !dev.data?.devices.length} onClick={test}>Send a test</button>
        {note && <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{note}</span>}
      </div>
      {dev.data && dev.data.devices.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
          {dev.data.devices.map((d) => (
            <div key={d.id} style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, padding: "6px 10px", borderRadius: 8, background: "var(--surface-2)", flexWrap: "wrap" }}>
              <strong>{d.label}</strong>
              <span style={{ color: "var(--muted)" }}>{d.last_ok ? `last delivered ${d.last_ok.slice(0, 10)}` : "nothing sent yet"}{d.last_error ? ` · last try: ${d.last_error}` : ""}</span>
              <button type="button" className="btn" style={{ marginLeft: "auto", minHeight: 30, padding: "3px 9px" }} onClick={async () => { await api(`/api/v1/push/devices/${d.id}`, { method: "DELETE" }); dev.reload(); }}>Remove</button>
            </div>
          ))}
        </div>
      )}
      {dev.error && <span style={{ fontSize: 13, color: "var(--critical)" }}>{dev.error.message}</span>}
    </section>
  );
}

export function ReminderSwitches() {
  const r = useApi<Reminders>("/api/v1/push/reminders");
  const [saving, setSaving] = useState(false);
  if (!r.data) return null;
  const s = r.data.settings;
  const put = async (patch: Partial<Reminders["settings"]>) => {
    setSaving(true);
    try {
      await api("/api/v1/push/reminders", { method: "PUT", body: JSON.stringify(patch) });
      r.reload();
    } finally {
      setSaving(false);
    }
  };
  const Toggle = ({ k, label }: { k: "payday" | "statement" | "checkin"; label: string }) => (
    <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
      <input type="checkbox" checked={s[k]} disabled={saving || DEMO} onChange={(e) => put({ [k]: e.target.checked })} style={{ width: 18, height: 18 }} />{label}
    </label>
  );
  return (
    <section className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
      <strong style={{ fontSize: 15 }}>Reminders</strong>
      <span style={{ fontSize: 13, color: "var(--ink-2)" }}>Each is sent once, from the hour you choose, to every device and service set up above.</span>
      <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, flexWrap: "wrap" }}>
        A bill
        <select className="input" value={s.bills_days} disabled={saving || DEMO} onChange={(e) => put({ bills_days: Number(e.target.value) })} style={{ minHeight: 32, padding: "2px 8px" }} aria-label="Days before a bill">
          <option value={0}>never</option>
          {[1, 2, 3, 5, 7].map((n) => <option key={n} value={n}>{n} day{n === 1 ? "" : "s"} before</option>)}
        </select>
        it is due
      </label>
      <Toggle k="payday" label="Payday, with the moves on the sheet" />
      <Toggle k="statement" label="A card's statement day, when it would report over 30%" />
      <Toggle k="checkin" label="The weekly check-in, on Sundays" />
      <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
        From
        <input className="input num" type="number" min={0} max={23} value={s.hour} disabled={saving || DEMO} onChange={(e) => put({ hour: Number(e.target.value) })} style={{ width: 70, minHeight: 32 }} aria-label="Hour" />
        o'clock
      </label>
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
      <strong style={{ fontSize: 15 }}>Ask Siri, or glance at a widget or your watch</strong>
      {g.data && <div style={{ fontSize: 13, padding: "8px 10px", borderRadius: 8, background: "var(--surface-2)" }}>“{g.data.text}”</div>}
      <ol style={{ margin: 0, paddingLeft: 20, fontSize: 13, display: "flex", flexDirection: "column", gap: 4 }}>
        <li>On <Link to="/settings/clients">Clients</Link>, make a key with the <strong>read</strong> scope only. Copy it.</li>
        <li>In the Shortcuts app, make a new shortcut and add <strong>Get Contents of URL</strong>. Set the URL to the address below; under Headers add <code>Authorization</code> with the value <code>Bearer</code>, a space, and the key.</li>
        <li>Add <strong>Get Dictionary Value</strong> for the key <code>text</code>, then <strong>Speak Text</strong> (or Show Result).</li>
        <li>Name it <em>Money check</em>. Say "Hey Siri, money check"; add the Shortcuts widget to the Home Screen; on Apple Watch, add the Shortcuts complication and tap it.</li>
      </ol>
      <input className="input num" readOnly value={url} onFocus={(e) => e.target.select()} aria-label="The glance address" style={{ fontSize: 12 }} />
      <span style={{ fontSize: 12, color: "var(--muted)" }}>The read key can only look; it cannot confirm, approve or change anything, and you can revoke it on Clients at any time.</span>
    </section>
  );
}
