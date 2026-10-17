// A balance typed off the bank's screen that is far from what the app holds is checked before it counts: the usual
// slip is a dropped decimal point (21580 for 215.80), which turns a thin week into a rich one.

export interface Slip {
  /** What was typed, as read. */
  value: number;
  /** The amount with the decimal point put back, when none was typed and that reading is plausible. */
  maybe: number | null;
}

/** Whether `value` (read from `text`) is far enough above `current` to ask first: over $500 more and five times as
 * big. Only upward: a lower figure is what holds and spending make, and counting less is the safe side. With no
 * figure to compare, only $10,000 or more is asked about. */
export function slipCheck(text: string, value: number, current: number | null | undefined): Slip | null {
  const size = Math.abs(value);
  const far = current === null || current === undefined
    ? size >= 10_000
    : value - current > 500 && size >= 5 * Math.max(Math.abs(current), 200);
  if (!far) return null;
  const noPoint = !text.includes(".") && size >= 100;
  return { value, maybe: noPoint ? Math.round(value) / 100 : null };
}
