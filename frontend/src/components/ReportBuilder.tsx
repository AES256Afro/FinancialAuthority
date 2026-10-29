// Reports you build (M32): pick what the rows are, the period and the picture, and the app sums it; a report saved
// by name comes back with one tap. Also groups of categories, the year in review, and the totals a tax return asks
// about. Every figure is the engine's (GET /api/v1/reports/custom…); these pictures draw them and add nothing.
import { ChartColumn, ChartLine, CircleCheck, CircleDashed, Download, Table2 } from "lucide-react";
import { useState, type ReactNode } from "react";
import { Link } from "react-router-dom";
import { api, useApi, type Loaded } from "../api";
import { localToday, money, shortDate } from "../format";
import { ROLES, VALIDATED_ORDER } from "../looks";
import { ConfirmButton } from "./ConfirmButton";
import { DEMO } from "../demo/shim";
import { Empty, Failed, Loading, Said } from "./State";

type Rows = "category" | "group" | "merchant" | "account" | "tag";
type Period = "month" | "quarter" | "year" | "last_30" | "last_90" | "last_365" | "custom";
type Picture = "bars" | "line" | "table";
type Note = { text: string; tone: "info" | "error" } | null;

interface Item {
  name: string;
  total: number;
  count: number;
  share: number;
  months: number[];
}

interface Custom {
  rows: Rows;
  period: Period;
  from: string;
  to: string;
  months: string[];
  items: Item[];
  total: number;
  month_totals: number[];
  not_advice: string;
}

interface Spec {
  rows: Rows;
  period: Period;
  picture: Picture;
  include_income: boolean;
  only: string[];
  start: string | null;
  end: string | null;
}

interface SavedReport extends Spec {
  name: string;
}

interface SavedList {
  reports: SavedReport[];
  groups: Record<string, string[]>;
}

interface YearReview {
  year: number;
  from: string;
  to: string;
  spent: number;
  income: number;
  kept: number;
  categories: Item[];
  merchants: Item[];
  months: string[];
  month_totals: number[];
  busiest_month: string | null;
  not_advice: string;
}

interface TaxTotals {
  year: number;
  from: string;
  to: string;
  categories: Item[];
  tagged_tax: Item | null;
  note: string;
  not_advice: string;
  // The pack (M50): the paystubs summed, the receipts kept by category, the statements kept month by month.
  paystubs?: { count: number; gross: number | null; gross_missing: number; net: number; deductions: Item[]; employers: string[] };
  receipts?: (Item & { tax: boolean })[];
  statements?: { account: string; months: boolean[]; count: number }[];
  files?: number;
  said?: string;
}

const MONTH_LETTERS = ["J", "F", "M", "A", "M", "J", "J", "A", "S", "O", "N", "D"];

/** What the pack holds for the year, picture first: pay as tiles and its deductions as bars, receipts by category,
 * and a row of twelve months per account, filled where a statement is kept. Then the one download. */
function TaxPackParts({ data }: { data: TaxTotals }) {
  const pay = data.paystubs;
  const sub = { margin: 0, fontSize: 14, fontWeight: 800 } as const;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, borderTop: "1px solid var(--surface-2)", paddingTop: 12 }}>
      {data.said && <span style={{ fontSize: 14, fontWeight: 700 }}>{data.said}</span>}
      {pay && pay.count > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <h3 style={sub}>Pay for the year, from {many(pay.count, "paystub", "paystubs")}</h3>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            {pay.gross !== null && (
              <div style={{ padding: "8px 12px", borderRadius: 10, background: "var(--surface-2)", minWidth: 130 }}>
                <div className="lab">Gross pay</div>
                <strong className="num" style={{ fontSize: 20 }}>{money(pay.gross)}</strong>
                {pay.gross_missing > 0 && <div style={{ fontSize: 11, color: "var(--muted)" }}>{many(pay.gross_missing, "stub", "stubs")} without a gross figure</div>}
              </div>
            )}
            <div style={{ padding: "8px 12px", borderRadius: 10, background: "var(--surface-2)", minWidth: 130 }}>
              <div className="lab">Net pay</div>
              <strong className="num" style={{ fontSize: 20 }}>{money(pay.net)}</strong>
            </div>
          </div>
          {pay.deductions.length > 0 && (
            <>
              <span style={{ fontSize: 13, color: "var(--ink-2)" }}>Taken from pay, summed from the paystubs:</span>
              <Bars items={pay.deductions} share={false} noun={["paystub", "paystubs"]} />
            </>
          )}
        </div>
      )}
      {data.receipts && data.receipts.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <h3 style={sub}>Receipts kept, by the purchase's category</h3>
          <Bars items={data.receipts.map((r) => ({ ...r, name: r.tax ? `${r.name} · tax category` : r.name }))} share={false} noun={["receipt", "receipts"]} />
        </div>
      )}
      {data.statements && data.statements.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          <h3 style={sub}>Statements kept, month by month</h3>
          {data.statements.map((st) => (
            <div key={st.account} style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: "4px 10px" }}>
              <span style={{ flex: "0 1 160px", minWidth: 0, fontSize: 13, fontWeight: 700, overflowWrap: "anywhere" }}>{st.account}</span>
              <div role="img" aria-label={`${st.account}: ${st.count} of 12 months kept`} style={{ display: "grid", gridTemplateColumns: "repeat(12, 22px)", gap: 3 }}>
                {st.months.map((kept, i) => (
                  <span key={i} title={`${MONTH_LETTERS[i]}: ${kept ? "kept" : "not kept"}`} style={{ height: 22, borderRadius: 4, display: "flex", alignItems: "center", justifyContent: "center", fontSize: 10, fontWeight: 800, background: kept ? "var(--ink-2)" : "transparent", color: kept ? "var(--inverse)" : "var(--muted)", border: kept ? 0 : "1px dashed var(--hairline)", boxSizing: "border-box" }}>{MONTH_LETTERS[i]}</span>
                ))}
              </div>
            </div>
          ))}
          <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)" }}>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}><CircleCheck size={13} aria-hidden="true" style={{ display: "inline-block" }} /> Filled: a statement is kept</span>
            <span style={{ display: "inline-flex", alignItems: "center", gap: 5 }}><CircleDashed size={13} aria-hidden="true" style={{ display: "inline-block" }} /> Dashed: none for that month</span>
          </div>
        </div>
      )}
      <div style={{ display: "flex", flexDirection: "column", gap: 4, alignItems: "flex-start" }}>
        <a className="btn dark" href={DEMO ? undefined : `/api/v1/reports/custom/tax/pack?year=${data.year}`} download={`tax-pack-${data.year}.zip`} aria-disabled={DEMO}
          style={{ textDecoration: "none", display: "inline-flex", alignItems: "center", gap: 6, opacity: DEMO ? 0.5 : 1 }}>
          <Download size={16} aria-hidden="true" /> Download the {data.year} tax pack (.zip)
        </a>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>A summary to read, the same as a spreadsheet, and {many(data.files ?? 0, "kept file", "kept files")} in folders: receipts by category, statements by account, paystubs by date.{DEMO ? " Not in the demo." : ""}</span>
      </div>
    </div>
  );
}

const ROW_WORD: Record<Rows, string> = { category: "Category", group: "Your groups", merchant: "Merchant", account: "Account", tag: "Tag" };
const PERIOD_WORD: Record<Period, string> = {
  month: "This month",
  quarter: "This quarter",
  year: "This year",
  last_30: "The last 30 days",
  last_90: "The last 90 days",
  last_365: "The last 365 days",
  custom: "Between two dates",
};
const PICTURES: { id: Picture; word: string; icon: ReactNode }[] = [
  { id: "bars", word: "Bars", icon: <ChartColumn size={14} aria-hidden="true" /> },
  { id: "line", word: "A line by month", icon: <ChartLine size={14} aria-hidden="true" /> },
  { id: "table", word: "A table", icon: <Table2 size={14} aria-hidden="true" /> },
];
/** Up to six lines, coloured in the validated order from looks/; each is also named in the legend and the table. */
const SERIES = VALIDATED_ORDER.map((role) => `var(${ROLES.find((r) => r.role === role)?.token ?? "--ink"})`);
const START: Spec = { rows: "category", period: "month", picture: "bars", include_income: false, only: [], start: null, end: null };

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];
const monthName = (key: string) => MONTHS[Number(key.slice(5, 7)) - 1];
const monthLong = (key: string) => `${monthName(key)} ${key.slice(0, 4)}`;
const pct = (share: number) => `${(share * 100).toFixed(0)}%`;
const many = (n: number, one: string, more: string) => `${n} ${n === 1 ? one : more}`;

/** "Wed Oct 1 to Thu Oct 16, 2026", with both years when the span crosses one. */
function span(from: string, to: string): string {
  const a = from.slice(0, 4);
  const b = to.slice(0, 4);
  return a === b ? `${shortDate(from)} to ${shortDate(to)}, ${b}` : `${shortDate(from)}, ${a} to ${shortDate(to)}, ${b}`;
}

function reportPath(s: Spec): string | null {
  const q = new URLSearchParams({ rows: s.rows, period: s.period, include_income: String(s.include_income) });
  if (s.only.length) q.set("only", s.only.join(","));
  if (s.period === "custom") {
    if (!s.start || !s.end || s.start > s.end) return null;
    q.set("start", s.start);
    q.set("end", s.end);
  }
  return `/api/v1/reports/custom?${q.toString()}`;
}

function sameSpec(a: Spec, b: Spec): boolean {
  const dates = a.period !== "custom" || (a.start === b.start && a.end === b.end);
  return a.rows === b.rows && a.period === b.period && a.picture === b.picture && a.include_income === b.include_income && (a.only ?? []).join("\n") === (b.only ?? []).join("\n") && dates;
}

function describe(s: Spec): string {
  const period = s.period === "custom" && s.start && s.end ? span(s.start, s.end) : PERIOD_WORD[s.period].toLowerCase();
  return `by ${ROW_WORD[s.rows].toLowerCase()}, ${period}${s.include_income ? ", with income" : ""}`;
}

const cardStyle = { padding: 16, display: "flex", flexDirection: "column", gap: 12, scrollMarginTop: 16 } as const;
const h2Style = { margin: 0, fontSize: 15, fontWeight: 700 } as const;
const h3Style = { margin: 0, fontSize: 13, fontWeight: 800 } as const;
const small = { minHeight: 32, padding: "4px 10px" } as const;

/** A row of buttons that act as one choice; the chosen one is dark and pressed. */
function Choice<T extends string>({ label, value, options, onChange }: { label: string; value: T; options: { id: T; word: string; icon?: ReactNode }[]; onChange: (v: T) => void }) {
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
      <span className="lab">{label}</span>
      <div role="group" aria-label={label} style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
        {options.map((o) => (
          <button key={o.id} type="button" aria-pressed={value === o.id} className={`btn${value === o.id ? " dark" : ""}`} style={{ minHeight: 36, padding: "4px 12px", display: "inline-flex", alignItems: "center", gap: 6 }} onClick={() => onChange(o.id)}>
            {o.icon}
            {o.word}
          </button>
        ))}
      </div>
    </div>
  );
}

function YearPick({ value, onChange }: { value: number; onChange: (year: number) => void }) {
  const now = Number(localToday().slice(0, 4));
  const years = Array.from({ length: 6 }, (_, k) => now - k);
  if (!years.includes(value)) years.push(value);
  return (
    <label style={{ display: "inline-flex", alignItems: "center", gap: 6, marginLeft: "auto" }}>
      <span className="lab">Year</span>
      <select className="input" value={value} onChange={(e) => onChange(Number(e.target.value))} style={{ minHeight: 36, padding: "4px 10px" }}>
        {years.map((y) => <option key={y} value={y}>{y}</option>)}
      </select>
    </label>
  );
}

/** One bar per row, biggest first (the engine sorts them). Money that came in is drawn in the paycheck colour and
 * says "came in" in words. */
function Bars({ items, share, limit = 12, noun = ["transaction", "transactions"] }: { items: Item[]; share: boolean; limit?: number; noun?: [string, string] }) {
  const [all, setAll] = useState(false);
  const shown = all ? items : items.slice(0, limit);
  const max = Math.max(1, ...items.map((i) => Math.abs(i.total)));
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
      {shown.map((i) => {
        const came = i.total < 0;
        return (
          <div key={i.name} style={{ display: "grid", gridTemplateColumns: "minmax(0, 11rem) minmax(40px, 1fr) minmax(5.5rem, auto)", gap: 10, alignItems: "center", fontSize: 13 }}>
            <span style={{ minWidth: 0, display: "flex", flexDirection: "column" }}>
              <span style={{ overflow: "hidden", textOverflow: "ellipsis", whiteSpace: "nowrap" }} title={i.name}>{i.name}</span>
              <span style={{ fontSize: 11, color: "var(--muted)" }}>{many(i.count, noun[0], noun[1])}</span>
            </span>
            <div aria-hidden="true" style={{ height: 14, background: "var(--surface-2)", borderRadius: 4 }}>
              <div style={{ width: `${(Math.abs(i.total) / max) * 100}%`, height: "100%", background: came ? "var(--paycheck)" : "var(--bill)", borderRadius: 4 }} />
            </div>
            <span style={{ textAlign: "right", display: "flex", flexDirection: "column" }}>
              <strong className="num" style={{ color: came ? "var(--paycheck-ink)" : "var(--ink)" }}>{money(Math.abs(i.total))}{came ? " came in" : ""}</strong>
              {share && !came && <span style={{ fontSize: 11, color: "var(--ink-2)" }}>{pct(i.share)} of the total</span>}
            </span>
          </div>
        );
      })}
      {items.length > limit && (
        <button type="button" className="btn" aria-expanded={all} style={{ ...small, alignSelf: "flex-start" }} onClick={() => setAll((a) => !a)}>
          {all ? `Show the biggest ${limit}` : `Show all ${items.length} rows`}
        </button>
      )}
    </div>
  );
}

/** A line per row across the months, for the six biggest rows, with a legend that names each colour. */
function Lines({ data }: { data: Custom }) {
  const series = data.items.slice(0, 6);
  const months = data.months;
  const w = 640;
  const h = 220;
  const pad = { l: 30, r: 30, t: 24, b: 26 };
  const values = series.flatMap((s) => s.months);
  const top = Math.max(...values, 0);
  const hi = Math.max(1, top);
  const lo = Math.min(0, ...values);
  const x = (i: number) => (months.length < 2 ? w / 2 : pad.l + ((w - pad.l - pad.r) * i) / (months.length - 1));
  const y = (v: number) => pad.t + ((h - pad.t - pad.b) * (hi - v)) / (hi - lo);
  const every = Math.ceil(months.length / 12);
  const crosses = months.length > 0 && months[0].slice(0, 4) !== months[months.length - 1].slice(0, 4);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)" }}>
        {series.map((s, k) => (
          <span key={s.name} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
            <span className="sw" style={{ background: SERIES[k], margin: 0 }} />
            {s.name} <strong className="num" style={{ color: "var(--ink)" }}>{money(s.total)}</strong>
          </span>
        ))}
      </div>
      <svg viewBox={`0 0 ${w} ${h}`} width="100%" role="img" aria-label={`A line per row by month: ${series.map((s) => s.name).join(", ")}`} style={{ display: "block", maxWidth: 760 }}>
        {top > 0 && (
          <>
            <line x1={pad.l} x2={w - pad.r} y1={y(top)} y2={y(top)} stroke="var(--hairline)" strokeWidth={1} />
            <text x={pad.l} y={y(top) - 6} fontSize={11} fill="var(--muted)" className="num">{money(top)}, the biggest month</text>
          </>
        )}
        <line x1={pad.l} x2={w - pad.r} y1={y(0)} y2={y(0)} stroke="var(--ink-2)" strokeWidth={1} />
        {series.map((s, k) => (
          <g key={s.name}>
            {s.months.length > 1 && <polyline points={s.months.map((v, i) => `${x(i)},${y(v)}`).join(" ")} fill="none" stroke={SERIES[k]} strokeWidth={2.5} strokeLinejoin="round" strokeLinecap="round" />}
            {s.months.map((v, i) => (
              <circle key={months[i]} cx={x(i)} cy={y(v)} r={3.5} fill={SERIES[k]}>
                <title>{`${s.name}, ${monthLong(months[i])}: ${money(v)}`}</title>
              </circle>
            ))}
          </g>
        ))}
        {months.map((m, i) =>
          i % every === 0 ? (
            <text key={m} x={x(i)} y={h - 8} textAnchor="middle" fontSize={11} fill="var(--muted)">
              {monthName(m)}{crosses && (i === 0 || m.endsWith("-01")) ? ` ${m.slice(0, 4)}` : ""}
            </text>
          ) : null,
        )}
      </svg>
      {months.length < 2 && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>This period is one month, so each row is a single dot. A line needs two months or more: pick the last 90 days or this year.</span>}
      {data.items.length > series.length && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>The {series.length} biggest rows are drawn; all {data.items.length} are in the table.</span>}
    </div>
  );
}

/** Every row, with a column per month when the period spans more than one. */
function ReportTable({ data, share }: { data: Custom; share: boolean }) {
  const byMonth = data.months.length > 1;
  const right = { textAlign: "right", whiteSpace: "nowrap" } as const;
  return (
    <div style={{ overflowX: "auto" }}>
      <table className="ledger">
        <thead>
          <tr>
            <th>{ROW_WORD[data.rows]}</th>
            {byMonth && data.months.map((m) => <th key={m} style={right}>{monthLong(m)}</th>)}
            <th style={right}>Total</th>
            {share && <th style={right}>Share</th>}
            <th style={right}>Transactions</th>
          </tr>
        </thead>
        <tbody>
          {data.items.map((i) => (
            <tr key={i.name}>
              <td>{i.name}</td>
              {byMonth && i.months.map((v, k) => <td key={data.months[k]} className="num" style={right}>{money(v)}</td>)}
              <td className="num" style={{ ...right, fontWeight: 700 }}>{money(i.total)}</td>
              {share && <td className="num" style={{ ...right, color: "var(--ink-2)" }}>{pct(i.share)}</td>}
              <td className="num" style={{ ...right, color: "var(--muted)" }}>{i.count}</td>
            </tr>
          ))}
        </tbody>
        <tfoot>
          <tr>
            <td style={{ fontWeight: 800 }}>Total</td>
            {byMonth && data.month_totals.map((v, k) => <td key={data.months[k]} className="num" style={{ ...right, fontWeight: 800 }}>{money(v)}</td>)}
            <td className="num" style={{ ...right, fontWeight: 800 }}>{money(data.total)}</td>
            {share && <td />}
            <td />
          </tr>
        </tfoot>
      </table>
    </div>
  );
}

/** The same numbers as a table, one tap under every picture. */
function TableTwin({ children }: { children: ReactNode }) {
  return (
    <details>
      <summary style={{ cursor: "pointer", fontWeight: 700, fontSize: 13, padding: "6px 0" }}>The same numbers as a table</summary>
      {children}
    </details>
  );
}

function Result({ data, picture, income, updating }: { data: Custom; picture: Picture; income: boolean; updating: boolean }) {
  const share = !income;
  return (
    <div aria-busy={updating} style={{ display: "flex", flexDirection: "column", gap: 10, opacity: updating ? 0.6 : 1 }}>
      <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "baseline", fontSize: 13 }}>
        <strong>By {ROW_WORD[data.rows].toLowerCase()}, {span(data.from, data.to)}</strong>
        <span>{income ? "Spent, less what came in" : "Spent"} <strong className="num">{money(data.total)}</strong></span>
        <span style={{ color: "var(--muted)" }}>{many(data.items.length, "row", "rows")}</span>
        {updating && <span role="status" style={{ color: "var(--ink-2)" }}>Updating…</span>}
      </div>
      {data.items.length === 0 ? (
        <Empty card={false}>Nothing in this period with these choices. Try a longer period, or another kind of row.</Empty>
      ) : (
        <>
          {picture === "bars" && (
            <>
              <div style={{ display: "flex", gap: 14, flexWrap: "wrap", fontSize: 12, color: "var(--ink-2)" }}>
                <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span className="sw" style={{ background: "var(--bill)", margin: 0 }} />Spent</span>
                {income && <span style={{ display: "inline-flex", alignItems: "center", gap: 6 }}><span className="sw" style={{ background: "var(--paycheck)", margin: 0 }} />Came in</span>}
              </div>
              <Bars items={data.items} share={share} />
            </>
          )}
          {picture === "line" && <Lines data={data} />}
          {picture === "table" ? <ReportTable data={data} share={share} /> : <TableTwin><ReportTable data={data} share={share} /></TableTwin>}
        </>
      )}
      <div style={{ fontSize: 12, color: "var(--muted)" }}>
        {data.not_advice}
        {income ? " With money that came in included, it counts against what was spent; in the table it has a minus sign." : " Spending leaves out transfers, card payments and income."}
      </div>
    </div>
  );
}

/** Build a report, and the groups of categories it can use; both read the saved list. */
export function ReportBuilder() {
  const saved = useApi<SavedList>("/api/v1/reports/custom/saved");
  const [spec, setSpec] = useState<Spec>(START);
  const path = reportPath(spec);
  const report = useApi<Custom>(path);
  const [name, setName] = useState("");
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<Note>(null);
  const reports = saved.data?.reports ?? [];
  const noGroups = saved.data !== null && Object.keys(saved.data.groups ?? {}).length === 0;
  const replacing = reports.some((r) => r.name === name.trim());

  function change(patch: Partial<Spec>) {
    setSpec((s) => ({ ...s, ...patch }));
    setSaid(null);
  }
  function setPeriod(p: Period) {
    const today = localToday();
    change(p === "custom" ? { period: p, start: spec.start ?? `${today.slice(0, 4)}-01-01`, end: spec.end ?? today } : { period: p });
  }
  function open(r: SavedReport) {
    setSpec({ rows: r.rows, period: r.period, picture: r.picture, include_income: Boolean(r.include_income), only: r.only ?? [], start: r.start ?? null, end: r.end ?? null });
    setName(r.name);
    setSaid(null);
  }
  async function save() {
    const n = name.trim();
    if (!n) {
      setSaid({ text: "Give the report a name first.", tone: "error" });
      return;
    }
    setBusy(true);
    setSaid(null);
    try {
      const custom = spec.period === "custom";
      await api("/api/v1/reports/custom/saved", { method: "POST", body: JSON.stringify({ ...spec, name: n, start: custom ? spec.start : null, end: custom ? spec.end : null }) });
      setSaid({ text: `Saved as "${n}". It is with the saved reports at the top of this card.`, tone: "info" });
      saved.reload();
    } catch (e) {
      setSaid({ text: (e as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }
  async function remove(n: string) {
    await api(`/api/v1/reports/custom/saved/${encodeURIComponent(n)}`, { method: "DELETE" });
    saved.reload();
  }

  const failed = Boolean(report.error) && !report.loading;
  return (
    <>
      <section id="build" className="card" style={cardStyle}>
        <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
          <h2 style={h2Style}>Build a report</h2>
          <span style={{ fontSize: 13, color: "var(--ink-2)" }}>Choose what each row is, the period and the picture. The app adds it up and draws it at once.</span>
        </div>

        {saved.error && !saved.data && <Failed error={saved.error} what="your saved reports" onRetry={saved.reload} card={false} />}
        {saved.data && (
          <div role="group" aria-label="Saved reports" style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <span className="lab">Saved</span>
            {reports.length === 0 && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>Nothing saved yet. Build one below and press Save as; it comes back here with one tap.</span>}
            {reports.map((r) => {
              const on = sameSpec(r, spec);
              return (
                <span key={r.name} style={{ display: "inline-flex", alignItems: "center", gap: 4, flexWrap: "wrap", border: "1px solid var(--hairline)", borderRadius: 12, padding: 3 }}>
                  <button type="button" aria-pressed={on} className={`btn${on ? " dark" : ""}`} style={{ ...small, textAlign: "left" }} onClick={() => open(r)}>
                    {r.name}
                    <span style={{ fontWeight: 400, fontSize: 11 }}> · {describe(r)}</span>
                  </button>
                  <ConfirmButton label="Remove" name={`Remove the saved report ${r.name}`} question={`Remove the saved report "${r.name}"? Only the shortcut goes; your transactions stay.`} yes="Yes, remove it" no="Keep it" onConfirm={() => remove(r.name)} style={{ ...small, fontSize: 12 }} />
                </span>
              );
            })}
          </div>
        )}

        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "flex-end" }}>
          <Choice label="Each row is a" value={spec.rows} options={(Object.keys(ROW_WORD) as Rows[]).map((id) => ({ id, word: ROW_WORD[id] }))} onChange={(rows) => change({ rows, only: [] })} />
          <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
            <span className="lab">Period</span>
            <select className="input" value={spec.period} onChange={(e) => setPeriod(e.target.value as Period)} style={{ minHeight: 36, padding: "4px 10px" }}>
              {(Object.keys(PERIOD_WORD) as Period[]).map((p) => <option key={p} value={p}>{PERIOD_WORD[p]}</option>)}
            </select>
          </label>
          {spec.period === "custom" && (
            <>
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span className="lab">From</span>
                <input className="input" type="date" value={spec.start ?? ""} max={spec.end ?? undefined} onChange={(e) => change({ start: e.target.value || null })} style={{ minHeight: 36, padding: "4px 10px" }} />
              </label>
              <label style={{ display: "flex", flexDirection: "column", gap: 4 }}>
                <span className="lab">To</span>
                <input className="input" type="date" value={spec.end ?? ""} min={spec.start ?? undefined} onChange={(e) => change({ end: e.target.value || null })} style={{ minHeight: 36, padding: "4px 10px" }} />
              </label>
            </>
          )}
        </div>
        <div style={{ display: "flex", gap: 14, flexWrap: "wrap", alignItems: "flex-end" }}>
          <Choice label="Picture" value={spec.picture} options={PICTURES} onChange={(picture) => change({ picture })} />
          <label style={{ display: "inline-flex", alignItems: "center", gap: 8, minHeight: 36, fontSize: 13 }}>
            <input type="checkbox" checked={spec.include_income} onChange={(e) => change({ include_income: e.target.checked })} style={{ width: 18, height: 18 }} />
            Include money that came in (pay and other income)
          </label>
        </div>
        {spec.only.length > 0 && (
          <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", fontSize: 13 }}>
            <span>Showing only: <strong>{spec.only.join(", ")}</strong></span>
            <button type="button" className="btn" style={small} onClick={() => change({ only: [] })}>Show every row</button>
          </div>
        )}
        {spec.rows === "group" && noGroups && (
          <div style={{ fontSize: 13, color: "var(--ink-2)" }}>You have no groups yet, so each category shows on its own. Make one under <a href="#groups" style={{ fontWeight: 700 }}>Groups of categories</a>.</div>
        )}

        <div style={{ borderTop: "1px solid var(--surface-2)", paddingTop: 12 }}>
          {!path && <div role="status" style={{ fontSize: 13, fontWeight: 700, color: "var(--ink)" }}>Pick both dates, the first on or before the second.</div>}
          {path && failed && report.error && <Failed error={report.error} what="the report" onRetry={report.reload} card={false} />}
          {path && !report.data && !report.error && <Loading what="the report" card={false} />}
          {path && report.data && !failed && <Result data={report.data} picture={spec.picture} income={spec.include_income} updating={report.loading} />}
        </div>

        <div style={{ display: "flex", gap: 8, alignItems: "flex-end", flexWrap: "wrap", borderTop: "1px solid var(--surface-2)", paddingTop: 12 }}>
          <label style={{ display: "flex", flexDirection: "column", gap: 4, flex: "1 1 220px" }}>
            <span className="lab">Name for this report</span>
            <input className="input" value={name} maxLength={60} onChange={(e) => { setName(e.target.value); setSaid(null); }} placeholder="Eating out this year" />
          </label>
          <button type="button" className="btn dark" disabled={busy || !path} onClick={save}>{busy ? "Saving…" : replacing ? "Save over it" : "Save as"}</button>
          {said && <Said text={said.text} tone={said.tone} />}
        </div>
        {replacing && !said && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>A report named "{name.trim()}" is saved already; saving puts these choices in its place.</span>}
      </section>
      <Groups saved={saved} onSaved={() => { saved.reload(); report.reload(); }} />
    </>
  );
}

/** Groups of categories: a name and the categories it adds together. A category is in one group at a time. */
function Groups({ saved, onSaved }: { saved: Loaded<SavedList>; onSaved: () => void }) {
  const seen = useApi<Custom>("/api/v1/reports/custom?rows=category&period=last_365");
  const groups = saved.data?.groups ?? {};
  const [editing, setEditing] = useState<string | null>(null);
  const [gname, setGname] = useState("");
  const [picked, setPicked] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [said, setSaid] = useState<Note>(null);
  const categories = [...new Set([...(seen.data?.items ?? []).map((i) => i.name), ...Object.values(groups).flat()])].sort((a, b) => a.localeCompare(b));
  const homeOf = (c: string) => Object.entries(groups).find(([g, cs]) => g !== editing && cs.includes(c))?.[0];

  function reset() {
    setEditing(null);
    setGname("");
    setPicked([]);
  }
  function change(g: string) {
    setEditing(g);
    setGname(g);
    setPicked(groups[g] ?? []);
    setSaid(null);
  }
  function toggle(c: string) {
    setPicked((p) => (p.includes(c) ? p.filter((x) => x !== c) : [...p, c]));
    setSaid(null);
  }
  async function put(next: Record<string, string[]>) {
    await api("/api/v1/reports/custom/groups", { method: "PUT", body: JSON.stringify({ groups: next }) });
    onSaved();
  }
  async function save() {
    const n = gname.trim();
    if (!n) {
      setSaid({ text: "Give the group a name first.", tone: "error" });
      return;
    }
    if (!picked.length) {
      setSaid({ text: "Pick at least one category for it.", tone: "error" });
      return;
    }
    // A picked category leaves any other group it was in, so it is counted once.
    const next: Record<string, string[]> = {};
    for (const [g, cs] of Object.entries(groups)) {
      if (g === editing || g === n) continue;
      const rest = cs.filter((c) => !picked.includes(c));
      if (rest.length) next[g] = rest;
    }
    next[n] = picked;
    setBusy(true);
    setSaid(null);
    try {
      await put(next);
      setSaid({ text: `Saved the group "${n}" with ${many(picked.length, "category", "categories")}.`, tone: "info" });
      reset();
    } catch (e) {
      setSaid({ text: (e as Error).message, tone: "error" });
    } finally {
      setBusy(false);
    }
  }
  async function remove(g: string) {
    await put(Object.fromEntries(Object.entries(groups).filter(([k]) => k !== g)));
    if (editing === g) reset();
    setSaid({ text: `Removed the group "${g}". Its categories show on their own again.`, tone: "info" });
  }

  const names = Object.keys(groups);
  return (
    <section id="groups" className="card" style={cardStyle}>
      <div style={{ display: "flex", flexDirection: "column", gap: 2 }}>
        <h2 style={h2Style}>Groups of categories</h2>
        <span style={{ fontSize: 13, color: "var(--ink-2)" }}>A group adds categories together under one name. Choose "Your groups" as the rows above to see them.</span>
      </div>
      {saved.error && !saved.data && <Failed error={saved.error} what="your groups" onRetry={saved.reload} card={false} />}
      {!saved.data && !saved.error && <Loading what="your groups" card={false} />}
      {saved.data && names.length === 0 && <Empty card={false}>No groups yet. Make one below: a name, then the categories it holds.</Empty>}
      {names.length > 0 && (
        <div style={{ display: "flex", flexDirection: "column", gap: 8 }}>
          {names.map((g) => (
            <div key={g} style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap", padding: "8px 10px", borderRadius: 10, background: editing === g ? "var(--paycheck-soft)" : "var(--surface-2)" }}>
              <strong style={{ fontSize: 13 }}>{g}</strong>
              <span aria-hidden="true" style={{ color: "var(--muted)" }}>=</span>
              <span style={{ display: "flex", gap: 4, flexWrap: "wrap", flex: "1 1 200px" }}>
                {groups[g].map((c) => <span key={c} style={{ fontSize: 12, padding: "2px 8px", borderRadius: 999, background: "var(--surface)", border: "1px solid var(--hairline)" }}>{c}</span>)}
              </span>
              <button type="button" className="btn" style={small} onClick={() => change(g)}>Change</button>
              <ConfirmButton label="Remove" name={`Remove the group ${g}`} question={`Remove the group "${g}"? Its categories stay and show on their own again.`} yes="Yes, remove it" no="Keep it" onConfirm={() => remove(g)} style={small} />
            </div>
          ))}
        </div>
      )}

      <div style={{ display: "flex", flexDirection: "column", gap: 8, borderTop: "1px solid var(--surface-2)", paddingTop: 12 }}>
        <h3 style={h3Style}>{editing ? `Change "${editing}"` : "Make a group"}</h3>
        <label style={{ display: "flex", flexDirection: "column", gap: 4, maxWidth: 320 }}>
          <span className="lab">Group name</span>
          <input className="input" value={gname} maxLength={40} onChange={(e) => { setGname(e.target.value); setSaid(null); }} placeholder="Eating out" />
        </label>
        <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
          <span className="lab">Its categories ({picked.length} picked)</span>
          {seen.error && !seen.data && <Failed error={seen.error} what="the categories" onRetry={seen.reload} card={false} />}
          {!seen.data && !seen.error && <Loading what="the categories" card={false} />}
          {seen.data && categories.length === 0 && <Empty card={false} to="/settings/connectors#import" action="Import a file">No categories seen in the last 365 days yet.</Empty>}
          {categories.length > 0 && (
            <div role="group" aria-label="Categories in this group" style={{ display: "flex", gap: 4, flexWrap: "wrap" }}>
              {categories.map((c) => {
                const on = picked.includes(c);
                const home = homeOf(c);
                return (
                  <button key={c} type="button" aria-pressed={on} className={`btn${on ? " dark" : ""}`} style={{ ...small, fontWeight: on ? 800 : 600 }} onClick={() => toggle(c)}>
                    {on ? "✓ " : ""}{c}
                    {home && <span style={{ fontWeight: 400, fontSize: 11 }}> · in {home}</span>}
                  </button>
                );
              })}
            </div>
          )}
          <span style={{ fontSize: 12, color: "var(--ink-2)" }}>The categories are the ones seen in the last 365 days. A category is in one group at a time: picking one marked "in" another group moves it here.</span>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button type="button" className="btn dark" disabled={busy} onClick={save}>{busy ? "Saving…" : editing ? "Save the group" : "Make the group"}</button>
          {editing && <button type="button" className="btn" onClick={() => { reset(); setSaid(null); }}>Stop changing it</button>}
          {said && <Said text={said.text} tone={said.tone} />}
        </div>
      </div>
    </section>
  );
}

/** Spent by month for a year, the busiest month marked in words. */
function YearMonths({ months, totals, busiest }: { months: string[]; totals: number[]; busiest: string | null }) {
  const w = 640;
  const h = 160;
  const pad = { l: 6, r: 6, t: 34, b: 22 };
  const max = Math.max(1, ...totals);
  const slot = (w - pad.l - pad.r) / Math.max(1, months.length);
  const bar = Math.min(34, slot * 0.6);
  const y = (v: number) => pad.t + (h - pad.t - pad.b) * (1 - Math.max(0, v) / max);
  return (
    <svg viewBox={`0 0 ${w} ${h}`} width="100%" role="img" aria-label="Spent by month" style={{ display: "block", maxWidth: 720 }}>
      {months.map((m, i) => {
        const cx = pad.l + slot * i + slot / 2;
        const top = m === busiest;
        return (
          <g key={m}>
            <title>{`${monthLong(m)}: spent ${money(totals[i])}${top ? ", the busiest month" : ""}`}</title>
            <rect x={cx - bar / 2} y={y(totals[i])} width={bar} height={Math.max(0, y(0) - y(totals[i]))} rx={3} fill="var(--bill)" opacity={top ? 1 : 0.55} />
            {top && (
              <>
                <text x={cx} y={y(totals[i]) - 20} textAnchor="middle" fontSize={11} fontWeight={800} fill="var(--bill-ink)">Busiest</text>
                <text x={cx} y={y(totals[i]) - 7} textAnchor="middle" fontSize={11} fontWeight={700} fill="var(--bill-ink)" className="num">{money(totals[i])}</text>
              </>
            )}
            <text x={cx} y={h - 7} textAnchor="middle" fontSize={11} fill="var(--muted)">{monthName(m)}</text>
          </g>
        );
      })}
      <line x1={pad.l} x2={w - pad.r} y1={y(0)} y2={y(0)} stroke="var(--ink-2)" strokeWidth={1} />
    </svg>
  );
}

function Tile({ label, value, ink, bar, fill, note }: { label: string; value: string; ink?: string; bar?: number; fill?: string; note?: string }) {
  return (
    <div style={{ background: "var(--surface-2)", borderRadius: 10, padding: "10px 12px", display: "flex", flexDirection: "column", gap: 4 }}>
      <span className="lab">{label}</span>
      <strong className="num" style={{ fontSize: 22, fontWeight: 800, color: ink ?? "var(--ink)" }}>{value}</strong>
      {bar !== undefined && (
        <div aria-hidden="true" style={{ height: 8, background: "var(--surface)", borderRadius: 4 }}>
          <div style={{ width: `${bar * 100}%`, height: "100%", background: fill, borderRadius: 4 }} />
        </div>
      )}
      {note && <span style={{ fontSize: 12, color: "var(--ink-2)" }}>{note}</span>}
    </div>
  );
}

export function YearInReview() {
  const [year, setYear] = useState(() => Number(localToday().slice(0, 4)));
  const state = useApi<YearReview>(`/api/v1/reports/custom/year?year=${year}`);
  const { data } = state;
  const scale = data ? Math.max(1, data.income, data.spent) : 1;
  const busiestAt = data?.busiest_month ? data.months.indexOf(data.busiest_month) : -1;
  return (
    <section id="year" className="card" style={cardStyle}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <h2 style={h2Style}>The year in review</h2>
        <YearPick value={year} onChange={setYear} />
      </div>
      {state.error && !state.loading && <Failed error={state.error} what="the year in review" onRetry={state.reload} card={false} />}
      {!data && !state.error && <Loading what="the year in review" card={false} />}
      {data && !(state.error && !state.loading) && (
        data.spent === 0 && data.income === 0 ? (
          <Empty card={false} to="/settings/connectors#import" action="Import a file">Nothing recorded in {data.year} yet.</Empty>
        ) : (
          <div aria-busy={state.loading} style={{ display: "flex", flexDirection: "column", gap: 12, opacity: state.loading ? 0.6 : 1 }}>
            <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{span(data.from, data.to)}{data.to.slice(5) !== "12-31" ? ", so far" : ""}</span>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(180px, 100%), 1fr))", gap: 10 }}>
              <Tile label="Came in" value={money(data.income)} ink="var(--paycheck-ink)" bar={Math.max(0, data.income) / scale} fill="var(--paycheck)" />
              <Tile label="Spent" value={money(data.spent)} ink="var(--bill-ink)" bar={Math.max(0, data.spent) / scale} fill="var(--bill)" />
              <Tile label={data.kept < 0 ? "Spent more than came in" : "Kept"} value={money(data.kept)} ink={data.kept < 0 ? "var(--critical-ink)" : "var(--safe-ink)"} note={data.kept < 0 ? "Spending was bigger than income this year." : "What came in and was not spent."} />
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: 4 }}>
              <h3 style={h3Style}>Spent, month by month</h3>
              {data.busiest_month && busiestAt >= 0 && <span style={{ fontSize: 13 }}>The busiest month was <strong>{monthLong(data.busiest_month)}</strong>, with <strong className="num">{money(data.month_totals[busiestAt])}</strong> spent.</span>}
              <YearMonths months={data.months} totals={data.month_totals} busiest={data.busiest_month} />
              <TableTwin>
                <div style={{ overflowX: "auto" }}>
                  <table className="ledger">
                    <thead><tr><th>Month</th><th style={{ textAlign: "right" }}>Spent</th></tr></thead>
                    <tbody>{data.months.map((m, i) => <tr key={m}><td>{monthLong(m)}{m === data.busiest_month ? " (busiest)" : ""}</td><td className="num" style={{ textAlign: "right" }}>{money(data.month_totals[i])}</td></tr>)}</tbody>
                  </table>
                </div>
              </TableTwin>
            </div>
            <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(min(300px, 100%), 1fr))", gap: 14 }}>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <h3 style={h3Style}>Top categories</h3>
                {data.categories.length ? <Bars items={data.categories} share limit={10} /> : <span style={{ fontSize: 13, color: "var(--ink-2)" }}>No spending in a category yet.</span>}
              </div>
              <div style={{ display: "flex", flexDirection: "column", gap: 6 }}>
                <h3 style={h3Style}>Top merchants</h3>
                {data.merchants.length ? <Bars items={data.merchants} share limit={10} /> : <span style={{ fontSize: 13, color: "var(--ink-2)" }}>No merchants yet.</span>}
              </div>
            </div>
            <TableTwin>
              <div style={{ overflowX: "auto" }}>
                <table className="ledger">
                  <thead><tr><th>Top</th><th>Name</th><th style={{ textAlign: "right" }}>Spent</th><th style={{ textAlign: "right" }}>Share</th><th style={{ textAlign: "right" }}>Transactions</th></tr></thead>
                  <tbody>
                    {[...data.categories.map((i) => ({ ...i, what: "Category" })), ...data.merchants.map((i) => ({ ...i, what: "Merchant" }))].map((i) => (
                      <tr key={`${i.what}:${i.name}`}><td style={{ color: "var(--ink-2)" }}>{i.what}</td><td>{i.name}</td><td className="num" style={{ textAlign: "right" }}>{money(i.total)}</td><td className="num" style={{ textAlign: "right", color: "var(--ink-2)" }}>{pct(i.share)}</td><td className="num" style={{ textAlign: "right", color: "var(--muted)" }}>{i.count}</td></tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </TableTwin>
            <div style={{ fontSize: 12, color: "var(--muted)" }}>{data.not_advice}</div>
          </div>
        )
      )}
    </section>
  );
}

export function TaxTime() {
  const [year, setYear] = useState<number | null>(null);
  const state = useApi<TaxTotals>(`/api/v1/reports/custom/tax${year ? `?year=${year}` : ""}`);
  const { data } = state;
  const rows = data ? [...data.categories, ...(data.tagged_tax ? [{ ...data.tagged_tax, name: 'Tagged "tax"' }] : [])] : [];
  return (
    <section id="tax" className="card" style={cardStyle}>
      <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
        <h2 style={h2Style}>Tax time</h2>
        <YearPick value={year ?? data?.year ?? Number(localToday().slice(0, 4))} onChange={setYear} />
      </div>
      <span style={{ fontSize: 13, color: "var(--ink-2)" }}>What a tax return usually asks about: medical costs, giving to charity, education, childcare, business costs and taxes paid, plus anything you tagged "tax".</span>
      {state.error && !state.loading && <Failed error={state.error} what="the tax totals" onRetry={state.reload} card={false} />}
      {!data && !state.error && <Loading what="the tax totals" card={false} />}
      {data && !(state.error && !state.loading) && (
        <div aria-busy={state.loading} style={{ display: "flex", flexDirection: "column", gap: 10, opacity: state.loading ? 0.6 : 1 }}>
          <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{span(data.from, data.to)}{data.to.slice(5) !== "12-31" ? ", so far" : ""}</span>
          {rows.length === 0 ? (
            <Empty card={false} to="/transactions" action="Open Transactions">Nothing in these categories, and nothing tagged "tax", in {data.year}.</Empty>
          ) : (
            <>
              {data.categories.length > 0 ? <Bars items={data.categories} share={false} /> : <span style={{ fontSize: 13 }}>None of these categories had spending in {data.year}.</span>}
              <div style={{ display: "flex", gap: 8, alignItems: "baseline", flexWrap: "wrap", padding: "8px 10px", borderRadius: 10, background: "var(--surface-2)", fontSize: 13 }}>
                <span className="lab">Tagged "tax"</span>
                {data.tagged_tax ? (
                  <span><strong className="num">{money(data.tagged_tax.total)}</strong> across {many(data.tagged_tax.count, "transaction", "transactions")}</span>
                ) : (
                  <span>Nothing tagged "tax" in {data.year}.</span>
                )}
              </div>
              <TableTwin>
                <div style={{ overflowX: "auto" }}>
                  <table className="ledger">
                    <thead><tr><th>What</th><th style={{ textAlign: "right" }}>Total</th><th style={{ textAlign: "right" }}>Transactions</th></tr></thead>
                    <tbody>{rows.map((i) => <tr key={i.name}><td>{i.name}</td><td className="num" style={{ textAlign: "right" }}>{money(i.total)}</td><td className="num" style={{ textAlign: "right", color: "var(--muted)" }}>{i.count}</td></tr>)}</tbody>
                  </table>
                </div>
              </TableTwin>
            </>
          )}
          <span style={{ fontSize: 13 }}>{data.note} <Link to="/transactions" style={{ fontWeight: 700 }}>Open Transactions</Link></span>
          <TaxPackParts data={data} />
          <div style={{ fontSize: 12, color: "var(--muted)" }}>{data.not_advice}</div>
        </div>
      )}
    </section>
  );
}
