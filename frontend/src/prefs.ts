// Per-device preferences: the home view and the look. Kept in this browser only, as BoxPilot does, so a
// phone and a wall screen can differ. Every read and write is guarded: storage can be absent or blocked.
import type { LookId } from "./looks";

export interface Prefs {
  view: string;
  look: LookId;
}

const KEY = "fa.prefs.v1";
const DEFAULTS: Prefs = { view: "river", look: "light" };

export function getPrefs(): Prefs {
  try {
    const raw = localStorage.getItem(KEY);
    if (!raw) return { ...DEFAULTS };
    const parsed = JSON.parse(raw) as Partial<Prefs>;
    return { ...DEFAULTS, ...parsed };
  } catch {
    return { ...DEFAULTS };
  }
}

export function setPrefs(update: Partial<Prefs>): Prefs {
  const next = { ...getPrefs(), ...update };
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // storage unavailable: the choice lasts for this page only
  }
  return next;
}
