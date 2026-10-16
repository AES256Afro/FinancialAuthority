// The paycheck section: eight ways to draw the same split, picked per section and remembered. Every one draws
// GET /api/v1/paychecks; none computes a figure of its own.
import { useState } from "react";
import { useApi } from "../api";
import { ByCheck } from "../components/ByCheck";
import { ModeSwitch } from "../components/ModeSwitch";
import { Failed, Loading } from "../components/State";
import { money } from "../format";
import type { PaycheckViews } from "../paychecks";
import { useShowAs } from "../showas";
import type { Snapshot } from "../types";
import { Beads } from "./Beads";
import { checkTitle, Legend, TableTwin, useColours } from "./common";
import { Ribbon } from "./Ribbon";
import { Rings } from "./Rings";
import { Stream } from "./Stream";
import { Strips } from "./Strips";
import { Waterfall } from "./Waterfall";

export type PaycheckMode = "ribbon" | "strips" | "waterfall" | "stream" | "rings" | "beads" | "bars" | "table";

export const PAYCHECK_MODES: { id: PaycheckMode; name: string; description: string }[] = [
  { id: "ribbon", name: "Ribbon", description: "One check splitting into where it goes; tap a part for its bills." },
  { id: "strips", name: "Strips over time", description: "The last checks from your transactions and the next ones planned, with the trend of what was yours." },
  { id: "waterfall", name: "Waterfall", description: "A check down to what is yours, one step per part." },
  { id: "stream", name: "Stream", description: "Six months of bands that widen and narrow as plans end." },
  { id: "rings", name: "Rings", description: "One ring per check, what is yours in the middle." },
  { id: "beads", name: "Beads", description: "Each bill on its day between paydays; drag one, or pick it from the list, to try another due day." },
  { id: "bars", name: "Bars", description: "One bar per check with the card payment in its parts." },
  { id: "table", name: "Table", description: "Every check and every part as figures." },
];

export function PaycheckPicture({ data, mode, snapshot }: { data: PaycheckViews; mode: PaycheckMode; snapshot?: Snapshot }) {
  const colours = useColours();
  const [table, setTable] = useState(false);
  const twin = [...data.history.slice(-3), ...data.windows].map((c) => ({ title: checkTitle(c), check: c }));
  const legendParts = data.parts.filter((p) => mode !== "stream" || p.key !== "aside");
  const showLegend = !["bars", "table", "beads"].includes(mode);
  let body;
  if (mode === "ribbon") body = <Ribbon checks={data.windows} />;
  else if (mode === "strips") body = <Strips history={data.history} planned={data.windows} />;
  else if (mode === "waterfall") body = <Waterfall checks={data.windows} />;
  else if (mode === "stream") body = <Stream months={data.months} />;
  else if (mode === "rings") body = <Rings checks={data.windows} />;
  else if (mode === "beads") body = <Beads spans={data.beads} />;
  else if (mode === "bars") body = snapshot ? <ByCheck s={snapshot} /> : null;
  else body = <TableTwin checks={twin} />;
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10 }}>
      {showLegend && (
        <div style={{ display: "flex", gap: 10, alignItems: "center", flexWrap: "wrap" }}>
          <Legend parts={legendParts} colours={colours} />
          <button type="button" className="btn" aria-pressed={table} style={{ marginLeft: "auto", minHeight: 32, padding: "4px 10px" }} onClick={() => setTable((t) => !t)}>{table ? "Show the picture" : "Show as a table"}</button>
        </div>
      )}
      {table && showLegend ? (mode === "stream" ? (
        <div style={{ overflowX: "auto" }}>
          <table className="ledger">
            <thead><tr><th>Month</th><th style={{ textAlign: "right" }}>Brings in</th>{data.months[0]?.parts.map((p) => <th key={p.key} style={{ textAlign: "right" }}>{p.label}</th>)}</tr></thead>
            <tbody>{data.months.map((m) => <tr key={m.month}><td>{m.month}{m.partial ? " (rest)" : ""}</td><td className="num" style={{ textAlign: "right" }}>{money(m.income)}</td>{m.parts.map((p) => <td key={p.key} className="num" style={{ textAlign: "right" }}>{money(p.amount)}</td>)}</tr>)}</tbody>
          </table>
        </div>
      ) : <TableTwin checks={twin} />) : body}
    </div>
  );
}

export function PaycheckSection({ snapshot, mode: given, onMode, title = "Your paychecks" }: { snapshot?: Snapshot; mode?: PaycheckMode; onMode?: (m: PaycheckMode) => void; title?: string }) {
  const data = useApi<PaycheckViews>("/api/v1/paychecks");
  const [chosen, setChosen] = useShowAs<PaycheckMode>("paycheck", "ribbon", PAYCHECK_MODES.map((m) => m.id));
  const mode = given ?? chosen;
  const change = onMode ?? setChosen;
  return (
    <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12, minWidth: 0 }}>
      <div style={{ display: "flex", alignItems: "center", gap: 10, flexWrap: "wrap" }}>
        <h2 style={{ margin: 0, fontSize: 15, fontWeight: 700 }}>{title}</h2>
        <span style={{ fontSize: 12, color: "var(--muted)" }}>{PAYCHECK_MODES.find((m) => m.id === mode)?.description}</span>
        <div style={{ marginLeft: "auto" }}><ModeSwitch label="Show paychecks as" modes={PAYCHECK_MODES} value={mode} onChange={change} /></div>
      </div>
      {data.error && !data.data && <Failed error={data.error} what="your paychecks" onRetry={data.reload} card={false} />}
      {!data.data && !data.error && <Loading what="your paychecks" card={false} />}
      {data.data && <PaycheckPicture data={data.data} mode={mode} snapshot={snapshot} />}
      {data.data && <div style={{ fontSize: 11, color: "var(--muted)" }}>{data.data.not_advice}</div>}
    </section>
  );
}
