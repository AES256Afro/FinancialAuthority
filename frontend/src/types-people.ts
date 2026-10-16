// A second person (M33): who signs in, their role, the accounts marked shared or whose, and the shared page.
// The shapes the server sends; nothing here computes a figure.
import type { Me } from "./types";

export type Role = "owner" | "everything" | "shared";

/** GET /api/auth/me, with what M33 added. An older server (or the demo) leaves the new fields out. */
export interface MePerson extends Me {
  person_id?: number | null;
  role?: Role | "";
  owner?: string;
}

/** Who is signed in: the owner, a person with everything, or a person who sees the shared accounts only. */
export function roleOf(me: MePerson | null | undefined): Role {
  if (me?.role === "everything" || me?.role === "shared") return me.role;
  return "owner";
}

export interface PersonRow {
  id: number;
  username: string;
  display_name: string;
  role: "everything" | "shared";
  role_words: string;
  active: boolean;
  password_set: boolean;
  second_step: boolean;
  invite_open: boolean;
  invite_expires_at: string | null;
  created_at: string;
  last_sign_in_at: string | null;
  source: string;
}

export interface MarkedAccount {
  id: number;
  name: string;
  institution: string;
  kind: string;
  shared: boolean;
  person_id: number | null;
  source: string;
}

export interface Invite {
  person_id: number;
  link: string;
  expires_at: string;
}

export interface PeopleOut {
  people: PersonRow[];
  roles: { everything: string; shared: string };
  owner: string;
  accounts: MarkedAccount[];
  how: string;
  invite?: Invite;
}

export interface JoinCheck {
  username: string;
  display_name: string;
  role: "everything" | "shared";
  role_words: string;
  expires_at: string | null;
  min_length: number;
}

export interface SharedAccount {
  id: number;
  name: string;
  institution: string;
  kind: string;
  balance: number | null;
  balance_at: string | null;
  shared: boolean;
  whose: "shared" | "theirs";
  source: string;
}

export interface SharedBill {
  bill_id: number;
  name: string;
  amount: number;
  date: string;
  autopay: boolean;
  account: string;
  kind: string;
  source: string;
}

export interface SharedTx {
  id: number;
  date: string;
  merchant: string;
  amount: number;
  category: string | null;
  account: string;
  pending: boolean;
  source: string;
}

export interface SharedOut {
  as_of: string;
  person: { id: number; name: string; role: string } | null;
  accounts: SharedAccount[];
  cash: number;
  cards_owed: number;
  bills: SharedBill[];
  bills_total: number;
  bills_until: string;
  spending: { category: string; spent: number }[];
  spent_30d: number;
  recent: SharedTx[];
  note: string | null;
  not_advice: string;
}

/** Whose an account is, as a mark: shared, the owner's (unmarked), or a person's. */
export type Mark = { shared: true; person_id: null } | { shared: false; person_id: number | null };

/** The mark as one key, for a radio group: "shared", "owner", or "p<id>". */
export function markKey(a: { shared: boolean; person_id: number | null }): string {
  if (a.shared) return "shared";
  return a.person_id === null ? "owner" : `p${a.person_id}`;
}

export function keyToMark(key: string): Mark {
  if (key === "shared") return { shared: true, person_id: null };
  if (key.startsWith("p")) return { shared: false, person_id: Number(key.slice(1)) };
  return { shared: false, person_id: null };
}

/** "Sam's", or "Chris'" for a name ending in s. */
export function possessive(name: string): string {
  return /s$/i.test(name) ? `${name}'` : `${name}'s`;
}
