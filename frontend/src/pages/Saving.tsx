// Saving: the ladder, the share of each check to keep, and the rollover. Five rungs computed from your own
// bills; a share of what each check truly leaves, suggested and never moved for you; and a finished plan's
// payment that keeps going into the ladder. Every target and date is the engine's.
import { useEffect, useState } from "react";
import { api, useApi } from "../api";
import { Why } from "../components/Status";
import { money, monthDay, shortDate } from "../format";
import type { Saving as SavingData } from "../guide";
import { palette } from "../looks";
import { getPrefs } from "../prefs";

function monthYear(iso: string | null): string {
  if (!iso) return "not within six years";
  const d = new Date(`${iso}T12:00:00`);
  return d.toLocaleDateString("en-US", { month: "short", year: "numeric" });
}

function Ladder({ s, etas }: { s: SavingData; etas: (string | null)[] }) {
  const c = palette(getPrefs().look);
  const top = Math.max(...s.rungs.map((r) => r.target), 1);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 8 }} role="list" aria-label="The saving ladder">
      {[...s.rungs].reverse().map((r) => {
        const here = s.next_rung?.n === r.n;
        const width = Math.max(12, Math.sqrt(r.target / top) * 100);
        const eta = etas[r.n - 1];
        return (
          <div key={r.n} role="listitem" style={{ display: "grid", gridTemplateColumns: "minmax(150px, 230px) minmax(0, 1fr) auto", gap: 10, alignItems: "center" }}>
            <div style={{ fontSize: 13, fontWeight: here ? 800 : 700, lineHeight: 1.2 }}>
              {r.n} · {r.name}
              <div className="num" style={{ fontWeight: 600, color: "var(--ink-2)", fontSize: 12 }}>{money(r.target)}</div>
            </div>
            <div style={{ position: "relative", width: `${width}%`, height: 26, borderRadius: 7, background: "var(--surface-2)", overflow: "hidden", outline: here ? "2px solid var(--ink)" : "none", outlineOffset: 1 }} role="img" aria-label={`${Math.round(r.progress * 100)}% of ${money(r.target)}`}>
              <div style={{ position: "absolute", inset: "0 auto 0 0", width: `${Math.round(r.progress * 100)}%`, background: c.saving }} />
            </div>
            <div className="num" style={{ fontSize: 12, textAlign: "right", color: r.reached ? "var(--safe-ink)" : "var(--ink-2)", fontWeight: here ? 800 : 600, whiteSpace: "nowrap" }}>
              {r.reached ? "reached" : eta ? (here ? shortDate(eta) : monthYear(eta)) : "not within six years"}
            </div>
          </div>
        );
      })}
    </div>
  );
}

function Rollover({ s }: { s: SavingData }) {
  const c = palette(getPrefs().look);
  const items = s.rollover.items;
  if (!items.length) return <div style={{ fontSize: 13, color: "var(--ink-2)" }}>No pay-later plan is running, so there is nothing to roll over yet.</div>;
  const start = new Date(`${s.as_of}T12:00:00`).getTime();
  const lastEnd = Math.max(...items.map((i) => new Date(`${i.last_payment}T12:00:00`).getTime()));
  const end = lastEnd + 120 * 86400000;
  const W = 640;
  const rowH = 30;
  const padL = 160;
  const H = items.length * (rowH + 8) + 28;
  const x = (t: number) => padL + ((W - padL - 10) * (t - start)) / Math.max(1, end - start);
  // Axis labels: today, then each plan's end, skipping any that would sit on top of the last one shown.
  const ticks: { key: string; x: number; text: string }[] = [{ key: "today", x: x(start), text: "today" }];
  for (const it of [...items].sort((a, b) => a.last_payment.localeCompare(b.last_payment))) {
    const tx = x(new Date(`${it.last_payment}T12:00:00`).getTime());
    if (tx - ticks[ticks.length - 1].x >= 52) ticks.push({ key: `t${it.id}`, x: tx, text: monthDay(it.last_payment) });
  }
  return (
    <div style={{ overflowX: "auto" }}>
      <svg viewBox={`0 0 ${W} ${H}`} width="100%" role="img" aria-label="Each plan's payment until it ends, then into the ladder" style={{ display: "block", maxWidth: 820, height: "auto" }}>
        {items.map((it, i) => {
          const y0 = 6 + i * (rowH + 8);
          const endX = x(new Date(`${it.last_payment}T12:00:00`).getTime());
          return (
            <g key={it.id}>
              <text x={0} y={y0 + rowH / 2 + 4} fontSize="12" fontWeight="700" fill="var(--ink)">{it.name.length > 22 ? `${it.name.slice(0, 21)}…` : it.name}</text>
              <rect x={x(start)} y={y0} width={Math.max(2, endX - x(start) - 2)} height={rowH} rx={5} fill={c.later}><title>{`${it.name}: ${money(it.installment, { cents: true })} each until ${shortDate(it.last_payment)}`}</title></rect>
              {s.rollover.enabled && <rect x={endX} y={y0} width={Math.max(2, W - 10 - endX)} height={rowH} rx={5} fill={c.saving}><title>{`From ${shortDate(it.last_payment)}: ${money(it.per_check, { cents: true })} a check into the ladder`}</title></rect>}
              {endX - x(start) > 90 && <text x={x(start) + 8} y={y0 + rowH / 2 + 4} fontSize="11" fontWeight="800" fill="#141518">{money(it.installment)} each</text>}
              {s.rollover.enabled && W - 10 - endX > 120 && <text x={endX + 8} y={y0 + rowH / 2 + 4} fontSize="11" fontWeight="800" fill="#141518">+{money(it.per_check)} a check saved</text>}
            </g>
          );
        })}
        {ticks.map((t) => <text key={t.key} x={t.x} y={H - 6} fontSize="10.5" fill="var(--muted)" textAnchor={t.key === "today" ? "start" : "middle"}>{t.text}</text>)}
      </svg>
    </div>
  );
}

export function Saving() {
  const loaded = useApi<SavingData>("/api/v1/saving");
  const [data, setData] = useState<SavingData | null>(null);
  const [share, setShare] = useState<number>(25);
  const [note, setNote] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  useEffect(() => {
    if (loaded.data) {
      setData(loaded.data);
      setShare(loaded.data.share_used);
    }
  }, [loaded.data]);
  if (loaded.error) return <div style={{ color: "var(--critical)" }}>Could not load the ladder: {loaded.error.message}</div>;
  if (!data) return <div style={{ color: "var(--muted)" }}>Building the ladder from your bills…</div>;
  const s = data;
  const row = s.by_share.find((b) => b.share === share) ?? s.by_share[0];
  const nextIdx = (s.next_rung?.n ?? 1) - 1;

  async function save(body: Record<string, unknown>, done: string) {
    setBusy(true);
    setNote(null);
    try {
      const out = await api<SavingData>("/api/v1/saving", { method: "PUT", body: JSON.stringify(body) });
      setData(out);
      setShare(out.share_used);
      setNote(done);
    } catch (e) {
      setNote((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 14, maxWidth: 1000 }}>
      <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 12 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 12, flexWrap: "wrap" }}>
          <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>Saving</h1>
          <span className="num" style={{ fontSize: 13, color: "var(--ink-2)" }}>{money(s.balance)} saved{s.accounts.length ? ` in ${s.accounts.map((a) => a.name).join(", ")}` : " · no savings account on file yet"}</span>
        </div>
        <Ladder s={s} etas={row.etas} />
        <div style={{ fontSize: 12, color: "var(--ink-2)", maxWidth: "75ch" }}>
          Rung 1 is a first cushion, so a surprise comes out of savings instead of an advance. Rung 2 is {money(s.must_pays)}, the bills, card payments and advances of your heavier next check. A month here is {money(s.month)}: your bills and your usual everyday spending.
        </div>
      </section>

      <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
          <strong style={{ fontSize: 15 }}>Keep a share of what each check leaves</strong>
          <span style={{ fontSize: 12, color: "var(--muted)" }}>{s.chosen ? `your share is ${s.share}%` : `not chosen yet; the suggestion is ${s.suggested}%`}</span>
        </div>
        <div style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
          {Object.entries(s.presets).map(([name, pct]) => (
            <button key={name} type="button" className={`btn${share === pct ? " dark" : ""}`} onClick={() => setShare(pct)}>{name[0].toUpperCase() + name.slice(1)} {pct}%</button>
          ))}
        </div>
        <label htmlFor="share" style={{ fontSize: 13, color: "var(--ink-2)" }}>Keep <strong className="num" style={{ color: "var(--ink)" }}>{share}%</strong></label>
        <input id="share" type="range" min={0} max={60} step={5} value={share} onChange={(e) => setShare(Number(e.target.value))} style={{ width: "100%", maxWidth: 520, accentColor: "var(--paycheck)" }} />
        <div className="num" style={{ display: "flex", gap: 18, flexWrap: "wrap", fontSize: 14 }}>
          <span>About <strong>{money(row.per_check)}</strong> a check</span>
          <span>Rung {nextIdx + 1} on <strong>{row.etas[nextIdx] ? shortDate(row.etas[nextIdx] as string) : "no date within six years"}</strong></span>
        </div>
        <div style={{ display: "flex", gap: 8, alignItems: "center", flexWrap: "wrap" }}>
          <button type="button" className="btn dark" disabled={busy || (s.chosen && s.share === share)} onClick={() => save({ share }, `Kept: ${share}% of what each check leaves. The payday sheet on Cover lists the move.`)}>Keep {share}%</button>
          {s.chosen && <button type="button" className="btn" disabled={busy} onClick={() => save({ clear_share: true }, "Cleared. The ladder shows the suggested share until you choose again.")}>Clear my share</button>}
          {note && <span style={{ fontSize: 13, color: "var(--ink-2)" }}>{note}</span>}
        </div>
        <div style={{ overflowX: "auto" }}>
          <table className="ledger">
            <thead><tr><th>Check</th><th style={{ textAlign: "right" }}>Leaves after bills and everyday spending</th><th style={{ textAlign: "right" }}>Keep at {s.share_used}%</th></tr></thead>
            <tbody>{s.per_check.map((p) => <tr key={p.date}><td>{shortDate(p.date)}</td><td className="num" style={{ textAlign: "right", color: p.left < 0 ? "var(--critical)" : "inherit" }}>{money(p.left)}</td><td className="num" style={{ textAlign: "right", fontWeight: 800 }}>{money(p.keep)}</td></tr>)}</tbody>
          </table>
        </div>
        <Why label="How the share is worked out" text={`${s.basis} A check that leaves nothing keeps nothing. Nothing moves until you move it.`} />
      </section>

      <section className="card" style={{ padding: 16, display: "flex", flexDirection: "column", gap: 10 }}>
        <div style={{ display: "flex", alignItems: "baseline", gap: 10, flexWrap: "wrap" }}>
          <strong style={{ fontSize: 15 }}>The rollover</strong>
          <span style={{ fontSize: 12, color: "var(--muted)" }}>when a plan ends, its payment keeps going, into the ladder</span>
        </div>
        <Rollover s={s} />
        <label style={{ display: "flex", gap: 8, alignItems: "center", fontSize: 13 }}>
          <input type="checkbox" checked={s.rollover.enabled} disabled={busy} onChange={(e) => save({ rollover: e.target.checked }, e.target.checked ? "Finished payments now roll into the ladder." : "Finished payments are free again; the share still applies to them.")} style={{ width: 18, height: 18, accentColor: "var(--safe)" }} />
          Roll finished payments into the ladder{s.rollover.freed_per_check ? ` (${money(s.rollover.freed_per_check)} a check once every plan ends)` : ""}
        </label>
        {s.next_rung && s.next_rung.eta !== s.next_rung.eta_without_rollover && (
          <div style={{ fontSize: 13, color: "var(--ink-2)" }}>With the rollover, rung {s.next_rung.n} lands {s.next_rung.eta ? shortDate(s.next_rung.eta) : "later"} instead of {s.next_rung.eta_without_rollover ? shortDate(s.next_rung.eta_without_rollover) : "not within six years"}.</div>
        )}
      </section>
      <div style={{ fontSize: 11, color: "var(--muted)" }}>{s.not_advice}</div>
    </div>
  );
}
