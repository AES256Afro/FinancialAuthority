// Calm mode's Today: one card at a time, with Back and Next, a row of dots that shows where you are (each dot opens
// its card), and a button that reads the card aloud with the browser's own voice. Every card stays mounted, so
// nothing reloads as you move; a card with nothing to say this morning is skipped. With Hide amounts on, a figure
// is read as "an amount".
import { ArrowLeft, ArrowRight, Square, Volume2 } from "lucide-react";
import { useEffect, useRef, useState, type ReactNode } from "react";

export interface DeckCard {
  key: string;
  label: string;
  node: ReactNode;
}

function canSpeak(): boolean {
  return typeof window !== "undefined" && "speechSynthesis" in window && typeof SpeechSynthesisUtterance !== "undefined";
}

/** The words of a card as a listener should hear them: no hidden icons, and amounts withheld while hidden. */
export function spokenText(el: HTMLElement, quiet: boolean): string {
  const copy = el.cloneNode(true) as HTMLElement;
  copy.querySelectorAll('[aria-hidden="true"], button, input, select, textarea').forEach((n) => n.remove());
  if (quiet) copy.querySelectorAll(".num").forEach((n) => (n.textContent = " an amount "));
  return (copy.textContent ?? "").replace(/\s+/g, " ").trim();
}

export function CalmDeck({ cards, jumpTo }: { cards: DeckCard[]; jumpTo?: string }) {
  const [at, setAt] = useState(0);
  // Until the person moves, the deck stays on the first card that has something to say, even one that fills in
  // after the page opens (Getting started loads a moment later), so nothing is skipped unseen.
  const [moved, setMoved] = useState(false);
  const [filled, setFilled] = useState<boolean[]>([]);
  const [speaking, setSpeaking] = useState(false);
  const boxes = useRef<(HTMLDivElement | null)[]>([]);

  // Which cards have something to show; watched, since a card fills in when its data arrives.
  useEffect(() => {
    const check = () => {
      const next = cards.map((_, i) => ((boxes.current[i]?.textContent ?? "").trim().length > 0));
      setFilled((prev) => (prev.length === next.length && prev.every((v, i) => v === next[i]) ? prev : next));
    };
    check();
    const watch = new MutationObserver(check);
    boxes.current.forEach((el) => el && watch.observe(el, { childList: true, subtree: true, characterData: true }));
    return () => watch.disconnect();
  }, [cards]);

  // A link to one card of Today (#cover, #in-flight) opens that card.
  useEffect(() => {
    if (!jumpTo) return;
    const i = boxes.current.findIndex((el) => !!el?.querySelector(`#${CSS.escape(jumpTo)}`));
    if (i >= 0) {
      setAt(i);
      setMoved(true);
    }
  }, [jumpTo, filled]);

  const stop = () => {
    if (canSpeak()) window.speechSynthesis.cancel();
    setSpeaking(false);
  };
  useEffect(() => stop, []);

  const shown = cards.map((_, i) => i).filter((i) => filled[i] ?? true);
  const pos = moved ? Math.max(0, shown.indexOf(at)) : 0;
  const current = shown[pos] ?? 0;
  const go = (p: number) => {
    stop();
    const i = shown[Math.min(Math.max(p, 0), shown.length - 1)];
    if (i !== undefined) {
      setAt(i);
      setMoved(true);
    }
    window.scrollTo({ top: 0 });
  };
  const read = () => {
    const el = boxes.current[current];
    if (!el || !canSpeak()) return;
    stop();
    const say = new SpeechSynthesisUtterance(spokenText(el, document.documentElement.dataset.quiet === "1"));
    say.rate = 0.95;
    say.onend = () => setSpeaking(false);
    say.onerror = () => setSpeaking(false);
    setSpeaking(true);
    window.speechSynthesis.speak(say);
  };

  const nav = { display: "inline-flex", alignItems: "center", gap: 6, whiteSpace: "nowrap" as const };
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12 }}>
      <nav aria-label="Today's cards" style={{ display: "flex", flexDirection: "column", gap: 6 }}>
        <div style={{ display: "flex", flexWrap: "wrap", gap: 2 }}>
          {shown.map((i, p) => (
            <button key={cards[i].key} type="button" onClick={() => go(p)} aria-label={`Card ${p + 1} of ${shown.length}: ${cards[i].label}`} aria-current={i === current ? "step" : undefined}
              style={{ width: 26, height: 28, padding: 0, border: 0, background: "transparent", cursor: "pointer", display: "inline-flex", alignItems: "center", justifyContent: "center" }}>
              <span aria-hidden="true" style={{ width: i === current ? 14 : 10, height: i === current ? 14 : 10, borderRadius: 7, background: i === current ? "var(--ink)" : "transparent", border: "2px solid var(--ink-2)", boxSizing: "border-box" }} />
            </button>
          ))}
        </div>
        <div aria-live="polite" style={{ fontSize: 13, color: "var(--ink-2)" }}>
          <strong style={{ color: "var(--ink)" }}>{cards[current]?.label}</strong> · card {pos + 1} of {shown.length}
        </div>
      </nav>

      {cards.map((c, i) => (
        <div key={c.key} ref={(el) => { boxes.current[i] = el; }} hidden={i !== current} style={{ display: i === current ? "flex" : undefined, flexDirection: "column", gap: 14 }}>
          {c.node}
        </div>
      ))}

      <div style={{ display: "flex", flexWrap: "wrap", gap: 8, alignItems: "center" }}>
        <button type="button" className="btn" style={nav} onClick={() => go(pos - 1)} disabled={pos === 0}><ArrowLeft size={16} aria-hidden="true" /> Back</button>
        <button type="button" className="btn dark" style={nav} onClick={() => go(pos + 1)} disabled={pos >= shown.length - 1}>Next <ArrowRight size={16} aria-hidden="true" /></button>
        {canSpeak() && (
          speaking
            ? <button type="button" className="btn" style={{ ...nav, marginLeft: "auto" }} onClick={stop}><Square size={15} aria-hidden="true" /> Stop reading</button>
            : <button type="button" className="btn" style={{ ...nav, marginLeft: "auto" }} onClick={read}><Volume2 size={16} aria-hidden="true" /> Read this aloud</button>
        )}
      </div>
    </div>
  );
}
