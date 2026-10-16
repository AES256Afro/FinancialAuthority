// Looks are themes. Registering one here is all it takes to appear in Settings › Appearance.
export type LookId = "light" | "dark" | "console";

export interface Look {
  id: LookId;
  name: string;
  description: string;
  swatch: { ground: string; ink: string; accent: string };
}

export const LOOKS: Look[] = [
  { id: "light", name: "Paper", description: "Warm paper, ink text. The default.", swatch: { ground: "#f6f4ef", ink: "#141518", accent: "#1baf7a" } },
  { id: "dark", name: "Night", description: "The same shapes on a dark ground.", swatch: { ground: "#121417", ink: "#f2f2ee", accent: "#199e70" } },
  { id: "console", name: "Console", description: "Darker still, squarer corners, a monospace face.", swatch: { ground: "#0b0d10", ink: "#f2f2ee", accent: "#199e70" } },
];

// The colour roles the owner may change, each tied to the palette slot it replaces and the CSS token it sets.
export type Role = "housing" | "bills" | "card" | "later" | "aside" | "saving" | "yours";
export const ROLES: { role: Role; label: string; slot: keyof ReturnType<typeof basePalette>; token: string }[] = [
  { role: "housing", label: "Housing", slot: "housing", token: "--housing" },
  { role: "bills", label: "Bills", slot: "bill", token: "--bill" },
  { role: "card", label: "Card payment", slot: "debt", token: "--debt" },
  { role: "later", label: "Pay-later and advances", slot: "later", token: "--later" },
  { role: "aside", label: "Set aside", slot: "buffer", token: "--buffer" },
  { role: "saving", label: "Saving", slot: "saving", token: "--saving" },
  { role: "yours", label: "Yours", slot: "safe", token: "--safe" },
];
/** The categorical order the validator checks: set aside is a neutral and sits outside it. */
export const VALIDATED_ORDER: Role[] = ["housing", "bills", "card", "later", "saving", "yours"];

export type ColourOverrides = Partial<Record<"light" | "dark", Partial<Record<Role, string>>>>;
let overrides: ColourOverrides | null = null;

export function setColourOverrides(next: ColourOverrides | null): void {
  overrides = next;
  const root = document.documentElement;
  const mode = (root.dataset.look ?? "light") === "light" ? "light" : "dark";
  for (const r of ROLES) {
    const v = next?.[mode]?.[r.role];
    if (v) root.style.setProperty(r.token, v);
    else root.style.removeProperty(r.token);
  }
}

export function applyLook(id: LookId): void {
  document.documentElement.dataset.look = id;
  setColourOverrides(overrides);
}

function basePalette(id: LookId) {
  const dark = id !== "light";
  return {
    paycheck: dark ? "#3987e5" : "#2a78d6",
    bill: dark ? "#d95926" : "#eb6834",
    safe: dark ? "#199e70" : "#1baf7a",
    debt: dark ? "#9085e9" : "#4a3aa7",
    buffer: dark ? "#3a3f49" : "#c3c2b7",
    // The parts of a paycheck, in their fixed order (housing, bills, card, pay-later, set aside, saving, yours).
    // Adjacent pairs pass the colour-blind validator in both modes; every picture also labels each part.
    housing: dark ? "#3987e5" : "#2a78d6",
    later: dark ? "#d55181" : "#e87ba4",
    saving: dark ? "#c98500" : "#eda100",
    surface: dark ? (id === "console" ? "#14171c" : "#1b1e24") : "#ffffff",
    ink: dark ? "#f2f2ee" : "#141518",
    ink2: dark ? "#b9bcc6" : "#4e5058",
    muted: dark ? "#9a9ea8" : "#6b6d75",
    hairline: dark ? "#2a2e36" : "#e4e1da",
  };
}

/** The validated default colour of each role in a mode. */
export function defaultRoleColours(mode: "light" | "dark"): Record<Role, string> {
  const p = basePalette(mode === "light" ? "light" : "dark");
  return Object.fromEntries(ROLES.map((r) => [r.role, p[r.slot]])) as Record<Role, string>;
}

/** The chart colours of a look: the validated palette, with the owner's own role colours where they passed. */
export function palette(id: LookId) {
  const p = basePalette(id);
  const mine = overrides?.[id === "light" ? "light" : "dark"];
  if (mine) for (const r of ROLES) if (mine[r.role]) (p as Record<string, string>)[r.slot] = mine[r.role] as string;
  return p;
}
