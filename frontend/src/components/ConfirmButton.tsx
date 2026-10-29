// An action that cannot be taken back asks first, in place: the button becomes the question and two answers.
// A failure is said next to it, never swallowed. With `open`, the question is shown by the caller (after a choice
// in a select, say) and has no button of its own; "no" calls `onCancel`.
import { useEffect, useRef, useState, type CSSProperties, type KeyboardEvent } from "react";

export function ConfirmButton({
  label,
  question,
  yes,
  no = "Keep it",
  onConfirm,
  className = "btn",
  style,
  open,
  onCancel,
}: {
  label: string;
  question: string;
  yes: string;
  no?: string;
  onConfirm: () => Promise<unknown> | unknown;
  className?: string;
  style?: CSSProperties;
  open?: boolean;
  onCancel?: () => void;
}) {
  const [asking, setAsking] = useState(false);
  const controlled = open !== undefined;
  const shown = controlled ? open : asking;
  const cancel = () => (controlled ? onCancel?.() : setAsking(false));
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  // Focus goes back to the button that asked once the question closes, so a keyboard or a screen reader is not lost.
  const trigger = useRef<HTMLButtonElement>(null);
  const wasShown = useRef(false);
  useEffect(() => {
    if (wasShown.current && !shown) trigger.current?.focus();
    wasShown.current = Boolean(shown);
  }, [shown]);
  const onKey = (e: KeyboardEvent) => {
    if (e.key === "Escape" && !busy) {
      e.stopPropagation();
      cancel();
    }
  };
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
  if (!shown) {
    if (controlled) return error ? <span role="alert" style={{ fontSize: 12, color: "var(--critical-ink)", fontWeight: 700 }}>{error}</span> : null;
    return (
      <span style={{ display: "inline-flex", flexDirection: "column", gap: 4, alignItems: "flex-start" }}>
        <button ref={trigger} type="button" className={className} style={style} onClick={() => setAsking(true)}>{label}</button>
        {error && <span role="alert" style={{ fontSize: 12, color: "var(--critical-ink)", fontWeight: 700 }}>{error}</span>}
      </span>
    );
  }
  return (
    <span role="group" aria-label={question} onKeyDown={onKey} style={{ display: "inline-flex", flexWrap: "wrap", alignItems: "center", gap: 8, padding: "6px 10px", borderRadius: 10, background: "var(--warning-soft)", color: "var(--warning-ink)", fontSize: 13, fontWeight: 700 }}>
      <span>{question}</span>
      <button type="button" className="btn dark" disabled={busy} onClick={go} style={{ minHeight: 32, padding: "5px 12px" }}>{busy ? "Working…" : yes}</button>
      <button type="button" className="btn" disabled={busy} onClick={cancel} style={{ minHeight: 32, padding: "5px 12px" }} autoFocus>{no}</button>
      {error && <span role="alert" style={{ flexBasis: "100%", color: "var(--critical-ink)" }}>{error}</span>}
    </span>
  );
}
