// The app shell: a rail on wide screens, tabs on a phone, and the header with the payday chip.
import { BellDot, Clock, List, Receipt, Settings, Waves } from "lucide-react";
import type { ReactNode } from "react";
import { NavLink } from "react-router-dom";
import { daysWord, longDate, shortDate } from "../format";
import type { Snapshot } from "../types";

export function Shell({ children, snapshot, title }: { children: ReactNode; snapshot: Snapshot | null; title?: string }) {
  const today = snapshot?.as_of ?? new Date().toISOString().slice(0, 10);
  const pending = snapshot ? snapshot.pending.confirmations + snapshot.pending.approvals : 0;
  const NAV = [
    { to: "/today", label: "Today", icon: Clock },
    { to: "/", label: "Home", icon: Waves, end: true },
    { to: "/bills", label: "Bills", icon: Receipt },
    { to: "/confirm", label: "Needs you", icon: BellDot, badge: pending },
    { to: "/transactions", label: "Ledger", icon: List, phone: false },
    { to: "/settings", label: "Settings", icon: Settings },
  ];
  const item = ({ to, label, icon: Icon, end, badge }: (typeof NAV)[number], size: number) => (
    <NavLink key={to} to={to} end={end} className={({ isActive }) => (isActive ? "on" : "")} style={{ position: "relative" }}>
      <Icon size={size} strokeWidth={1.8} aria-hidden="true" />
      {label}
      {badge ? <span aria-label={`${badge} waiting`} style={{ position: "absolute", top: 4, right: 8, minWidth: 16, height: 16, borderRadius: 8, background: "var(--warning)", color: "#141518", fontSize: 10, fontWeight: 800, display: "grid", placeItems: "center", padding: "0 4px" }}>{badge}</span> : null}
    </NavLink>
  );
  return (
    <div style={{ minHeight: "100vh", display: "flex", flexWrap: "wrap", background: "var(--ground)" }}>
      <nav className="rail hidden md:flex" aria-label="Sections" style={{ flex: "0 0 76px", flexDirection: "column", gap: 2, padding: "14px 8px", boxSizing: "border-box", borderRight: "1px solid var(--hairline)" }}>
        <div style={{ display: "flex", alignItems: "center", justifyContent: "center", height: 36, marginBottom: 10 }}>
          <img src="/favicon.svg" width={28} height={28} alt="FinancialAuthority" />
        </div>
        {NAV.map((n) => item(n, 20))}
      </nav>
      <main style={{ flex: "999 1 320px", minWidth: 0, display: "flex", flexDirection: "column", gap: 16, boxSizing: "border-box" }} className="px-4 pt-3.5 pb-24 md:px-6 md:pb-6">
        <header style={{ display: "flex", flexWrap: "wrap", alignItems: "center", gap: 12, minHeight: 40 }}>
          <div style={{ fontSize: 13, color: "var(--ink-2)", fontWeight: 600 }}>{longDate(today)}</div>
          {snapshot && (
            <div style={{ display: "inline-flex", alignItems: "center", gap: 8, fontSize: 13, fontWeight: 700, padding: "6px 12px", borderRadius: 999, background: "var(--paycheck-soft)", color: "var(--paycheck-ink)" }}>
              <span className="sw" style={{ background: "var(--paycheck)", margin: 0 }} />
              Payday {daysWord(snapshot.next_paycheck.days)} · {shortDate(snapshot.next_paycheck.date)}
            </div>
          )}
          {title && <h1 style={{ margin: 0, fontSize: 16, fontWeight: 800 }}>{title}</h1>}
          <div style={{ flexGrow: 1 }} />
          <div style={{ fontSize: 12, color: "var(--muted)" }}>{snapshot ? `${snapshot.accounts.length} accounts` : ""}</div>
        </header>
        {children}
      </main>
      <nav className="tabs fixed left-0 right-0 bottom-0 flex justify-around items-start pt-2 md:hidden" aria-label="Tabs" style={{ height: 72, background: "var(--surface)", borderTop: "1px solid var(--hairline)" }}>
        {NAV.filter((n) => n.phone !== false).map((n) => item(n, 22))}
      </nav>
    </div>
  );
}
