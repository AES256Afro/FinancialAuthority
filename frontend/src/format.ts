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

export function daysWord(n: number): string {
  if (n === 0) return "today";
  if (n === 1) return "tomorrow";
  return `in ${n} days`;
}

export function ordinal(n: number): string {
  const tail = n % 100 >= 11 && n % 100 <= 13 ? "th" : ({ 1: "st", 2: "nd", 3: "rd" } as Record<number, string>)[n % 10] ?? "th";
  return `${n}${tail}`;
}
