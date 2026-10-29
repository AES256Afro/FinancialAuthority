// Settings › People: who else signs in, and what each sign-in sees. The picture first: every sign-in against the
// accounts, grouped as shared, yours, and each person's. Then each person (their status and what to change), adding
// a person (with the one-time link you hand over yourself), and whose each account is. Only the owner reaches it.
import { Copy, Eye, EyeOff, Link2, User, UserPlus, Users } from "lucide-react";
import { useEffect, useRef, useState, type CSSProperties } from "react";
import { Link } from "react-router-dom";
import { api, useApi } from "../api";
import { ConfirmButton } from "../components/ConfirmButton";
import { Empty, Said, Show } from "../components/State";
import { StatusPill } from "../components/Status";
import { when } from "../format";
import type { Status } from "../guide";
import { KIND_WORD, keyToMark, markKey, ownerName, possessive, roleOf, type Invite, type MarkedAccount, type MePerson, type PeopleOut, type PersonRow } from "../types-people";
import { WhoTag } from "./Shared";

type Note = { text: string; tone: "info" | "error" } | null;
type PersonRole = PersonRow["role"];

const ROLE_TITLE: Record<PersonRole, string> = { everything: "Everything", shared: "Shared accounts only" };

const h2 = { margin: 0, fontSize: 16, fontWeight: 800 } as const;
const intro = { margin: 0, color: "var(--ink-2)", fontSize: 13 } as const;
const field = { display: "flex", flexDirection: "column", gap: 4 } as const;
const SMALL: CSSProperties = { minHeight: 32, padding: "4px 10px", fontSize: 12 };

// ---- the picture: who sees which accounts ----------------------------------------------------------------------

interface Group {
  key: string;
  word: string;
  shared: boolean;
  accounts: MarkedAccount[];
}

function groupsOf(d: PeopleOut): Group[] {
  return [
    { key: "shared", word: "Shared", shared: true, accounts: d.accounts.filter((a) => markKey(a) === "shared") },
    { key: "owner", word: "Yours", shared: false, accounts: d.accounts.filter((a) => markKey(a) === "owner") },
    ...d.people.map((p) => ({ key: `p${p.id}`, word: possessive(p.display_name), shared: false, accounts: d.accounts.filter((a) => markKey(a) === `p${p.id}`) })),
  ];
}

/** The groups a sign-in reaches, by the rules the server keeps: everything reaches all; shared reaches the shared
 * accounts and the person's own; a person turned off reaches none. */
function reaches(p: PersonRow | null, key: string): boolean {
  if (p === null) return true;
  if (!p.active) return false;
  return p.role === "everything" || key === "shared" || key === `p${p.id}`;
}

function Cell({ sees }: { sees: boolean }) {
  const Icon = sees ? Eye : EyeOff;
  return (
    <td style={{ padding: 4, borderTop: 0 }}>
      <span style={{ display: "flex", alignItems: "center", justifyContent: "center", gap: 5, minHeight: 34, borderRadius: 8, fontSize: 12, fontWeight: 800, whiteSpace: "nowrap", padding: "0 8px", background: sees ? "var(--paycheck-soft)" : "transparent", color: sees ? "var(--paycheck-ink)" : "var(--muted)", border: sees ? "1px solid transparent" : "1px dashed var(--hairline)" }}>
        <Icon size={14} aria-hidden="true" />{sees ? "Sees" : "Does not see"}
      </span>
    </td>
  );
}

function WhoSees({ d }: { d: PeopleOut }) {
  const groups = groupsOf(d);
  const rows: { key: string; name: string; what: string; person: PersonRow | null }[] = [
    { key: "owner", name: ownerName(d.owner) === "the owner" ? "You" : `You (${d.owner})`, what: "The owner: everything", person: null },
    ...d.people.map((p) => ({ key: `p${p.id}`, name: p.display_name, what: `${ROLE_TITLE[p.role]}${p.active ? "" : " · turned off"}`, person: p })),
  ];
  return (
    <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
      <h2 style={h2}>Who sees which accounts</h2>
      <p style={intro}>Each row is a sign-in; each column is a group of accounts. The groups come from the marks under Whose each account is, below.</p>
      <div style={{ overflowX: "auto" }}>
        <table className="ledger" style={{ minWidth: 120 + groups.length * 130 }}>
          <thead>
            <tr>
              <th style={{ verticalAlign: "bottom" }}>Sign-in</th>
              {groups.map((g) => (
                <th key={g.key} style={{ verticalAlign: "bottom", textTransform: "none", letterSpacing: 0, fontWeight: 600, padding: "0 4px 6px" }}>
                  <div style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: "flex-start" }}>
                    <WhoTag shared={g.shared} word={g.word} />
                    {g.accounts.length === 0
                      ? <span style={{ fontSize: 11, color: "var(--muted)" }}>no account yet</span>
                      : g.accounts.map((a) => <span key={a.id} style={{ fontSize: 11, color: "var(--ink-2)", padding: "1px 6px", borderRadius: 6, background: "var(--surface-2)", overflowWrap: "anywhere" }}>{a.name}</span>)}
                  </div>
                </th>
              ))}
            </tr>
          </thead>
          <tbody>
            {rows.map((r) => (
              <tr key={r.key}>
                <td style={{ padding: "4px 8px 4px 0", borderTop: "1px solid var(--surface-2)" }}>
                  <strong style={{ display: "flex", alignItems: "center", gap: 6 }}>{r.person === null ? <User size={14} aria-hidden="true" /> : <Users size={14} aria-hidden="true" />}{r.name}</strong>
                  <span style={{ fontSize: 11, color: "var(--ink-2)" }}>{r.what}</span>
                </td>
                {groups.map((g) => <Cell key={g.key} sees={reaches(r.person, g.key)} />)}
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      {d.people.length === 0 && <Empty card={false}>Nobody else signs in yet. Add a person below: they get a link and choose their own password.</Empty>}
      <div style={{ fontSize: 12, color: "var(--ink-2)", display: "flex", flexDirection: "column", gap: 2 }}>
        <span><strong>Everything:</strong> {d.roles.everything}</span>
        <span><strong>Shared accounts only:</strong> {d.roles.shared}</span>
      </div>
    </section>
  );
}

// ---- the one-time link -----------------------------------------------------------------------------------------

function CopyLink({ text }: { text: string }) {
  const [said, setSaid] = useState<Note>(null);
  async function copy() {
    try {
      await navigator.clipboard.writeText(text);
      setSaid({ tone: "info", text: "Copied. Paste it into a text or a message to them." });
    } catch {
      setSaid({ tone: "error", text: "This browser would not copy. Select the link and copy it by hand." });
    }
  }
  return (
    <span style={{ display: "inline-flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
      <button type="button" className="btn dark" onClick={copy} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><Copy size={14} aria-hidden="true" />Copy the link</button>
      <Said text={said?.text ?? null} tone={said?.tone} />
    </span>
  );
}

function InviteCard({ invite, name, onHide }: { invite: Invite; name: string; onHide: () => void }) {
  const ref = useRef<HTMLHeadingElement>(null);
  useEffect(() => {
    ref.current?.scrollIntoView({ block: "center", behavior: "smooth" });
    ref.current?.focus();
  }, [invite.link]);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, padding: 14, borderRadius: 12, border: "2px solid var(--paycheck)", background: "var(--paycheck-soft)", color: "var(--ink)" }}>
      <h4 ref={ref} tabIndex={-1} style={{ margin: 0, fontSize: 15, fontWeight: 800, display: "flex", alignItems: "center", gap: 6, outline: "none" }}><Link2 size={16} aria-hidden="true" />{possessive(name)} one-time link</h4>
      <code style={{ fontSize: 17, fontWeight: 700, lineHeight: 1.4, wordBreak: "break-all", padding: "10px 12px", borderRadius: 8, background: "var(--surface)", border: "1px solid var(--hairline)", userSelect: "all" }}>{invite.link}</code>
      <div><CopyLink text={invite.link} /></div>
      <p style={{ margin: 0, fontSize: 13 }}>
        Send this to {name} yourself (a text or a message). It works once, until {when(invite.expires_at)}. They choose their own password; you never see it.
      </p>
      <p style={{ margin: 0, fontSize: 12, color: "var(--ink-2)" }}>This page shows the link only now. If it is lost, make a new link.</p>
      <div><button type="button" className="btn" onClick={onHide} style={SMALL}>Done: hide the link</button></div>
    </div>
  );
}

// ---- each person -----------------------------------------------------------------------------------------------

function standing(p: PersonRow): { status: Status; text: string } {
  if (!p.active) return { status: "warn", text: "Turned off: cannot sign in" };
  if (!p.password_set) {
    return p.invite_open
      ? { status: "warn", text: `Waiting for them to set a password. The link works until ${when(p.invite_expires_at)}.` }
      : { status: "crit", text: "The link ran out before they used it. Make a new link." };
  }
  return { status: "good", text: p.last_sign_in_at ? `Signed in ${when(p.last_sign_in_at)}` : "Password set; not signed in yet" };
}

function RoleChoice({ name, value, roles, onChange }: { name: string; value: PersonRole; roles: PeopleOut["roles"]; onChange: (r: PersonRole) => void }) {
  return (
    <fieldset style={{ border: 0, margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 6 }}>
      <legend className="lab" style={{ padding: 0, marginBottom: 4 }}>What they see</legend>
      {(["shared", "everything"] as PersonRole[]).map((r) => (
        <label key={r} style={{ display: "flex", gap: 8, alignItems: "flex-start", padding: "8px 10px", borderRadius: 10, border: `1px solid ${value === r ? "var(--ink)" : "var(--hairline)"}`, background: "var(--surface)", cursor: "pointer", minHeight: 32 }}>
          <input type="radio" name={name} value={r} checked={value === r} onChange={() => onChange(r)} style={{ marginTop: 3 }} />
          <span style={{ display: "flex", flexDirection: "column", gap: 2 }}>
            <strong style={{ fontSize: 13 }}>{ROLE_TITLE[r]}</strong>
            <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{roles[r]}</span>
          </span>
        </label>
      ))}
    </fieldset>
  );
}

function PersonCard({ p, roles, invite, onChanged, onRemoved, onInvite, onHideInvite }: { p: PersonRow; roles: PeopleOut["roles"]; invite: Invite | null; onChanged: () => void; onRemoved: (text: string) => void; onInvite: (i: Invite) => void; onHideInvite: () => void }) {
  const [editing, setEditing] = useState(false);
  const [name, setName] = useState(p.display_name);
  const [role, setRole] = useState<PersonRole>(p.role);
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);
  const s = standing(p);

  async function put(body: { display_name?: string; role?: PersonRole; active?: boolean }) {
    return api<PeopleOut>(`/api/v1/people/${p.id}`, { method: "PUT", body: JSON.stringify(body) });
  }

  async function save(e: React.FormEvent) {
    e.preventDefault();
    const body: { display_name?: string; role?: PersonRole } = {};
    if (name.trim() && name.trim() !== p.display_name) body.display_name = name.trim();
    if (role !== p.role) body.role = role;
    if (!body.display_name && !body.role) {
      setNote({ tone: "info", text: "Nothing changed." });
      return;
    }
    setBusy(true);
    setNote(null);
    try {
      await put(body);
      const who = body.display_name ?? p.display_name;
      setNote({ tone: "info", text: body.role ? `Kept. ${who} now has ${ROLE_TITLE[body.role]}. They were signed out, and sign in again with the same password.` : `Kept. The app now calls them ${who}.` });
      setEditing(false);
      onChanged();
    } catch (err) {
      setNote({ tone: "error", text: `Couldn't keep it: ${(err as Error).message}` });
    } finally {
      setBusy(false);
    }
  }

  async function turnOn() {
    setBusy(true);
    setNote(null);
    try {
      await put({ active: true });
      setNote({ tone: "info", text: p.password_set ? `Turned on. ${p.display_name} can sign in again with the same password.` : `Turned on. Make a new link so ${p.display_name} can choose a password.` });
      onChanged();
    } catch (err) {
      setNote({ tone: "error", text: `Couldn't turn them on: ${(err as Error).message}` });
    } finally {
      setBusy(false);
    }
  }

  // Asked first by ConfirmButton, which says a refusal beside the button.
  async function turnOff() {
    await put({ active: false });
    setNote({ tone: "info", text: `Turned off. ${p.display_name} is signed out everywhere and cannot sign in.` });
    onChanged();
  }

  async function newLink() {
    const r = await api<PeopleOut>(`/api/v1/people/${p.id}/invite`, { method: "POST" });
    if (r.invite) onInvite(r.invite);
    setNote({ tone: "info", text: `${p.display_name} is signed out, and their password, second step and passkeys are cleared. Send them the new link.` });
    onChanged();
  }

  async function remove() {
    await api(`/api/v1/people/${p.id}`, { method: "DELETE" });
    onRemoved(`Removed ${p.display_name}. They can no longer sign in, and accounts marked ${possessive(p.display_name)} are yours again.`);
  }

  return (
    <article className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
      <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "baseline" }}>
        <h3 style={{ margin: 0, fontSize: 16, fontWeight: 800 }}>{p.display_name}</h3>
        <span style={{ fontSize: 13, color: "var(--ink-2)" }}>signs in as <code style={{ fontWeight: 700 }}>{p.username}</code></span>
        <WhoTag shared={p.role === "shared"} word={ROLE_TITLE[p.role]} />
      </div>
      <div style={{ fontSize: 13, color: "var(--ink-2)" }}>{p.role_words}</div>
      <div style={{ display: "flex", gap: 8, flexWrap: "wrap" }}>
        <StatusPill status={s.status}>{s.text}</StatusPill>
        {p.password_set && <StatusPill status={p.second_step ? "good" : "warn"}>{p.second_step ? "Code from their phone: on" : "Code from their phone: off"}</StatusPill>}
      </div>
      {invite && <InviteCard invite={invite} name={p.display_name} onHide={onHideInvite} />}
      {editing ? (
        <form onSubmit={save} style={{ display: "flex", flexDirection: "column", gap: 10, padding: 12, borderRadius: 10, background: "var(--surface-2)" }}>
          <label style={field}>
            <span className="lab">Their name</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} />
          </label>
          <RoleChoice name={`role-${p.id}`} value={role} roles={roles} onChange={setRole} />
          {role !== p.role && <span style={{ fontSize: 12, color: "var(--warning-ink)", fontWeight: 700 }}>A new role signs them out. They sign in again with the same password.</span>}
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <button className="btn dark" type="submit" disabled={busy} style={{ minHeight: 34, padding: "4px 12px" }}>{busy ? "Keeping…" : "Keep the changes"}</button>
            <button className="btn" type="button" onClick={() => { setEditing(false); setName(p.display_name); setRole(p.role); }} style={{ minHeight: 34, padding: "4px 12px" }}>Cancel</button>
          </div>
        </form>
      ) : (
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "flex-start" }}>
          <Link to={`/shared?person=${p.id}`} className="btn" style={{ ...SMALL, display: "inline-flex", alignItems: "center", gap: 6, textDecoration: "none" }}><Eye size={13} aria-hidden="true" />See what {p.display_name} sees</Link>
          <button type="button" className="btn" onClick={() => { setNote(null); setName(p.display_name); setRole(p.role); setEditing(true); }} style={SMALL}>Change name or role</button>
          {p.active && (
            <ConfirmButton
              label="New link"
              question={`Make a new link for ${p.display_name}? It signs them out and clears their password, second step and passkeys; they choose a new password from the new link. Use it when they forgot their password or lost their phone.`}
              yes="Yes, make a new link"
              no="Keep things as they are"
              onConfirm={newLink}
              style={SMALL}
            />
          )}
          {p.active
            ? <ConfirmButton label="Turn off" question={`Turn off ${p.display_name}? They are signed out now and cannot sign in until you turn them on again. Their password stays.`} yes="Yes, turn them off" no="Keep them on" onConfirm={turnOff} style={SMALL} />
            : <button type="button" className="btn" disabled={busy} onClick={turnOn} style={SMALL}>{busy ? "Turning on…" : "Turn on"}</button>}
          <ConfirmButton label="Remove" name={`Remove ${p.display_name}`} question={`Remove ${p.display_name} for good? They are signed out, their passkeys are deleted, and accounts marked ${possessive(p.display_name)} become yours again.`} yes={`Yes, remove ${p.display_name}`} no="Keep them" onConfirm={remove} style={SMALL} />
        </div>
      )}
      {!p.active && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Turn them on to make a new link.</span>}
      <Said text={note?.text ?? null} tone={note?.tone} />
    </article>
  );
}

// ---- adding a person -------------------------------------------------------------------------------------------

function AddPerson({ d, onAdded }: { d: PeopleOut; onAdded: (invite: Invite | undefined) => void }) {
  const [username, setUsername] = useState("");
  const [name, setName] = useState("");
  const [role, setRole] = useState<PersonRole>("shared");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<Note>(null);

  async function add(e: React.FormEvent) {
    e.preventDefault();
    if (!username.trim()) {
      setNote({ tone: "error", text: "Type a username first: the name they type to sign in." });
      return;
    }
    setBusy(true);
    setNote(null);
    try {
      const r = await api<PeopleOut>("/api/v1/people", { method: "POST", body: JSON.stringify({ username: username.trim().toLowerCase(), display_name: name.trim(), role }) });
      setUsername("");
      setName("");
      setRole("shared");
      onAdded(r.invite);
    } catch (err) {
      setNote({ tone: "error", text: `Couldn't add them: ${(err as Error).message}` });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section aria-labelledby="add-title" className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
      <h2 id="add-title" style={{ ...h2, display: "flex", alignItems: "center", gap: 8 }}><UserPlus size={18} aria-hidden="true" />Add a person</h2>
      <p style={intro}>{d.how}</p>
      <form onSubmit={add} style={{ display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
          <label style={{ ...field, flex: "1 1 200px" }}>
            <span className="lab">Username</span>
            <input className="input" value={username} onChange={(e) => setUsername(e.target.value)} autoCapitalize="off" autoComplete="off" spellCheck={false} maxLength={32} aria-describedby="add-username" />
            <span id="add-username" style={{ fontSize: 12, color: "var(--ink-2)" }}>What they type to sign in: 2 to 32 lower-case letters, digits, dots, dashes or underscores.</span>
          </label>
          <label style={{ ...field, flex: "1 1 200px" }}>
            <span className="lab">Their name</span>
            <input className="input" value={name} onChange={(e) => setName(e.target.value)} maxLength={60} aria-describedby="add-name" />
            <span id="add-name" style={{ fontSize: 12, color: "var(--ink-2)" }}>How the app greets them and labels their accounts.</span>
          </label>
        </div>
        <RoleChoice name="role-new" value={role} roles={d.roles} onChange={setRole} />
        <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
          <button className="btn dark" type="submit" disabled={busy}>{busy ? "Adding…" : "Add them and make their link"}</button>
          <Said text={note?.text ?? null} tone={note?.tone} />
        </div>
      </form>
    </section>
  );
}

// ---- whose each account is -------------------------------------------------------------------------------------

function AccountMarks({ d, onChanged }: { d: PeopleOut; onChanged: () => void }) {
  const [busy, setBusy] = useState<number | null>(null);
  const [notes, setNotes] = useState<Record<number, Note>>({});
  const choices = [
    { key: "owner", word: "Yours", shared: false },
    { key: "shared", word: "Shared", shared: true },
    ...d.people.map((p) => ({ key: `p${p.id}`, word: possessive(p.display_name), shared: false })),
  ];

  async function mark(a: MarkedAccount, key: string, word: string) {
    if (markKey(a) === key) return;
    setBusy(a.id);
    setNotes({ ...notes, [a.id]: null });
    try {
      await api(`/api/v1/accounts/${a.id}/sharing`, { method: "PUT", body: JSON.stringify(keyToMark(key)) });
      setNotes((n) => ({ ...n, [a.id]: { tone: "info", text: `${a.name} is now marked ${word}.` } }));
      onChanged();
    } catch (err) {
      setNotes((n) => ({ ...n, [a.id]: { tone: "error", text: `Couldn't mark it: ${(err as Error).message}` } }));
    } finally {
      setBusy(null);
    }
  }

  return (
    <section id="accounts" aria-labelledby="marks-title" className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10, scrollMarginTop: 12 }}>
      <h2 id="marks-title" style={h2}>Whose each account is</h2>
      <p style={intro}>Shared: everyone you add sees it. Yours: only you, and anyone with everything. A person's: that person sees it as theirs; you and anyone with everything see it too. Each choice is kept as soon as you tap it.</p>
      {d.accounts.length === 0 && <Empty card={false} to="/settings/connectors" action="Connect a bank">No accounts yet.</Empty>}
      <div style={{ display: "flex", flexDirection: "column" }}>
        {d.accounts.map((a) => {
          const now = markKey(a);
          return (
            <div key={a.id} style={{ display: "flex", flexDirection: "column", gap: 6, padding: "10px 0", borderTop: "1px solid var(--surface-2)" }}>
              <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "baseline" }}>
                <strong style={{ fontSize: 14 }}>{a.name}</strong>
                <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{KIND_WORD[a.kind] ?? a.kind}{a.institution ? ` · ${a.institution}` : ""}</span>
              </div>
              <div role="group" aria-label={`Whose is ${a.name}`} style={{ display: "flex", gap: 6, flexWrap: "wrap", alignItems: "center" }}>
                {choices.map((c) => {
                  const on = now === c.key;
                  const Icon = c.shared ? Users : User;
                  return (
                    <button key={c.key} type="button" aria-pressed={on} disabled={busy === a.id} onClick={() => mark(a, c.key, c.word)} className="btn" style={{ ...SMALL, display: "inline-flex", alignItems: "center", gap: 5, background: on ? "var(--ink)" : "var(--surface)", color: on ? "var(--inverse)" : "var(--ink)", borderColor: on ? "var(--ink)" : "var(--hairline)" }}>
                      <Icon size={13} aria-hidden="true" />{c.word}
                    </button>
                  );
                })}
                <Said text={notes[a.id]?.text ?? null} tone={notes[a.id]?.tone} />
              </div>
            </div>
          );
        })}
      </div>
    </section>
  );
}

// ---- the page --------------------------------------------------------------------------------------------------

export function People({ me }: { me: MePerson | null }) {
  const owner = roleOf(me) === "owner";
  const state = useApi<PeopleOut>(owner ? "/api/v1/people" : null);
  const [invite, setInvite] = useState<Invite | null>(null);
  const [removed, setRemoved] = useState<string | null>(null);

  if (!owner) {
    return (
      <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 900 }}>
        <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>People</h1>
        <Empty to="/settings/security" action="Your sign-in">Only {ownerName(me?.owner)} adds, changes or removes people. Your own password, code and passkeys are on Your sign-in.</Empty>
      </div>
    );
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16, maxWidth: 900 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>People</h1>
      <p style={intro}>Someone else can have their own sign-in: with everything, or with the shared accounts only. Each person approves only their own emails, and you only yours.</p>
      <Show state={state} what="the people you added">
        {(d) => {
          const ids = new Set(d.people.map((p) => p.id));
          const shown = invite && ids.has(invite.person_id) ? invite : null;
          return (
            <>
              <WhoSees d={d} />
              {d.people.length > 0 && (
                <section aria-labelledby="people-title" style={{ display: "flex", flexDirection: "column", gap: 10 }}>
                  <h2 id="people-title" style={h2}>The people you added</h2>
                  {d.people.map((p) => (
                    <PersonCard
                      key={p.id}
                      p={p}
                      roles={d.roles}
                      invite={shown && shown.person_id === p.id ? shown : null}
                      onChanged={() => { setRemoved(null); state.reload(); }}
                      onRemoved={(text) => { setRemoved(text); state.reload(); }}
                      onInvite={setInvite}
                      onHideInvite={() => setInvite(null)}
                    />
                  ))}
                </section>
              )}
              <Said text={removed} />
              <AddPerson d={d} onAdded={(i) => { if (i) setInvite(i); state.reload(); }} />
              <AccountMarks d={d} onChanged={state.reload} />
            </>
          );
        }}
      </Show>
    </div>
  );
}
