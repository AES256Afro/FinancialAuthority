// The shape GET /api/v1/paychecks returns, and the colour of each part. The parts come in a fixed order and
// keep their colour wherever they appear, whatever is filtered: colour follows the part, never its rank.
import { palette } from "./looks";

export type PartKey = "housing" | "bills" | "card" | "later" | "aside" | "saving" | "yours";

export interface PartItem {
  name: string;
  amount: number;
  date: string;
  source: string;
  kind: string;
}

export interface Part {
  key: PartKey;
  label: string;
  amount: number;
  items: PartItem[];
  riding?: PartItem[];
  everyday?: number;
  kept?: number;
  share_of_check?: number | null;
}

export interface Check {
  start: string;
  end: string;
  paycheck: number;
  carried_in: number;
  borrowed?: number;
  inflow: number;
  short: number;
  safe: number;
  parts: Part[];
  parts_total: number;
  cover_total: number;
  actual: boolean;
  partial?: boolean;
}

export interface Month {
  month: string;
  from: string;
  to: string;
  income: number;
  obligations: number;
  everyday: number;
  short: number;
  parts: Part[];
  parts_total: number;
  cover_total: number;
  partial: boolean;
}

export interface Bead {
  name: string;
  amount: number;
  date: string;
  source: string;
  kind: string;
  part: PartKey;
  via_card: boolean;
  bill_id: number | null;
  movable: boolean;
  due_day: number | null;
}

export interface PaycheckViews {
  as_of: string;
  parts: { key: PartKey; label: string }[];
  windows: Check[];
  history: Check[];
  months: Month[];
  beads: { start: string; end: string; items: Bead[] }[];
  share: number | null;
  basis: string;
  not_advice: string;
}

export function partColours(look: string): Record<PartKey, string> {
  const c = palette(look as never);
  return { housing: c.housing, bills: c.bill, card: c.debt, later: c.later, aside: c.buffer, saving: c.saving, yours: c.safe };
}

// Text on a part's fill: dark ink on the light fills, white on the deep ones; checked per colour.
export function inkOn(hex: string): string {
  const h = hex.replace("#", "");
  const [r, g, b] = [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255).map((c) => (c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4));
  const lum = 0.2126 * r + 0.7152 * g + 0.0722 * b;
  return (lum + 0.05) / 0.05 >= 1.05 / (lum + 0.05) ? "#141518" : "#ffffff";
}
