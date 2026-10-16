// A row of choices for how a section is drawn. The same numbers in every mode.
// It is a radio group: Tab reaches the chosen one, the arrow keys move between them.
import { useRef, type KeyboardEvent } from "react";

export function ModeSwitch<T extends string>({ modes, value, onChange, label }: { modes: { id: T; name: string }[]; value: T; onChange: (v: T) => void; label: string }) {
  const refs = useRef<(HTMLButtonElement | null)[]>([]);
  const current = Math.max(0, modes.findIndex((m) => m.id === value));
  function onKey(e: KeyboardEvent<HTMLDivElement>) {
    const step = e.key === "ArrowRight" || e.key === "ArrowDown" ? 1 : e.key === "ArrowLeft" || e.key === "ArrowUp" ? -1 : 0;
    const to = e.key === "Home" ? 0 : e.key === "End" ? modes.length - 1 : step ? (current + step + modes.length) % modes.length : -1;
    if (to < 0) return;
    e.preventDefault();
    onChange(modes[to].id);
    refs.current[to]?.focus();
  }
  return (
    <div role="radiogroup" aria-label={label} onKeyDown={onKey} style={{ display: "inline-flex", gap: 4, flexWrap: "wrap", background: "var(--surface-2)", padding: 3, borderRadius: 10 }}>
      {modes.map((m, k) => (
        <button key={m.id} ref={(el) => { refs.current[k] = el; }} type="button" role="radio" aria-checked={value === m.id} tabIndex={k === current ? 0 : -1} onClick={() => onChange(m.id)}
          style={{ font: "inherit", fontSize: 12, fontWeight: 800, padding: "6px 10px", borderRadius: 8, border: 0, cursor: "pointer", minHeight: 32, background: value === m.id ? "var(--surface)" : "transparent", color: value === m.id ? "var(--ink)" : "var(--ink-2)", boxShadow: value === m.id ? "0 1px 2px rgba(0,0,0,.12)" : "none" }}>
          {m.name}
        </button>
      ))}
    </div>
  );
}
