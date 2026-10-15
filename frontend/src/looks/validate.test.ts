import { describe, expect, it } from "vitest";
import { defaultRoleColours, VALIDATED_ORDER } from "./index";
import { validatePalette } from "./validate";

describe("the paycheck palette", () => {
  it("passes in both modes in the order the parts sit", () => {
    for (const mode of ["light", "dark"] as const) {
      const colours = VALIDATED_ORDER.map((r) => defaultRoleColours(mode)[r]);
      const report = validatePalette(colours, mode);
      expect(report.ok, JSON.stringify(report.checks)).toBe(true);
      expect(report.checks.find((c) => c.name === "Colour-blind neighbours")?.state).toBe("pass");
    }
  });

  it("measures as the reference validator does", () => {
    // light, in part order: the reference reports the worst adjacent pair aqua and yellow at 9.1 (protan)
    const report = validatePalette(VALIDATED_ORDER.map((r) => defaultRoleColours("light")[r]), "light");
    expect(report.checks.find((c) => c.name === "Colour-blind neighbours")?.detail).toContain("9.1 apart");
    expect(report.checks.find((c) => c.name === "Neighbours, full colour")?.detail).toContain("19.6 apart");
  });

  it("refuses orange beside yellow, grey, and nonsense", () => {
    expect(validatePalette(["#eb6834", "#eda100"], "light").ok).toBe(false);
    expect(validatePalette(["#888888", "#2a78d6"], "light").ok).toBe(false);
    expect(validatePalette(["red", "#2a78d6"], "light").ok).toBe(false);
  });
});
