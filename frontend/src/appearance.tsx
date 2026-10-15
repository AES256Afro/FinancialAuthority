// Appearance at run time: loaded from the server once signed in, applied everywhere (the look, the owner's part
// colours, the name and mark, the type size, the density, quiet numbers), and saved back when it changes, so the
// phone and the desktop agree. Before it loads, this browser's last choice of look is used.
import { useEffect, useState } from "react";
import { api } from "./api";
import { DEMO } from "./demo/shim";
import { applyLook, setColourOverrides, type ColourOverrides, type LookId } from "./looks";
import { getPrefs, setPrefs } from "./prefs";
import { onShowAsChange, setServerShowAs } from "./showas";

export interface Section {
  key: string;
  show_as?: string | null;
  hidden?: boolean;
}

export interface Arrangement {
  id: string;
  name: string;
  sections: Section[];
}

export interface AppearanceState {
  look: LookId;
  active: string;
  arrangements: Arrangement[];
  show_as: Record<string, string>;
  colours: ColourOverrides | null;
  brand: { name: string; mark: string };
  type_size: "regular" | "larger" | "largest";
  density: "comfortable" | "compact";
  quiet: boolean;
  saved?: boolean;
}

const DEFAULT_ICON = `${import.meta.env.BASE_URL}favicon.svg`;

let current: AppearanceState = {
  look: getPrefs().look, active: getPrefs().view || "river", arrangements: [], show_as: {}, colours: null,
  brand: { name: "", mark: "" }, type_size: "regular", density: "comfortable", quiet: false,
};
const listeners = new Set<(a: AppearanceState) => void>();

export function appearance(): AppearanceState {
  return current;
}

export function brandName(): string {
  return current.brand.name || "FinancialAuthority";
}

export function brandMark(): string {
  return current.brand.mark || DEFAULT_ICON;
}

function apply(a: AppearanceState): void {
  const root = document.documentElement;
  applyLook(a.look);
  setColourOverrides(a.colours);
  root.dataset.size = a.type_size;
  root.dataset.density = a.density;
  if (a.quiet) root.dataset.quiet = "1";
  else delete root.dataset.quiet;
  setServerShowAs(a.saved ? a.show_as : null);
  document.title = a.brand.name || "FinancialAuthority";
  const icon = document.querySelector<HTMLLinkElement>("link[rel~='icon']");
  if (icon) icon.href = a.brand.mark || DEFAULT_ICON;
  setPrefs({ look: a.look });
}

export function setAppearance(a: AppearanceState): void {
  current = a;
  apply(a);
  listeners.forEach((l) => l(a));
}

export async function saveAppearance(next: AppearanceState): Promise<AppearanceState> {
  setAppearance({ ...next, saved: true });
  if (DEMO) return current;
  const { saved: _saved, ...body } = next;
  const out = await api<AppearanceState>("/api/v1/appearance", { method: "PUT", body: JSON.stringify(body) });
  setAppearance(out);
  return out;
}

export async function loadAppearance(): Promise<void> {
  try {
    const a = await api<AppearanceState>("/api/v1/appearance");
    setAppearance({ ...current, ...a, look: a.saved ? a.look : current.look, active: a.saved ? a.active : current.active });
  } catch {
    apply(current);
  }
}

export function useAppearance(): AppearanceState {
  const [a, setA] = useState(current);
  useEffect(() => {
    listeners.add(setA);
    return () => {
      listeners.delete(setA);
    };
  }, []);
  return a;
}

// A picture chosen on a section is kept with the appearance, a moment after the last change.
let timer: number | undefined;
onShowAsChange((map) => {
  window.clearTimeout(timer);
  timer = window.setTimeout(() => {
    saveAppearance({ ...current, show_as: map }).catch(() => undefined);
  }, 600);
});

// Quiet numbers: figures stay blurred until tapped or hovered; a tap shows one, a second tap hides it again.
if (typeof document !== "undefined") {
  document.addEventListener("click", (e) => {
    if (!document.documentElement.dataset.quiet) return;
    const el = (e.target as Element | null)?.closest?.(".num");
    if (el) el.classList.toggle("shown");
  });
}
