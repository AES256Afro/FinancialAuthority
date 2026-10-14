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

export interface Window {
  start: string;
  end: string;
  paycheck: number;
  carried_in: number;
  advances: { total: number; items: Event[] };
  bills: { total: number; items: Event[] };
  set_aside: { total: number; items: Event[] };
  buffer: number;
  safe: number;
}

export interface CalendarWeek {
  start: string;
  end: string;
  low: number | null;
  low_date: string | null;
}

export interface Snapshot {
  version: string;
  as_of: string;
  windows: Window[];
  calendar: { events: Event[]; weeks: CalendarWeek[] };
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

export interface SeriesRow {
  id: number;
  merchant: string;
  category: string;
  cadence: string;
  typical_amount: number;
  latest_amount: number;
  charges_seen: number;
  first_seen: string;
  last_date: string;
  next_expected: string;
  active: boolean;
  kind: string;
  confirmed: boolean;
}

export interface BillRow {
  id: number;
  name: string;
  kind: string;
  amount: number;
  amount_mode: string;
  cadence: string;
  due_day: number;
  autopay: boolean;
  pay_from_account_id: number | null;
  grace_days: number;
  late_fee: number;
  movable: boolean;
  move_window: string;
  category: string;
  series_id: number | null;
  active: boolean;
}

export interface Me {
  kind: string;
  name: string;
  scopes: string[];
  auth_enabled: boolean;
}
