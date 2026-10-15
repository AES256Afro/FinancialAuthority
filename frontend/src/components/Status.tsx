// Status always ships with an icon and a word, never colour alone. The three states are reserved: they are
// never used as a series colour.
import { CircleCheck, CircleHelp, OctagonAlert, TriangleAlert } from "lucide-react";
import { useState, type ReactNode } from "react";
import type { Status } from "../guide";

const LOOK: Record<Status, { bg: string; ink: string; word: string; Icon: typeof CircleCheck }> = {
  good: { bg: "var(--safe-soft)", ink: "var(--safe-ink)", word: "On track", Icon: CircleCheck },
  warn: { bg: "var(--warning-soft)", ink: "var(--warning-ink)", word: "Watch", Icon: TriangleAlert },
  crit: { bg: "var(--critical-soft)", ink: "var(--critical)", word: "Act", Icon: OctagonAlert },
};

export function StatusPill({ status, children }: { status: Status; children?: ReactNode }) {
  const l = LOOK[status];
  return (
    <span style={{ display: "inline-flex", alignItems: "center", gap: 6, padding: "3px 9px", borderRadius: children ? 12 : 999, background: l.bg, color: l.ink, fontSize: 12, fontWeight: 800, whiteSpace: children ? "normal" : "nowrap", maxWidth: "100%", boxSizing: "border-box" }}>
      <l.Icon size={14} strokeWidth={2.2} aria-hidden="true" style={{ flexShrink: 0 }} />
      {children ?? l.word}
    </span>
  );
}

export function statusInk(status: Status): string {
  return LOOK[status].ink;
}

// "Why this number": the engine's own sentence, one tap away from the figure it explains.
export function Why({ text, label = "Why this number" }: { text?: string | null; label?: string }) {
  const [open, setOpen] = useState(false);
  if (!text) return null;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 6, alignItems: "flex-start" }}>
      <button type="button" onClick={() => setOpen((o) => !o)} aria-expanded={open} style={{ display: "inline-flex", alignItems: "center", gap: 5, background: "none", border: 0, padding: 0, color: "var(--paycheck-ink)", font: "inherit", fontSize: 12, fontWeight: 700, cursor: "pointer" }}>
        <CircleHelp size={14} aria-hidden="true" /> {open ? "Hide why" : label}
      </button>
      {open && <div style={{ fontSize: 13, color: "var(--ink-2)", background: "var(--surface-2)", borderRadius: 10, padding: "8px 10px", maxWidth: "70ch" }}>{text}</div>}
    </div>
  );
}
