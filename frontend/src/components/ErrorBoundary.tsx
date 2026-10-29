// A page that breaks shows a plain card in its place, never a blank app: what went wrong, a Try again that opens
// the page afresh, and the way to Today. The header and the menu stay, so every other page is still one tap away.
import { OctagonAlert } from "lucide-react";
import { Component, Fragment, type ErrorInfo, type ReactNode } from "react";
import { Link } from "react-router-dom";

// A page's code that could not be fetched (the app was updated since this tab opened) only comes back with a reload.
const STALE_CODE = /dynamically imported module|importing a module script failed|loading chunk/i;

interface Props {
  children: ReactNode;
  /** Opening another page (a new path) clears the problem, so the next page is not hidden behind this one's card. */
  resetKey?: string;
}

interface State {
  error: Error | null;
  /** Bumped by Try again: a new key mounts the page afresh. */
  attempt: number;
}

export class ErrorBoundary extends Component<Props, State> {
  state: State = { error: null, attempt: 0 };

  static getDerivedStateFromError(error: unknown): Partial<State> {
    return { error: error instanceof Error ? error : new Error(String(error)) };
  }

  componentDidCatch(error: Error, info: ErrorInfo) {
    console.error("A page hit a problem", error, info.componentStack);
  }

  componentDidUpdate(prev: Props) {
    if (this.state.error && prev.resetKey !== this.props.resetKey) this.setState((s) => ({ error: null, attempt: s.attempt + 1 }));
  }

  private again = () => {
    if (this.state.error && STALE_CODE.test(this.state.error.message)) {
      // Still offline: a reload would lose the shell too, so the card stays until the network is back.
      if (navigator.onLine) window.location.reload();
      return;
    }
    this.setState((s) => ({ error: null, attempt: s.attempt + 1 }));
  };

  render() {
    const { error, attempt } = this.state;
    if (!error) return <Fragment key={attempt}>{this.props.children}</Fragment>;
    // A page's code that could not be fetched while offline: say that, not the browser's words.
    const message = STALE_CODE.test(error.message) && !navigator.onLine
      ? "this page needs the network to open, and this device is offline. Check the connection"
      : (error.message || "something went wrong").trim().replace(/[.\s]+$/, "");
    return (
      <div role="alert" className="card" style={{ padding: 14, display: "flex", alignItems: "flex-start", gap: 10, flexWrap: "wrap", fontSize: 13, background: "var(--critical-soft)", color: "var(--critical-ink)", maxWidth: 760 }}>
        <OctagonAlert size={16} aria-hidden="true" style={{ flexShrink: 0, marginTop: 1 }} />
        <span style={{ flex: "1 1 220px", minWidth: 0, overflowWrap: "anywhere" }}>
          <strong>This page hit a problem:</strong> {message}. Try again, or go to <Link to="/today" style={{ fontWeight: 700 }}>Today</Link>.
        </span>
        <button type="button" className="btn" onClick={this.again} style={{ minHeight: 32, padding: "5px 12px" }}>Try again</button>
      </div>
    );
  }
}
