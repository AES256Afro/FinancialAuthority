// Money and date formatting. Numbers are formatted here and nowhere else.

export function money(value: number, opts: { sign?: boolean; cents?: boolean } = {}): string {
  const abs = Math.abs(value);
  const text = abs.toLocaleString("en-US", { minimumFractionDigits: opts.cents ? 2 : 0, maximumFractionDigits: opts.cents ? 2 : 0 });
  if (value < 0) return `−$${text}`;
  return `${opts.sign ? "+" : ""}$${text}`;
}

const DAYS = ["Sun", "Mon", "Tue", "Wed", "Thu", "Fri", "Sat"];
const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function parseIso(iso: string): Date {
  const [y, m, d] = iso.split("-").map(Number);
  return new Date(y, m - 1, d);
}

export function shortDate(iso: string): string {
  const d = parseIso(iso);
  return `${DAYS[d.getDay()]} ${MONTHS[d.getMonth()]} ${d.getDate()}`;
}

export function monthDay(iso: string): string {
  const d = parseIso(iso);
  return `${MONTHS[d.getMonth()]} ${d.getDate()}`;
}

export function longDate(iso: string): string {
  const d = parseIso(iso);
  const days = ["Sunday", "Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday"];
  const months = ["January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December"];
  return `${days[d.getDay()]}, ${months[d.getMonth()]} ${d.getDate()}`;
}

/** A moment from the server in local time with the weekday: "Wed Oct 15, 2:04 pm". A plain date gets the
 * weekday only. The server writes UTC; a time without a zone is read as UTC. */
export function when(value: string | null | undefined): string {
  if (!value) return "never";
  if (/^\d{4}-\d{2}-\d{2}$/.test(value)) return shortDate(value);
  const zoned = /[zZ]|[+-]\d{2}:?\d{2}$/.test(value) ? value : `${value}Z`;
  const d = new Date(zoned);
  if (Number.isNaN(d.getTime())) return value;
  const hour = d.getHours() % 12 || 12;
  const minute = String(d.getMinutes()).padStart(2, "0");
  return `${DAYS[d.getDay()]} ${MONTHS[d.getMonth()]} ${d.getDate()}, ${hour}:${minute} ${d.getHours() < 12 ? "am" : "pm"}`;
}

/** Today as the owner's local date, YYYY-MM-DD (never the UTC date, which is tomorrow on a US evening). */
export function localToday(): string {
  const d = new Date();
  return `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
}

/** An amount typed by a person: "$1,200.50" and "1200.5" both read; anything else is null. */
export function parseMoney(text: string): number | null {
  const clean = text.replace(/[$,\s]/g, "");
  if (!/^-?\d+(\.\d{1,2})?$/.test(clean)) return null;
  const n = Number(clean);
  return Number.isFinite(n) ? n : null;
}

export function daysWord(n: number): string {
  if (n === 0) return "today";
  if (n === 1) return "tomorrow";
  return `in ${n} days`;
}

export function ordinal(n: number): string {
  const tail = n % 100 >= 11 && n % 100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th";
  return `${n}${tail}`;
}

export function monthYear(iso: string | null): string {
  if (!iso) return "not within six years";
  const d = parseIso(iso);
  return `${MONTHS[d.getMonth()]} ${d.getFullYear()}`;
}
