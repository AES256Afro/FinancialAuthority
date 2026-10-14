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

export function applyLook(id: LookId): void {
  document.documentElement.dataset.look = id;
}

/** The chart series colours of a look, validated in both modes. */
export function palette(id: LookId) {
  const dark = id !== "light";
  return {
    paycheck: dark ? "#3987e5" : "#2a78d6",
    bill: dark ? "#d95926" : "#eb6834",
    safe: dark ? "#199e70" : "#1baf7a",
    debt: dark ? "#9085e9" : "#4a3aa7",
    buffer: dark ? "#3a3f49" : "#c3c2b7",
    surface: dark ? (id === "console" ? "#14171c" : "#1b1e24") : "#ffffff",
    ink: dark ? "#f2f2ee" : "#141518",
    ink2: dark ? "#b9bcc6" : "#4e5058",
    muted: "#85878f",
    hairline: dark ? "#2a2e36" : "#e4e1da",
  };
}
