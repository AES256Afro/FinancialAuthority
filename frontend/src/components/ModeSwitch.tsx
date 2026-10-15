// A row of choices for how a section is drawn. The same numbers in every mode.
export function ModeSwitch<T extends string>({ modes, value, onChange, label }: { modes: { id: T; name: string }[]; value: T; onChange: (v: T) => void; label: string }) {
  return (
    <div role="radiogroup" aria-label={label} style={{ display: "inline-flex", gap: 4, flexWrap: "wrap", background: "var(--surface-2)", padding: 3, borderRadius: 10 }}>
      {modes.map((m) => (
        <button key={m.id} type="button" role="radio" aria-checked={value === m.id} onClick={() => onChange(m.id)}
          style={{ font: "inherit", fontSize: 12, fontWeight: 800, padding: "6px 10px", borderRadius: 8, border: 0, cursor: "pointer", minHeight: 32, background: value === m.id ? "var(--surface)" : "transparent", color: value === m.id ? "var(--ink)" : "var(--ink-2)", boxShadow: value === m.id ? "0 1px 2px rgba(0,0,0,.12)" : "none" }}>
          {m.name}
        </button>
      ))}
    </div>
  );
}
