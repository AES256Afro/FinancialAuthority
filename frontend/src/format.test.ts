import { describe, expect, it } from "vitest";
import { daysWord, money, monthDay, shortDate } from "./format";

describe("money", () => {
  it("formats whole dollars with a true minus sign", () => {
    expect(money(2240)).toBe("$2,240");
    expect(money(-231)).toBe("−$231");
    expect(money(2240, { sign: true })).toBe("+$2,240");
    expect(money(142.17, { cents: true })).toBe("$142.17");
    expect(money(-1234.5, { cents: true })).toBe("−$1,234.50");
    expect(money(1e15)).toBe("$1,000,000,000,000,000");
  });
  it("never says minus nothing or NaN", () => {
    expect(money(-0)).toBe("$0");
    expect(money(-0.4)).toBe("$0");
    expect(money(-0.004, { cents: true })).toBe("$0.00");
    expect(money(-0.5)).toBe("−$1");
    expect(money(Number.NaN)).toBe("$0");
    expect(money(undefined as unknown as number, { cents: true })).toBe("$0.00");
  });
});

describe("dates", () => {
  it("names the day and month without time zones interfering", () => {
    expect(shortDate("2026-10-16")).toBe("Fri Oct 16");
    expect(monthDay("2026-11-01")).toBe("Nov 1");
    expect(daysWord(0)).toBe("today");
    expect(daysWord(2)).toBe("in 2 days");
  });
});

describe("typed and server values", () => {
  it("reads a typed amount the way people type it", async () => {
    const { parseMoney } = await import("./format");
    expect(parseMoney("$1,200.50")).toBe(1200.5);
    expect(parseMoney("120")).toBe(120);
    expect(parseMoney("12.345")).toBeNull();
    expect(parseMoney("abc")).toBeNull();
  });
  it("reads a server time without a zone as UTC and says it with the weekday", async () => {
    const { when } = await import("./format");
    expect(when(null)).toBe("never");
    expect(when("2026-10-16")).toBe("Fri Oct 16");
    expect(when("2026-10-16T12:00:00")).toMatch(/^(Fri|Thu|Sat) Oct 1[5-7], \d{1,2}:\d{2} (am|pm)$/);
  });
});
