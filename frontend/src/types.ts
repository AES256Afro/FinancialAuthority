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
  line?: "housing" | "utilities" | "debt";
  funded?: boolean;
  share?: number;
  bill_id?: number;
  paid_from?: string;
  pending?: boolean;
}

export interface PaymentRow {
  bill_id: number;
  name: string;
  kind: string;
  due_date: string;
  amount: number;
  status: "paid" | "late" | "past_due" | "scheduled";
  paid_date: string | null;
  evidence: string;
  autopay: boolean;
  source: string;
}

export interface MailAccountRow {
  id: number;
  label: string;
  kind: "proton" | "gmail" | "imap";
  host: string;
  port: number;
  username: string;
  use_ssl: boolean;
  starttls: boolean;
  folders: string;
  active: boolean;
  last_sync_at: string | null;
  last_error: string;
  messages_seen: number;
  has_password: boolean;
}

export interface MessageRow {
  id: number;
  account: string;
  folder: string;
  uid: number;
  sender: string;
  sender_name: string;
  subject: string;
  date: string | null;
  has_attachments: boolean;
  attachment_names: string;
  kind: string;
  snippet: string;
}

export interface FactRow {
  id: number;
  kind: string;
  fields: Record<string, string | number | undefined>;
  confidence: number;
  status: string;
  excerpt: string;
  produced_by: string;
  applied_to: string;
  created_at: string;
  message: { id: number; sender: string; sender_name: string; subject: string; date: string | null } | null;
}

export interface PaystubRow {
  id: number;
  employer: string;
  provider: string;
  pay_date: string;
  period_start: string | null;
  period_end: string | null;
  gross: number | null;
  net: number;
  hours: number | null;
  deductions: { name: string; amount: number }[];
  source: string;
  transaction_id: number | null;
  matched: boolean;
  expected_net: number | null;
  difference: number | null;
}

export interface BriefItem {
  severity: "info" | "warning" | "critical";
  text: string;
  link: string;
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
  payments: { past_due: PaymentRow[]; late: PaymentRow[]; paid: PaymentRow[]; scheduled: PaymentRow[] };
  paystubs: { recent: PaystubRow[]; last: PaystubRow | null; short: PaystubRow[] };
  brief: BriefItem[];
  accounts: AccountRow[];
  cash: number;
  buffer: number;
  next_paycheck: { date: string; days: number; amount: number; source: string | null; known?: boolean; cadence?: string };
  paydays?: { date: string; amount: number }[];
  cash_all?: number;
  savings?: number;
  caveats?: string[];
  due_before_payday: { total: number; items: Event[] };
  advances: { total: number; items: Event[] };
  window: { start: string; end: string; total: number; items: Event[] };
  set_aside: { total: number; items: Event[] };
  safe_to_spend: { amount: number; after_goals: number; through: string; version: string };
  goals: { per_paycheck: number; items: { id: number; name: string; kind: string; target: number; funded: number; remaining: number; target_date: string | null; per_paycheck: number; on_track: boolean }[] };
  usual_pace: { per_day: number; per_window: number; basis_days: number; transactions: number };
  debts: { id: number; name: string; kind: string; balance: number; apr: number; minimum: number; due_day: number | null }[];
  pending: { confirmations: number; series: number; facts: number; approvals: number; past_due: number };
  mail: { pending: { id: number; kind: string; sender_name: string; subject: string; date: string | null }[]; alerts: { id: number; kind: string; fields: Record<string, string | number>; sender_name: string; date: string | null }[] };
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
  suggested_kind: string;
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
  paid_from: string;
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

export interface ReviewNote {
  n: number;
  kind: "disagree" | "tighten" | "ask" | "ok";
  text: string;
  action: { type: "rule" | "buffer" | "ask" | "none"; text?: string; value?: number };
  status: "open" | "applied" | "dismissed";
  unverified: string[];
}

export interface ReviewRun {
  id: number;
  created_at: string;
  trigger: "payday" | "change" | "request";
  provider: string;
  model: string;
  ok: boolean;
  error: string;
  notes: ReviewNote[];
  input_tokens: number;
  output_tokens: number;
  cost: number | null;
}
