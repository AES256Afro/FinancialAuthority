// The engine's results, as the API returns them. Views render these and compute nothing.

export interface AccountRow {
  id: number;
  name: string;
  institution: string;
  kind: string;
  balance: number | null;
  balance_at: string | null;
  connector: string;
}

export interface Event {
  date: string;
  name: string;
  amount: number;
  kind: string;
  autopay: boolean;
  source: string;
}

export interface Snapshot {
  version: string;
  as_of: string;
  accounts: AccountRow[];
  cash: number;
  buffer: number;
  next_paycheck: { date: string; days: number; amount: number; source: string | null };
  due_before_payday: { total: number; items: Event[] };
  advances: { total: number; items: Event[] };
  window: { start: string; end: string; total: number; items: Event[] };
  set_aside: { total: number; items: Event[] };
  safe_to_spend: { amount: number; through: string; version: string };
  usual_pace: { per_day: number; per_window: number; basis_days: number; transactions: number };
  debts: { id: number; name: string; kind: string; balance: number; apr: number; minimum: number; due_day: number | null }[];
  pending: { confirmations: number; approvals: number };
  projection: { date: string; available: number; at_pace: number; cash: number }[];
  not_advice: string;
}

export interface TransactionRow {
  id: number;
  date: string;
  merchant: string;
  amount: number;
  category: string;
  account: string;
  institution: string;
  description: string;
  note: string;
}

export interface Me {
  kind: string;
  name: string;
  scopes: string[];
  auth_enabled: boolean;
}
