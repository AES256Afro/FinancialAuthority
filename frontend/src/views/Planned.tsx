import { Link } from "react-router-dom";
import type { ViewProps } from "./index";
import { money } from "../format";

export function Planned({ snapshot }: ViewProps) {
  return (
    <div className="card" style={{ padding: 24, display: "flex", flexDirection: "column", gap: 10, maxWidth: 720 }}>
      <div className="lab">This view is drawn, not yet built</div>
      <h1 style={{ margin: 0, fontSize: 22, fontWeight: 800 }}>It arrives with the other home views in M7.6</h1>
      <p style={{ margin: 0, color: "var(--ink-2)" }}>
        The mock-up is on the design canvas and in the repository. Until it is built, the River shows the same numbers: safe to spend {money(snapshot.safe_to_spend.amount)} through {snapshot.safe_to_spend.through}.
      </p>
      <div>
        <Link to="/settings/appearance" className="btn dark" style={{ textDecoration: "none", display: "inline-block" }}>Pick another view</Link>
      </div>
    </div>
  );
}
