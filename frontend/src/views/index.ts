// Views are home layouts. Every view renders the same engine results and computes nothing of its own.
// Registering one here is all it takes to appear in Settings › Appearance.
import type { ComponentType } from "react";
import type { Snapshot } from "../types";
import { MonthGrid } from "./MonthGrid";
import { Planned } from "./Planned";
import { River } from "./River";
import { WhereItGoes } from "./WhereItGoes";
import { Windows } from "./Windows";

export interface ViewProps {
  snapshot: Snapshot;
}

export interface View {
  id: string;
  name: string;
  description: string;
  status: "ready" | "planned";
  milestone?: string;
  component: ComponentType<ViewProps>;
}

export const VIEWS: View[] = [
  { id: "river", name: "The River", description: "Sixty days of what is free to spend, with Today fixed, paydays, bills and the buffer floor.", status: "ready", component: River },
  { id: "windows", name: "Paycheck windows", description: "One column per paycheck, filled from the bottom with what leaves it.", status: "ready", component: Windows },
  { id: "month", name: "Month grid", description: "The calendar with paydays, due dates and each week's low point.", status: "ready", component: MonthGrid },
  { id: "flow", name: "Where it goes", description: "The flow of one paycheck into advances, bills, set-asides, buffer and what is yours.", status: "ready", component: WhereItGoes },
  { id: "envelopes", name: "Envelopes", description: "Every obligation as an envelope with its fill level.", status: "planned", milestone: "M7.6", component: Planned },
  { id: "board", name: "Bill board", description: "Cards that move right as they are paid; a card that stops moving is the problem.", status: "planned", milestone: "M7.6", component: Planned },
  { id: "transit", name: "Transit map", description: "Bills as stations on lines, paydays as the interchanges.", status: "planned", milestone: "M7.6", component: Planned },
  { id: "console", name: "Mission control", description: "Tiles: safe to spend, jobs, connectors, the brief, what waits for you.", status: "planned", milestone: "M7.6", component: Planned },
];

export function viewById(id: string): View {
  return VIEWS.find((v) => v.id === id) ?? VIEWS[0];
}
