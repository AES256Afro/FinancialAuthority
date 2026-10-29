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
  planned?: boolean;
  via_card?: boolean;
  from_kind?: "card" | "cash";
  parts?: { balance: number; bills: number; spend: number };
  kept?: number;
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
  message: { id: number; sender: string; sender_name: string; subject: string; date: string | null; trust?: string; trust_why?: string } | null;
  /** A bill notice from someone the app already counts another way: where (Housing, Debts, Borrowing). */
  counted_elsewhere?: string | null;
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
  deductions_grouped: { name: string; amount: number; group: "tax" | "benefit" }[];
  gross_total: number;
  taxes_total: number;
  benefits_total: number;
  net_share: number | null;
  verdict?: { status: "good" | "warn"; short: boolean; word: string };
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
  inflow: number;
  advances: { total: number; items: Event[] };
  bills: {
    total: number;
    items: Event[];
    card_parts: { balance: number; bills: number; spend: number };
    other_total: number;
    small: { total: number; count: number; names: string[]; all_funded: boolean; under: number };
    after_eight: { total: number; count: number };
  };
  set_aside: { total: number; items: Event[] };
  buffer: number;
  out: number;
  spoken_for: number;
  safe: number;
}

export interface CalendarWeek {
  start: string;
  end: string;
  low: number | null;
  low_date: string | null;
}

export interface Snapshot {
  why?: Record<string, string>;
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
  on_card?: { total: number; items: Event[]; paid_on: string | null };
  card_payments?: Event[];
  caveats?: string[];
  due_before_payday: {
    total: number;
    items: Event[];
    groups?: { key: string; label: string; total: number; count: number; names: string[] }[];
    next_check?: { start: string; total: number; position: string; top: { name: string; amount: number; date: string }[] };
  };
  advances: { total: number; items: Event[] };
  window: { start: string; end: string; total: number; items: Event[] };
  set_aside: { total: number; items: Event[] };
  safe_to_spend: {
    amount: number; after_goals: number; share_of_cash?: number; through: string; version: string;
    parts?: { cash: number; due_before_payday: number; advances: number; advances_on_payday: number; buffer: number; next_window_shortfall: number };
    steps?: { label: string; amount: number; note?: string; as_of?: string | null; items?: { name: string; amount: number; date: string; source?: string }[] }[];
    by_account?: { id: number; name: string; kind: string; balance: number; as_of: string | null; from_bank: boolean; paycheck_lands: boolean; due: number; buffer: number; advances: number; shortfall: number; goes_out: number; safe: number; items: { name: string; amount: number; date: string }[] }[];
    left_out?: { name: string; verb?: string; amount: number; why: string; owes?: boolean }[];
  };
  borrowing?: { fees_90d: number; fees_month: number; advances_90d: number; outstanding_total: number; outstanding: number };
  goals: { per_paycheck: number; items: { id: number; name: string; kind: string; target: number; funded: number; remaining: number; target_date: string | null; per_paycheck: number; on_track: boolean }[] };
  usual_pace: { per_day: number; per_window: number; basis_days: number; transactions: number; over_safe: number };
  board: { problems: { total: number; items: PaymentRow[] }; due_7: { total: number; items: Event[] }; later: { total: number; items: Event[] }; paid: { total: number; items: PaymentRow[] } };
  debts: { id: number; name: string; kind: string; balance: number; apr: number | null; minimum: number | null; due_day: number | null; source?: string }[];
  pending: { total: number; confirmations: number; series: number; facts: number; pairs?: number; approvals: number; past_due: number };
  /** Everything owed (cards, loans, pay-later plans and advance apps, each once): the Debts page's own total. */
  debts_total: number;
  /** What debts_total counts, in the engine's words. */
  debts_words?: string;
  debts_cards_loans_total?: number;
  fresh?: { balances_as_of: string | null; accounts: { id: number; name: string; kind: string; as_of: string | null; connector: string; balance?: number | null; told?: { amount: number; at: string; bank?: number | null } }[]; newest_transaction: string | null };
  in_flight?: { pending: { total: number; items: { id: number; date: string; merchant: string; amount: number; account: string; source: string }[] }; mail: { total: number; counted: boolean; items: { amount: number; merchant: string; last4: string; date: string; source: string }[] } };
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

// M22: a budget for each paycheck (GET /api/v1/budgets; PUT and DELETE /api/v1/budgets/{category} answer the same).
export interface BudgetItem {
  category: string;
  per_paycheck: number;
  carried: number;
  allowed: number;
  spent: number;
  left: number;
  share_used: number | null;
  expected_by_now: number;
  status: "good" | "warn" | "crit";
  word: string;
  rollover: "next" | "none";
  suggested: number;
  source: string;
}

export interface BudgetPlan {
  as_of: string;
  span: { start: string; end: string; next_payday: string; known: boolean; days_total: number; days_gone: number; days_left: number };
  items: BudgetItem[];
  totals: { allowed: number; spent: number; left: number; left_unspent: number };
  unbudgeted: { spent: number; top: { category: string; spent: number }[] };
  suggestions: { category: string; per_paycheck: number }[];
  safe_to_spend: number;
  fits: boolean;
  fit_note: string | null;
  not_advice: string;
}

// M20: money that moved between the owner's own accounts, matched (GET /api/v1/pairs, POST /api/v1/pairs/{id}).
export interface PairSide {
  id: number;
  date: string;
  merchant: string;
  amount: number;
  account: string;
  source: string;
}

export interface PairRow {
  id: number;
  kind: "transfer" | "card_payment" | "duplicate";
  title: string;
  status: "auto" | "pending" | "confirmed" | "rejected";
  confidence: number;
  reason: string;
  out: PairSide | null;
  in: PairSide | null;
  decided_at: string | null;
  source: string;
}

export interface PairsOut {
  waiting: PairRow[];
  matched: PairRow[];
  counts: { waiting: number; matched: number };
}

// M34: what the app noticed in your transactions (GET /api/v1/watch; POST /api/v1/watch/{key}/expected answers the same).
export interface WatchRow {
  id: number;
  date: string;
  merchant: string;
  amount: number;
  account: string;
  pending: boolean;
  source: string;
}

export interface WatchFinding {
  key: string;
  kind: "unusual" | "double" | "new" | "rise";
  severity: "warning" | "info";
  title: string;
  text: string;
  amount: number;
  rows: WatchRow[];
  link: string;
  source?: string;
}

export interface WatchOut {
  as_of: string;
  items: WatchFinding[];
  count: number;
  not_advice: string;
}

// M23: each paycheck's plan beside what happened, and the week (GET /api/v1/recap).
export interface RecapPlan {
  start: string;
  end: string;
  paycheck: number;
  bills: number;
  advances: number;
  set_aside: number;
  free: number;
  pace_per_day: number;
  recorded: string;
}

export interface RecapWindow {
  plan: RecapPlan;
  came_in: number;
  bills_paid: number;
  everyday: number;
  everyday_against_free: number;
  status: "good" | "warn" | "crit";
  word: string;
  paycheck_word: string;
  top: { category: string; spent: number }[];
  finished: boolean;
}

export interface RecapOut {
  as_of: string;
  windows: RecapWindow[];
  pace: { now: number; then: number | null; change: number | null };
  note: string;
  week: { from: string; to: string; spent: number; usual_week: number; top: { category: string; spent: number }[]; text: string };
}

// M24: bills that come a few times a year, a little from each paycheck (GET /api/v1/goals/sinking; POST
// /api/v1/goals/sinking/{bill_id} answers the same with the goal made).
export interface SinkingFund {
  bill_id: number;
  name: string;
  cadence: string;
  amount: number;
  due: string;
  paychecks_left: number;
  per_paycheck: number;
  goal_name: string;
  goal_id: number | null;
  funded: number;
  ready_in_time: boolean;
  source: string;
}

export interface SinkingOut {
  as_of: string;
  items: SinkingFund[];
  per_paycheck_total: number;
  note: string;
}

// M25: paying debts off with dates, advance apps by cost, money between people (GET /api/v1/debts/payoff?extra=N).
export interface PayoffDebt {
  name: string;
  balance: number;
  apr: number;
  minimum: number;
  source: string;
  minimum_assumed: boolean;
}

export interface PayoffWay {
  order: "avalanche" | "snowball" | "minimums";
  months: number | null;
  debt_free: string | null;
  interest: number | null; // null when the way never finishes: the balance grows
  grows?: boolean;
  interest_saved?: number | null;
  debts: { name: string; source: string; paid_on: string | null; interest: number | null }[];
}

export interface AdvanceCost {
  provider: string;
  advances: number;
  taken: number;
  fees: number;
  fee_per_100: number;
  days_held: number;
  as_yearly_rate: number;
}

export interface PersonLoan {
  person: string;
  direction: "lent" | "borrowed";
  amount: number;
  on: string;
  note: string;
  settled: boolean;
}

export interface PayoffOut {
  as_of: string;
  extra: number;
  debts: PayoffDebt[];
  minimums_only: PayoffWay;
  avalanche: PayoffWay;
  snowball: PayoffWay;
  cheapest: "avalanche" | "snowball";
  minimums_total: number;
  advances: AdvanceCost[];
  people: PersonLoan[];
  note: string;
  not_advice: string;
}

// M37, M38: housing, utilities and documents (GET /api/v1/housing, /api/v1/utilities, /api/v1/documents).
export type HomeState = "current" | "upcoming" | "ended";

export interface HomeNotice {
  by: string;
  given_on: string | null;
  status: "good" | "warn" | "crit";
  word: string;
  days_left?: number;
}

export interface HomeCharge {
  id: number;
  name: string;
  kind: string;
  amount: number | null;
  estimate: boolean;
  pays_to: "home" | "other";
  payee: string;
  due_day: number | null;
  starts_on: string | null;
  ends_on: string | null;
  note: string;
  known: boolean;
  bill_id: number | null;
}

export interface CostPart {
  name: string;
  amount: number;
  refundable: boolean;
}

export interface HomeCost {
  id: number;
  name: string;
  amount: number | null;
  due_on: string | null;
  refundable: boolean;
  parts: CostPart[];
  paid: boolean;
  known: boolean;
  note: string;
  bill_id: number | null;
  counted: boolean;
}

export interface UtilityRow {
  id: number;
  home_id: number | null;
  service: string;
  what: string;
  provider: string;
  billed: string;
  billed_word: string;
  billing_company: string;
  account_last4: "set" | "";
  amount: number | null;
  estimate: boolean;
  due_day: number | null;
  starts_on: string | null;
  ends_on: string | null;
  moves_with_you: boolean;
  bill_id: number | null;
  note: string;
  known: boolean;
}

export interface HomeDocument {
  id: number;
  title: string;
  kind: string;
  pages: number;
  uploaded_at: string;
}

export interface HomeRow {
  id: number;
  name: string;
  kind: "rent" | "mortgage" | "other";
  payee: string;
  state: HomeState;
  starts_on: string | null;
  ends_on: string | null;
  first_due: string | null;
  /** The first regular payment as typed; null when it is the move-in day. */
  first_due_entered: string | null;
  notice_given_on: string | null;
  /** The lease term's end as the lease states it; the rent goes on after it unless you move out. */
  lease_ends_on: string | null;
  lease_word: string | null;
  next_due: string | null;
  due_day: number;
  grace_days: number;
  late_fee: number;
  autopay: boolean;
  notice_days: number | null;
  notice: HomeNotice | null;
  deposit: number;
  deposit_back_on: string | null;
  pay_from_account_id: number | null;
  note: string;
  charges: HomeCharge[];
  to_landlord_monthly: number;
  own_bills_monthly: number;
  monthly_total: number;
  missing: string[];
  costs: HomeCost[];
  costs_total: number;
  costs_missing: string[];
  costs_paid_before: number;
  /** Costs with an amount and no date: shown, not counted in the plan until they have the day they are paid. */
  costs_undated: string[];
  /** What the plan counts of the costs to get in: those with an amount and a date, not yet paid. */
  costs_counted: number;
  refundable_in_costs: number;
  utilities: UtilityRow[];
  documents: HomeDocument[];
  bill_id: number | null;
  source: string;
}

export interface HousingMove {
  from: string;
  to: string;
  on: string | null;
  monthly_change: number;
  costs_total: number;
  costs_missing: string[];
  old_ends_on: string | null;
  old_notice: HomeNotice | null;
  /** The next home's first unpaid cost with a date: the move-in check, usually. */
  first_cost: { name: string; on: string; amount: number | null; source: string } | null;
}

export interface HousingOut {
  as_of: string;
  homes: HomeRow[];
  current_id: number | null;
  upcoming_id: number | null;
  move: HousingMove | null;
  utilities_unassigned: UtilityRow[];
  rent_bills_to_adopt: { id: number; name: string; amount: number; due_day: number }[];
  /** The engine's sentences about a rent that may be counted twice or may not stop at the move. */
  warnings?: string[];
  /** What a change did, in words (an add or a change of a home may end or move another). */
  said?: string | null;
  not_advice: string;
}

export interface UtilitiesOut {
  utilities: UtilityRow[];
  services: string[];
  billed: Record<string, string>;
  /** Bills already on the list that belong to no home or utility: a utility can take one over. */
  bills_to_link: { id: number; name: string; amount: number; due_day: number; category: string }[];
}

export interface MovePathPoint {
  date: string;
  available: number;
  at_pace: number;
  cash: number;
}

export interface MoveCheckOut {
  home: HomeRow;
  move_on: string;
  low: MovePathPoint | null;
  holds: boolean | null;
  status: "good" | "crit" | "unknown";
  word: string;
  path: MovePathPoint[];
  caveats: string[];
}

export interface DocumentRow {
  id: number;
  title: string;
  kind: string;
  attach_to: string;
  filename: string;
  content_type: string;
  size: number;
  pages: number;
  has_text: boolean;
  note: string;
  uploaded_at: string;
  source: string;
  note_text?: string;
}

export interface DocumentsOut {
  documents: DocumentRow[];
  kinds: string[];
}

export interface DocumentHit {
  document_id: number;
  title: string;
  kind: string;
  attach_to: string;
  page: number;
  score: number;
  snippet: string;
}

export interface DocumentPage extends DocumentRow {
  page: number;
  of: number;
  text: string;
}

/** A document_terms fact's fields: what a lease or charge sheet states, each with its page and the words quoted. */
export interface DocumentTermsFields {
  document_id: number;
  title: string;
  attach_to: string;
  terms: Partial<Record<"rent" | "pet_rent" | "deposit" | "late_fee" | "notice_days" | "due_day" | "grace_days" | "ends_on", { value: number | string; page: number; quote: string }>>;
}
