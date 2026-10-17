// Views are home layouts. Every view renders the same engine results and computes nothing of its own.
// Registering one here is all it takes to appear in Settings › Appearance.
import { lazy, type ComponentType } from "react";
import type { Snapshot } from "../types";

// Each view is its own chunk, loaded when a home shows it.
const River = lazy(() => import("./River").then((m) => ({ default: m.River })));
const Windows = lazy(() => import("./Windows").then((m) => ({ default: m.Windows })));
const MonthGrid = lazy(() => import("./MonthGrid").then((m) => ({ default: m.MonthGrid })));
const WhereItGoes = lazy(() => import("./WhereItGoes").then((m) => ({ default: m.WhereItGoes })));
const Envelopes = lazy(() => import("./Envelopes").then((m) => ({ default: m.Envelopes })));
const BillBoard = lazy(() => import("./BillBoard").then((m) => ({ default: m.BillBoard })));
const TransitMap = lazy(() => import("./TransitMap").then((m) => ({ default: m.TransitMap })));
const MissionControl = lazy(() => import("./MissionControl").then((m) => ({ default: m.MissionControl })));

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
  { id: "river", name: "The River", description: "Sixty days of what is free to spend, with today, paydays, bills and the safety cushion marked.", status: "ready", component: River },
  { id: "windows", name: "Paycheck by paycheck", description: "One column per paycheck, filled from the bottom with what leaves it.", status: "ready", component: Windows },
  { id: "month", name: "Month grid", description: "The calendar with paydays, due dates and each week's lowest left to spend.", status: "ready", component: MonthGrid },
  { id: "flow", name: "Where it goes", description: "Where one paycheck goes: advances, bills, what it keeps for the next paycheck, the safety cushion, and what is yours.", status: "ready", component: WhereItGoes },
  { id: "envelopes", name: "Envelopes", description: "Every obligation as an envelope with its fill level.", status: "ready", component: Envelopes },
  { id: "board", name: "Bill board", description: "Cards that move right as they are paid; a card that stops moving is the problem.", status: "ready", component: BillBoard },
  { id: "transit", name: "Transit map", description: "Bills as stations on lines, paydays as the interchanges.", status: "ready", component: TransitMap },
  { id: "console", name: "Mission control", description: "Tiles: safe to spend, jobs, connections, the brief, what needs you.", status: "ready", component: MissionControl },
];

export function viewById(id: string): View {
  return VIEWS.find((v) => v.id === id) ?? VIEWS[0];
}
