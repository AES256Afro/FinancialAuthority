import { describe, expect, it } from "vitest";
import { slipCheck } from "./slip";

describe("slipCheck", () => {
  it("asks about a dropped decimal point and offers it back", () => {
    expect(slipCheck("21580", 21580, 215.8)).toEqual({ value: 21580, maybe: 215.8 });
    expect(slipCheck("21580", 21580, 2275.18)).toEqual({ value: 21580, maybe: 215.8 });  // against the bank's stale figure too
  });
  it("lets an ordinary change through", () => {
    expect(slipCheck("215.80", 215.8, 2275.18)).toBeNull();  // a drop the bank's holds explain is not a slip
    expect(slipCheck("1546.96", 1546.96, 2275.18)).toBeNull();
    expect(slipCheck("300", 300, 215.8)).toBeNull();
    expect(slipCheck("900", 900, 0)).toBeNull();
  });
  it("asks about a far figure with a decimal point, without guessing", () => {
    expect(slipCheck("21580.00", 21580, 215.8)).toEqual({ value: 21580, maybe: null });
  });
  it("with nothing to compare, asks only about a large amount", () => {
    expect(slipCheck("12000", 12000, null)).toEqual({ value: 12000, maybe: 120 });
    expect(slipCheck("1200", 1200, null)).toBeNull();
  });
});
