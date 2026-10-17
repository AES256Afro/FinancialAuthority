// The business and the tax year (M56 to M60): the shapes the server sends. Nothing here computes a figure; every
// amount, count and date is the engine's, from backend/app/engine/business.py and taxcenter.py.

/** Where a rule comes from: an official page, and the day the app last checked it. */
export interface Source {
  title: string;
  url: string;
  /** YYYY-MM-DD */
  checked: string;
}

export type BusinessKind = "sole_prop" | "llc";

export interface Business {
  id: number;
  name: string;
  kind: BusinessKind;
  state: string;
  started_on: string | null;
  what_it_does: string;
  /** Only whether an EIN exists; the number itself is never asked for or kept. */
  ein_on_file: boolean;
  assumed_name: boolean;
  account_id: number | null;
  home_office_sqft: number | null;
  active: boolean;
}

/** POST /api/v1/business and PUT /api/v1/business/{id}. */
export interface BusinessIn {
  name: string;
  kind: BusinessKind;
  state: string;
  started_on: string | null;
  what_it_does: string;
  ein_on_file: boolean;
  assumed_name: boolean;
  account_id: number | null;
  home_office_sqft: number | null;
  active?: boolean;
}

/** One row of "sole proprietor beside an LLC". */
export interface CompareRow {
  what: string;
  sole_prop: string;
  llc: string;
}

export interface SetupStep {
  id: string;
  title: string;
  for: "both" | "llc";
  how: string;
  done: boolean;
  source: Source;
}

export interface Guide {
  compare: CompareRow[];
  steps: SetupStep[];
  done: number;
  total: number;
  said: string;
  bare_minimum: string;
  not_advice: string;
}

/** A line of Schedule C a cost goes on. `source` here is a key, not a link (the finder groups carry the link). */
export interface TaxLine {
  key: string;
  line: string;
  words: string;
  examples: string;
  source?: string;
}

/** GET /api/v1/business (and what every change to a business or a setup step returns). */
export interface BusinessOverview {
  businesses: Business[];
  guide: Guide;
  lines: TaxLine[];
}

export interface BooksLine {
  key: string;
  line: string;
  words: string;
  amount: number;
  count: number;
  sources: string[];
  rule?: Source;
}

export interface Payer {
  payer: string;
  paid: number;
  expect_1099: boolean;
}

/** GET /api/v1/business/{id}/books?year= */
export interface Books {
  year: number;
  business: string;
  income: number;
  costs: number;
  profit: number;
  lines: BooksLine[];
  miles: number;
  trips: number;
  se_tax: number;
  se_note: string;
  payers: Payer[];
  form_1099_threshold: number;
  sources: Source[];
  not_advice: string;
}

export interface FinderItem {
  id: number;
  date: string;
  merchant: string;
  amount: number;
  receipt: boolean;
  source: string;
}

export interface FinderGroup {
  /** Also the tax line a purchase in this group is marked on. */
  key: string;
  words: string;
  line: string;
  examples: string;
  count: number;
  total: number;
  by_year: { year: number; total: number }[];
  items: FinderItem[];
  receipts: number;
  question: string;
  rule: string;
  source: Source;
}

export interface NeverItem {
  what: string;
  why: string;
  source: Source;
}

/** GET /api/v1/business/finder?years= (POST /finder/answer returns the same, with `said` saying what was done). */
export interface Finder {
  since: string;
  groups: FinderGroup[];
  total: number;
  this_year: number;
  se_saving_this_year: number;
  equipment: { count: number; cost: number; words: string; source: Source } | null;
  startup: { limit: number; phaseout: number; words: string; source: Source };
  never: NeverItem[];
  said: string;
  past_years: string;
  not_advice: string;
}

export interface FinderAnswerIn {
  ids: number[];
  answer: "business" | "personal";
  business_id: number | null;
  tax_line: string;
  share: number;
}

/** POST /api/v1/business/mark */
export interface MarkIn {
  ids: number[];
  business_id: number | null;
  share: number;
  tax_line: string;
}

export interface InvoiceLine {
  what: string;
  quantity: number;
  price: number;
}

export type InvoiceStatus = "draft" | "sent" | "paid" | "void";

/** A deposit (for an invoice sent) or a payment (for one received) of the same amount, from its date on. */
export interface InvoiceCandidate {
  id: number;
  date: string;
  merchant: string;
  amount: number;
}

export interface Invoice {
  id: number;
  business_id: number;
  direction: "out" | "in";
  number: string;
  counterparty: string;
  issued_on: string;
  due_on: string | null;
  lines: InvoiceLine[];
  amount: number;
  status: InvoiceStatus;
  paid_on: string | null;
  transaction_id: number | null;
  tax_line: string;
  note: string;
  source: string;
  candidates?: InvoiceCandidate[];
}

/** GET /api/v1/business/{id}/invoices */
export interface InvoicesOut {
  invoices: Invoice[];
  owed_to_you: number;
  unpaid: number;
}

/** POST /api/v1/business/{id}/invoices and PUT /api/v1/business/invoices/{inv}. Paid is set by matching a payment. */
export interface InvoiceIn {
  direction: "out" | "in";
  counterparty: string;
  issued_on: string;
  due_on: string | null;
  lines: InvoiceLine[];
  status: "draft" | "sent" | "void";
  tax_line: string;
  note: string;
  number: string;
}

export interface Trip {
  id: number;
  day: string;
  miles: number;
  purpose: string;
  start: string;
  end: string;
  /** Cents a mile on the day driven; null for a day the rate table does not cover yet. */
  cents: number | null;
  amount: number;
  source: string;
}

/** GET /api/v1/business/{id}/trips?year= (adding or removing a trip returns the same for its year). */
export interface TripsOut {
  year: number;
  trips: Trip[];
  miles: number;
  amount: number;
  rates: { from: string; to: string; cents: number }[];
  never: string;
  source: Source;
  not_advice: string;
}

export interface TripIn {
  day: string;
  miles: number;
  purpose: string;
  start: string;
  end: string;
}

export interface TaxDate {
  due: string;
  what: string;
}

export interface TaxQuestion {
  key: string;
  text: string;
  answer: boolean | null;
}

export interface TaxCheck {
  key: string;
  title: string;
  state: "ask" | "likely" | "no";
  words: string;
  questions: TaxQuestion[];
  loans_seen: string[];
  source: Source;
}

export type FilingStatus = "single" | "married_joint" | "head_of_household";

/** A spending category a return usually asks about, with its total for the year. */
export interface TaxCategory {
  name: string;
  total: number;
  count: number;
  share: number;
  months: number[];
  /** Whether Schedule A can take it (medical only past 7.5% of income); the engine says, the screen marks it. */
  itemizable?: boolean;
}

/** GET /api/v1/taxes and /api/v1/taxes/{year} (POST /answers returns this year's). */
export interface TaxYear {
  year: number;
  filing_status: FilingStatus;
  dates: TaxDate[];
  upcoming: TaxDate[];
  businesses: Books[];
  profit: number;
  se_tax: number;
  standard_deduction: number | null;
  itemizable: number;
  itemizable_categories: TaxCategory[];
  checks: TaxCheck[];
  never: NeverItem[];
  amend: { years: number[]; words: string; source: Source };
  said: string;
  sources: Source[];
  not_advice: string;
}
