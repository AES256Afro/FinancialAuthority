// An action that cannot be taken back asks first, in place: the button becomes the question and two answers.
// A failure is said next to it, never swallowed.
import { useState, type CSSProperties } from "react";

export function ConfirmButton({
  label,
  question,
  yes,
  no = "Keep it",
  onConfirm,
  className = "btn",
  style,
}: {
  label: string;
  question: string;
  yes: string;
  no?: string;
  onConfirm: () => Promise<unknown> | unknown;
  className?: string;
  style?: CSSProperties;
}) {
  const [asking, setAsking] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  async function go() {
    setBusy(true);
    setError(null);
    try {
      await onConfirm();
      setAsking(false);
    } catch (e) {
      setError((e as Error).message || "That did not work. Try again.");
    } finally {
      setBusy(false);
    }
  }
  if (!asking) {
    return (
      <span style={{ display: "inline-flex", flexDirection: "column", gap: 4, alignItems: "flex-start" }}>
        <button type="button" className={className} style={style} onClick={() => setAsking(true)}>{label}</button>
        {error && <span role="alert" style={{ fontSize: 12, color: "var(--critical-ink)", fontWeight: 700 }}>{error}</span>}
      </span>
    );
  }
  return (
    <span role="group" aria-label={question} style={{ display: "inline-flex", flexWrap: "wrap", alignItems: "center", gap: 8, padding: "6px 10px", borderRadius: 10, background: "var(--warning-soft)", color: "var(--warning-ink)", fontSize: 13, fontWeight: 700 }}>
      <span>{question}</span>
      <button type="button" className="btn dark" disabled={busy} onClick={go} style={{ minHeight: 32, padding: "5px 12px" }}>{busy ? "Working…" : yes}</button>
      <button type="button" className="btn" disabled={busy} onClick={() => setAsking(false)} style={{ minHeight: 32, padding: "5px 12px" }} autoFocus>{no}</button>
      {error && <span role="alert" style={{ flexBasis: "100%", color: "var(--critical-ink)" }}>{error}</span>}
    </span>
  );
}
