// Settings › Notifications: where the morning brief goes. ntfy or Gotify, both free, both in BoxPilot's catalog.
// The reminders for a receipt's last day to return, a warranty's end and the lease's notice sit with the others.
import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { DEMO } from "../demo/shim";
import { when } from "../format";
import { HOURS, hourWord, ReminderSwitches, ShortcutRecipe, ThisDevice } from "../components/Reach";
import { Failed, Loading, Said } from "../components/State";
import { Where } from "../components/Where";
import type { ReceiptReminders } from "../types-receipts";

interface PushState {
  kind: "off" | "ntfy" | "gotify";
  url: string;
  topic: string;
  token_set: boolean;
  brief_hour: number;
  last_brief_day: string;
  configured: boolean;
}

const RETURN_DAYS = [0, 1, 2, 3, 5, 7, 10, 14];
const WARRANTY_DAYS = [0, 1, 3, 7, 14, 30, 60];

/** The choices for "this many days before", with the one saved now among them; 0 is off. */
function dayChoices(preset: number[], now: number): { n: number; word: string }[] {
  const all = preset.includes(now) ? preset : [...preset, now].sort((a, b) => a - b);
  return all.map((n) => ({ n, word: n === 0 ? "Off" : `${n} day${n === 1 ? "" : "s"} before` }));
}

/** A receipt's last day to return, a warranty's end and the lease's written-notice deadline, each sent once. */
function ReceiptReminderSwitches() {
  const r = useApi<{ settings: Partial<ReceiptReminders> }>("/api/v1/push/reminders");
  const [saving, setSaving] = useState(false);
  const [note, setNote] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const title = <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>Reminders for receipts and the lease</h2>;
  if (!r.data) {
    return (
      <section className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
        {title}
        {r.error ? <Failed error={r.error} what="these reminders" onRetry={r.reload} card={false} /> : <Loading what="these reminders" card={false} />}
      </section>
    );
  }
  // An older server leaves these out; the app's own defaults show until it answers with them.
  const s: ReceiptReminders = { returns_days: r.data.settings.returns_days ?? 3, warranty_days: r.data.settings.warranty_days ?? 14, notice: r.data.settings.notice ?? true };
  const put = async (patch: Partial<ReceiptReminders>, what: string) => {
    setSaving(true);
    setNote(null);
    try {
      await api("/api/v1/push/reminders", { method: "PUT", body: JSON.stringify(patch) });
      setNote({ text: `Saved: ${what}.`, tone: "info" });
      r.reload();
    } catch (e) {
      setNote({ text: `Couldn't save it: ${(e as Error).message}`, tone: "error" });
    } finally {
      setSaving(false);
    }
  };
  const beforeWord = (n: number) => (n === 0 ? "off" : `${n} day${n === 1 ? "" : "s"} before`);
  return (
    <section className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
      {title}
      <span style={{ fontSize: 13, color: "var(--ink-2)" }}>
        Each goes once, from the reminder hour above. The dates come from the receipts on <Link to="/documents#receipts">Documents</Link> and the lease on <Link to="/housing">Housing</Link>.
      </span>
      <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(220px, 100%), 1fr))", gap: 10 }}>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">A receipt's last day to return</span>
          <select className="input" value={s.returns_days} disabled={saving || DEMO} onChange={(e) => { const n = Number(e.target.value); put({ returns_days: n }, `a receipt's last day to return, ${beforeWord(n)}`); }}>
            {dayChoices(RETURN_DAYS, s.returns_days).map((c) => <option key={c.n} value={c.n}>{c.word}</option>)}
          </select>
        </label>
        <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">A warranty's end</span>
          <select className="input" value={s.warranty_days} disabled={saving || DEMO} onChange={(e) => { const n = Number(e.target.value); put({ warranty_days: n }, `a warranty's end, ${beforeWord(n)}`); }}>
            {dayChoices(WARRANTY_DAYS, s.warranty_days).map((c) => <option key={c.n} value={c.n}>{c.word}</option>)}
          </select>
        </label>
      </div>
      <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13, minHeight: 32 }}>
        <input type="checkbox" checked={s.notice} disabled={saving || DEMO} onChange={(e) => put({ notice: e.target.checked }, e.target.checked ? "the lease's notice deadline, on" : "the lease's notice deadline, off")} style={{ width: 18, height: 18 }} />
        The lease's notice deadline: the last day to give written notice, three days before it
      </label>
      {note && <Said text={note.text} tone={note.tone} />}
    </section>
  );
}

export function Notifications() {
  const state = useApi<PushState>("/api/v1/push");
  const { data, reload, error } = state;
  const [form, setForm] = useState({ kind: "off", url: "", topic: "", token: "", brief_hour: 7 });
  const [note, setNote] = useState<{ text: string; tone: "info" | "error" } | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (data) setForm({ kind: data.kind, url: data.url, topic: data.topic, token: "", brief_hour: data.brief_hour });
  }, [data]);

  async function save() {
    setBusy(true);
    setNote(null);
    try {
      await api("/api/v1/push", { method: "PUT", body: JSON.stringify({ kind: form.kind, url: form.url, topic: form.topic, brief_hour: form.brief_hour, ...(form.token ? { token: form.token } : {}) }) });
      setForm({ ...form, token: "" });
      setNote({ text: "Saved.", tone: "info" });
      reload();
    } catch (e) {
      setNote({ text: (e as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }
  async function test(path: string) {
    setBusy(true);
    setNote(null);
    try {
      const r = await api<{ ok: boolean; skipped?: boolean; detail?: string; reason?: string }>(path, { method: "POST" });
      setNote(r.ok ? { text: "Sent. Check the phone.", tone: "info" } : { text: r.skipped ? r.reason ?? "Push is off." : `Not delivered: ${r.detail}`, tone: "error" });
    } catch (e) {
      setNote({ text: (e as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 820 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Notifications</h1>
      <p style={{ margin: 0, color: "var(--ink-2)" }}>The morning brief and your reminders, in the app's own sentences. The app can send them itself to this device, or through ntfy or Gotify below. Nothing is sent to anyone but you; the app never emails anyone without your approval.</p>
      <ThisDevice />
      <ReminderSwitches />
      <ReceiptReminderSwitches />
      <section className="card" style={{ padding: 14, display: "flex", flexDirection: "column", gap: 10 }}>
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 800 }}>Or send through ntfy or Gotify</h2>
        {error && !data && <Failed error={error} what="the ntfy and Gotify settings" onRetry={reload} card={false} />}
        {!data && !error && <Loading what="the ntfy and Gotify settings" card={false} />}
        {/* The form shows only once the saved settings are in, so Save can never write the blank defaults over them. */}
        {data && <>
        <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(180px, 100%), 1fr))", gap: 10 }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span className="lab">Service</span>
            <select className="input" value={form.kind} onChange={(e) => setForm({ ...form, kind: e.target.value })}>
              <option value="off">Off</option>
              <option value="ntfy">ntfy</option>
              <option value="gotify">Gotify</option>
            </select>
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span className="lab">Server address</span>
            <input className="input" value={form.url} onChange={(e) => setForm({ ...form, url: e.target.value })} placeholder={form.kind === "gotify" ? "http://gotify:80" : "https://ntfy.sh"} />
          </label>
          {form.kind === "ntfy" && (
            <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <span className="lab">Topic</span>
              <input className="input" value={form.topic} onChange={(e) => setForm({ ...form, topic: e.target.value })} placeholder="a long, private name" />
            </label>
          )}
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span className="lab">{form.kind === "gotify" ? "Application token" : "Access token"} · {data?.token_set ? "set" : "not set"}</span>
            <input className="input" type="password" value={form.token} onChange={(e) => setForm({ ...form, token: e.target.value })} placeholder={data?.token_set ? "Paste a new one to replace it" : "optional for a public ntfy topic"} autoComplete="new-password" />
          </label>
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span className="lab">Morning brief, sent from</span>
            <select className="input" value={form.brief_hour} onChange={(e) => setForm({ ...form, brief_hour: Number(e.target.value) })}>
              {HOURS.map((h) => <option key={h} value={h}>{hourWord(h)}</option>)}
            </select>
          </label>
        </div>
        <span style={{ fontSize: 12, color: "var(--ink-2)" }}>The morning brief goes once a day, from this hour, to this device and to ntfy or Gotify. Reminders have their own hour, under Reminders above.</span>
        {form.kind === "ntfy" && <Where id="ntfy" />}
        {form.kind === "gotify" && <Where id="gotify" />}
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <button className="btn dark" type="button" disabled={busy} onClick={save}>{busy ? "Working…" : "Save"}</button>
          <button className="btn" type="button" disabled={busy || !data.configured} onClick={() => test("/api/v1/push/test")}>Send a test</button>
          <button className="btn" type="button" disabled={busy} onClick={() => test("/api/v1/push/brief")}>Send today's brief now</button>
          {note && <Said text={note.text} tone={note.tone} />}
        </div>
        {!data.configured && <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Send a test works once ntfy or Gotify is chosen and saved.</div>}
        {data.last_brief_day && <div style={{ fontSize: 12, color: "var(--ink-2)" }}>Last brief sent on {when(data.last_brief_day)}.</div>}
        </>}
      </section>
      <ShortcutRecipe />
    </div>
  );
}
