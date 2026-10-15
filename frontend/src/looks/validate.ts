// The palette checks a chart palette must pass, computed rather than eyeballed: lightness band and chroma floor
// in OKLCH, separation between neighbouring parts under simulated protanopia and deuteranopia (Machado,
// Oliveira and Fernandes 2009, severity 1) and under normal vision, as OKLab distance ×100, and WCAG contrast
// against the chart surface. A colour the owner picks for a part is checked here before it can be saved.

export type Mode = "light" | "dark";
export type State = "pass" | "warn" | "fail";

const BAND: Record<Mode, [number, number]> = { light: [0.43, 0.77], dark: [0.48, 0.67] };
const CHROMA_FLOOR = 0.1;
const CVD_TARGET = 8;
const CVD_FLOOR = 6;
const NORMAL_FLOOR = 15;
const CONTRAST_MIN = 3;
export const SURFACE: Record<Mode, string> = { light: "#fcfcfb", dark: "#1a1a19" };

const MACHADO: Record<"protan" | "deutan", number[][]> = {
  protan: [[0.152286, 1.052583, -0.204868], [0.114503, 0.786281, 0.099216], [-0.003882, -0.048116, 1.051998]],
  deutan: [[0.367322, 0.860646, -0.227968], [0.280085, 0.672501, 0.047413], [-0.01182, 0.04294, 0.968881]],
};

export function isHex(v: string): boolean {
  return /^#?[0-9a-fA-F]{6}$/.test(v.trim());
}

function srgb(hex: string): [number, number, number] {
  const h = hex.trim().replace("#", "");
  return [0, 2, 4].map((i) => parseInt(h.slice(i, i + 2), 16) / 255) as [number, number, number];
}

function toLin(c: number): number {
  return c <= 0.04045 ? c / 12.92 : ((c + 0.055) / 1.055) ** 2.4;
}

function lin(hex: string): [number, number, number] {
  return srgb(hex).map(toLin) as [number, number, number];
}

function oklab([r, g, b]: [number, number, number]): [number, number, number] {
  const l = Math.cbrt(0.4122214708 * r + 0.5363325363 * g + 0.0514459929 * b);
  const m = Math.cbrt(0.2119034982 * r + 0.6806995451 * g + 0.1073969566 * b);
  const s = Math.cbrt(0.0883024619 * r + 0.2817188376 * g + 0.6299787005 * b);
  return [0.2104542553 * l + 0.793617785 * m - 0.0040720468 * s, 1.9779984951 * l - 2.428592205 * m + 0.4505937099 * s, 0.0259040371 * l + 0.7827717662 * m - 0.808675766 * s];
}

function simulate(hex: string, kind: "protan" | "deutan"): [number, number, number] {
  const [r, g, b] = lin(hex);
  return MACHADO[kind].map((row) => Math.max(0, Math.min(1, row[0] * r + row[1] * g + row[2] * b))) as [number, number, number];
}

function deltaE(a: string, b: string, kind?: "protan" | "deutan"): number {
  const x = oklab(kind ? simulate(a, kind) : lin(a));
  const y = oklab(kind ? simulate(b, kind) : lin(b));
  return 100 * Math.hypot(x[0] - y[0], x[1] - y[1], x[2] - y[2]);
}

export function oklch(hex: string): { L: number; C: number } {
  const [L, a, b] = oklab(lin(hex));
  return { L, C: Math.hypot(a, b) };
}

function luminance(hex: string): number {
  const [r, g, b] = lin(hex);
  return 0.2126 * r + 0.7152 * g + 0.0722 * b;
}

export function contrast(a: string, b: string): number {
  const [hi, lo] = [luminance(a), luminance(b)].sort((x, y) => y - x);
  return (hi + 0.05) / (lo + 0.05);
}

export interface Check {
  name: string;
  state: State;
  detail: string;
}

export interface Report {
  mode: Mode;
  ok: boolean;
  checks: Check[];
}

/** Validate colours in the order they sit side by side (adjacent pairs). Nothing below the hard floors passes. */
export function validatePalette(colours: string[], mode: Mode, surface = SURFACE[mode]): Report {
  const bad = colours.filter((c) => !isHex(c));
  if (bad.length) return { mode, ok: false, checks: [{ name: "Colours", state: "fail", detail: `not a #rrggbb colour: ${bad.join(", ")}` }] };
  const [lo, hi] = BAND[mode];
  const checks: Check[] = [];
  const off = colours.filter((c) => { const { L } = oklch(c); return L < lo || L > hi; });
  checks.push({ name: "Lightness", state: off.length ? "fail" : "pass", detail: off.length ? `too ${mode === "light" ? "light or dark" : "dark or bright"} for this mode: ${off.join(", ")}` : `all within the ${mode} band` });
  const grey = colours.filter((c) => oklch(c).C < CHROMA_FLOOR);
  checks.push({ name: "Colourfulness", state: grey.length ? "fail" : "pass", detail: grey.length ? `reads as grey: ${grey.join(", ")}` : "every colour reads as a colour" });
  let worst = { d: Infinity, a: "", b: "", kind: "" };
  let normal = { d: Infinity, a: "", b: "" };
  for (let i = 0; i < colours.length - 1; i++) {
    for (const kind of ["protan", "deutan"] as const) {
      const d = deltaE(colours[i], colours[i + 1], kind);
      if (d < worst.d) worst = { d, a: colours[i], b: colours[i + 1], kind };
    }
    const n = deltaE(colours[i], colours[i + 1]);
    if (n < normal.d) normal = { d: n, a: colours[i], b: colours[i + 1] };
  }
  if (colours.length > 1) {
    const kindWord = worst.kind === "protan" ? "red-blind" : "green-blind";
    checks.push({ name: "Colour-blind neighbours", state: worst.d >= CVD_TARGET ? "pass" : worst.d >= CVD_FLOOR ? "warn" : "fail", detail: `closest neighbours ${worst.a} and ${worst.b}, ${worst.d.toFixed(1)} apart for ${kindWord} readers (8 or more passes; 6 to 8 needs labels, which every picture has)` });
    checks.push({ name: "Neighbours, full colour", state: normal.d >= NORMAL_FLOOR ? "pass" : "fail", detail: `closest neighbours ${normal.a} and ${normal.b}, ${normal.d.toFixed(1)} apart (15 or more passes)` });
  }
  const dim = colours.filter((c) => contrast(c, surface) < CONTRAST_MIN);
  checks.push({ name: "Contrast with the page", state: dim.length ? "warn" : "pass", detail: dim.length ? `under 3:1 against the chart surface, so the pictures keep their labels and table: ${dim.join(", ")}` : "every colour stands out from the page" });
  return { mode, ok: checks.every((c) => c.state !== "fail"), checks };
}
