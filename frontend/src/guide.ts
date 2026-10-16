// The shapes the Cover, Saving and guide endpoints return. Every figure is the engine's.

export type Status = "good" | "warn" | "crit";

export interface CoverItem {
  name: string;
  amount: number;
  date: string;
  kind?: string;
  source: string;
}

export interface CoverWindow {
  start: string;
  end: string;
  has: number;
  inflow: number;
  has_total: number;
  needs: number;
  bills: number;
  everyday: number;
  buffer: number;
  left: number;
  low: number;
  low_on: string;
  items: CoverItem[];
}

export interface Rung {
  n: number;
  name: string;
  target: number;
  why: string;
  reached: boolean;
  short: number;
  progress: number;
  eta: string | null;
  eta_without_rollover: string | null;
}

export interface CoverAccount {
  id: number;
  name: string;
  institution: string;
  kind: string;
  balance: number | null;
  source: string;
  role: "spend" | "card" | "save" | "other";
  status: Status;
  headline: string;
  deposit?: boolean;
  pace_per_day?: number;
  windows?: CoverWindow[];
  limit?: number | null;
  utilisation?: number | null;
  statement_on?: string | null;
  statement_balance?: number | null;
  statement_utilisation?: number | null;
  to_target?: number;
  pay_by?: string | null;
  planned?: number;
  riding?: CoverItem[];
  payments?: { date: string; amount: number; parts: { balance?: number; bills?: number; spend?: number }; source: string }[];
  per_window?: [number, number];
  target_amount?: number | null;
  rung?: Rung | null;
  short?: number;
}

export interface SheetRow {
  id: string;
  kind: "short" | "pay" | "keep" | "move";
  date: string;
  action: string;
  amount: number;
  from: string | null;
  to: string;
  so_that: string;
  source: string;
  done: boolean;
}

export interface Cover {
  as_of: string;
  paydays: [string, string];
  buffer: number;
  deposit_account_id: number | null;
  accounts: CoverAccount[];
  sheet: SheetRow[];
  counts: Record<Status, number>;
  not_advice: string;
}

export interface Saving {
  as_of: string;
  balance: number;
  accounts: { id: number; name: string; balance: number | null; source: string }[];
  share: number | null;
  chosen: boolean;
  suggested: number;
  share_used: number;
  presets: Record<string, number>;
  starter: number;
  per_check: { date: string; left: number; keep: number }[];
  rungs: Rung[];
  next_rung: Rung | null;
  by_share: { share: number; per_check: number; etas: (string | null)[] }[];
  month: number;
  must_pays: number;
  rollover: { enabled: boolean; items: { id: number; name: string; installment: number; per_check: number; last_payment: string; source: string }[]; freed_per_check: number };
  basis: string;
  not_advice: string;
}

export interface Thing {
  text: string;
  why: string;
  link: string;
  source: string;
  amount: number | null;
  date: string;
}

export interface Streak {
  key: string;
  label: string;
  value: number;
  unit: string;
  since?: string;
  good: boolean;
  source: string;
}

export interface TodayThing {
  as_of: string;
  thing: Thing;
  status: Status;
  others: Thing[];
  streaks: Streak[];
  not_advice: string;
}

export interface CheckIn {
  as_of: string;
  week: string;
  done: boolean;
  answers: { flagged?: number[]; subscription?: string | null; card_paid?: boolean | null; saved?: boolean | null; note?: string };
  unexpected: { id: number; date: string; merchant: string; amount: number; category: string; source: string }[];
  subscription: { id: number; name: string; amount: number; yearly: number; source: string } | null;
  cards: { id: number; name: string; statement_on: string; statement_balance: number | null; statement_utilisation: number | null; pay_by: string | null; to_target: number | null; status: Status }[];
  saved_14d: number;
  coming: { name: string; amount: number; date: string; source: string }[];
  line: string;
  not_advice: string;
}

export interface FirstRun {
  steps: { id: string; title: string; text: string; done: boolean; link: string }[];
  done: number;
  total: number;
  dismissed: boolean;
  show: boolean;
}
