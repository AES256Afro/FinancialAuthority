import { describe, expect, it } from "vitest";
import { daysWord, money, monthDay, shortDate } from "./format";

describe("money", () => {
  it("formats whole dollars with a true minus sign", () => {
    expect(money(2240)).toBe("$2,240");
    expect(money(-231)).toBe("−$231");
    expect(money(2240, { sign: true })).toBe("+$2,240");
    expect(money(142.17, { cents: true })).toBe("$142.17");
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
