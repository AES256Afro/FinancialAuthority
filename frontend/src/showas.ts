// Which picture a section shows. Kept with the arrangement (Settings › Appearance) on the server once one is
// saved, and in this browser meanwhile, so a choice made on a section sticks.
import { useState } from "react";

const KEY = "fa.showas.v1";
const listeners = new Set<() => void>();
let serverCopy: Record<string, string> | null = null;

function local(): Record<string, string> {
  try {
    return JSON.parse(localStorage.getItem(KEY) || "{}") as Record<string, string>;
  } catch {
    return {};
  }
}

export function showAsMap(): Record<string, string> {
  return { ...local(), ...(serverCopy ?? {}) };
}

export function setServerShowAs(map: Record<string, string> | null): void {
  serverCopy = map;
  listeners.forEach((l) => l());
}

export function getShowAs<T extends string>(section: string, fallback: T, allowed?: readonly string[]): T {
  const v = showAsMap()[section];
  return (v && (!allowed || allowed.includes(v)) ? v : fallback) as T;
}

export function saveShowAs(section: string, value: string, persist?: (map: Record<string, string>) => void): void {
  const next = { ...local(), [section]: value };
  try {
    localStorage.setItem(KEY, JSON.stringify(next));
  } catch {
    // storage unavailable: the choice lasts for this page
  }
  if (serverCopy) serverCopy = { ...serverCopy, [section]: value };
  persist?.(showAsMap());
  listeners.forEach((l) => l());
}

export function useShowAs<T extends string>(section: string, fallback: T, allowed?: readonly string[]): [T, (v: T) => void] {
  const [value, setValue] = useState<T>(() => getShowAs(section, fallback, allowed));
  return [value, (v: T) => {
    setValue(v);
    saveShowAs(section, v, persistHook);
  }];
}

let persistHook: ((map: Record<string, string>) => void) | undefined;
export function onShowAsChange(fn: (map: Record<string, string>) => void): void {
  persistHook = fn;
}
