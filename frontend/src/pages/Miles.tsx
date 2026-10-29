// Miles (M60): the mileage log. The year's business miles and what they count for lead, then a bar per month (how
// many trips were logged in it), the form to log a trip with the commuting rule beside it, and every trip with the
// rate for its day. The miles, rates and amounts are the engine's; the month bars only count the trips listed.
import { Ban, Car, MapPin, Plus } from "lucide-react";
import { useState, type FormEvent } from "react";
import { api, useApi } from "../api";
import { ConfirmButton } from "../components/ConfirmButton";
import { Said, Show } from "../components/State";
import { localToday, money } from "../format";
import type { Trip, TripIn, TripsOut } from "../types-business";
import { BusinessPick, CARD, FIELD, H2, NeedsBusiness, NotAdvice, PageHead, SMALL, SourceLine, Tile, dayWithYear, useChosenBusiness } from "./Business";

const MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"];

export function Miles() {
  const { overview, business, choose } = useChosenBusiness();
  const thisYear = Number(localToday().slice(0, 4));
  const [year, setYear] = useState(thisYear);
  const trips = useApi<TripsOut>(business ? `/api/v1/business/${business.id}/trips?year=${year}` : null, { live: true });
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 16 }}>
      <PageHead back title="Miles" sub="Business trips by car, each counted at the rate for the day it was driven." />
      <Show state={overview} what="your business">
        {(o) => !business ? (
          <NeedsBusiness>The mileage log belongs to a business. Add yours on Business first.</NeedsBusiness>
        ) : (
          <>
            <div style={{ display: "flex", gap: 10, flexWrap: "wrap", alignItems: "center" }}>
              <BusinessPick businesses={o.businesses} value={business.id} onChange={choose} />
              <div role="radiogroup" aria-label="Which year" style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
                {[thisYear, thisYear - 1].map((y) => (
                  <button key={y} type="button" role="radio" aria-checked={year === y} className={year === y ? "btn dark" : "btn"} onClick={() => setYear(y)} style={SMALL}>{y}</button>
                ))}
              </div>
            </div>
            <Show state={trips} what="the mileage log">
              {(t) => (
                <>
                  <div style={{ display: "grid", gridTemplateColumns: "repeat(auto-fit, minmax(160px, 1fr))", gap: 10 }}>
                    <Tile label={`Business miles in ${t.year}`} value={t.miles.toLocaleString("en-US")} isMoney={false} accent="var(--paycheck)" sub={`${t.trips.length} ${t.trips.length === 1 ? "trip" : "trips"} logged`} />
                    <Tile label="What they count for" value={money(t.amount, { cents: true })} accent="var(--saving)" sub="at the standard mileage rate for each day" />
                  </div>
                  <MonthBars trips={t.trips} year={t.year} />
                  <AddTrip businessId={business.id} onYear={setYear} never={t.never} />
                  <TripList t={t} />
                  <NotAdvice text={t.not_advice} />
                </>
              )}
            </Show>
          </>
        )}
      </Show>
    </div>
  );
}

/** How many trips were logged in each month: the trips the engine listed, laid out by month. */
function MonthBars({ trips, year }: { trips: Trip[]; year: number }) {
  const counts = MONTHS.map((_, m) => trips.filter((x) => Number(x.day.slice(5, 7)) === m + 1).length);
  const most = Math.max(1, ...counts);
  const h = 72;
  return (
    <section className="card" style={CARD} aria-labelledby="months-title">
      <h2 id="months-title" style={H2}>Trips by month, {year}</h2>
      <div role="img" aria-label={counts.map((c, m) => `${MONTHS[m]}: ${c}`).join(", ")} style={{ display: "grid", gridTemplateColumns: "repeat(12, minmax(0, 1fr))", gap: 4, alignItems: "end" }}>
        {counts.map((c, m) => (
          <div key={m} style={{ display: "flex", flexDirection: "column", alignItems: "center", gap: 3, minWidth: 0 }}>
            <span style={{ fontSize: 11, fontWeight: 800, color: c ? "var(--ink)" : "var(--muted)" }}>{c}</span>
            <div style={{ width: "100%", maxWidth: 28, height: h, display: "flex", alignItems: "flex-end", background: "var(--surface-2)", borderRadius: 4, overflow: "hidden" }}>
              <div style={{ width: "100%", height: `${(c / most) * 100}%`, background: "var(--paycheck)", borderRadius: 4 }} />
            </div>
            <span style={{ fontSize: 10, fontWeight: 700, color: "var(--ink-2)" }}>{MONTHS[m].slice(0, 1)}<span className="sr-only">{MONTHS[m].slice(1)}</span></span>
          </div>
        ))}
      </div>
    </section>
  );
}

function AddTrip({ businessId, onYear, never }: { businessId: number; onYear: (y: number) => void; never: string }) {
  const today = localToday();
  const [day, setDay] = useState(today);
  const [miles, setMiles] = useState("");
  const [purpose, setPurpose] = useState("");
  const [start, setStart] = useState("");
  const [end, setEnd] = useState("");
  const [busy, setBusy] = useState(false);
  const [note, setNote] = useState<{ text: string; tone: "info" | "error" } | null>(null);

  async function save(e: FormEvent) {
    e.preventDefault();
    const m = Number(miles.trim());
    if (!day) return setNote({ tone: "error", text: "Pick the day of the trip." });
    if (day > today) return setNote({ tone: "error", text: "A trip is logged on the day it happens or after, not before." });
    if (!miles.trim() || !Number.isFinite(m) || m <= 0 || m > 2000) return setNote({ tone: "error", text: "The miles are a number above 0, like 12.5." });
    if (purpose.trim().length < 3) return setNote({ tone: "error", text: "Say what the trip was for, like “Meeting a client”." });
    const body: TripIn = { day, miles: m, purpose: purpose.trim(), start: start.trim(), end: end.trim() };
    setBusy(true);
    setNote(null);
    try {
      const out = await api<TripsOut>(`/api/v1/business/${businessId}/trips`, { method: "POST", body: JSON.stringify(body) });
      onYear(out.year);
      setNote({ tone: "info", text: `Logged ${body.miles} miles on ${dayWithYear(day)}.` });
      setMiles("");
      setPurpose("");
      setStart("");
      setEnd("");
    } catch (err) {
      setNote({ tone: "error", text: (err as Error).message });
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card" style={CARD} aria-labelledby="add-trip-title">
      <h2 id="add-trip-title" style={H2}><Car size={16} aria-hidden="true" /> Log a trip</h2>
      <div style={{ display: "flex", gap: 16, flexWrap: "wrap", alignItems: "flex-start" }}>
        <form onSubmit={save} style={{ flex: "3 1 300px", minWidth: 0, display: "flex", flexDirection: "column", gap: 10 }}>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <label style={{ ...FIELD, flex: "1 1 150px" }}>
              <span className="lab">Day</span>
              <input className="input" type="date" value={day} max={today} onChange={(e) => setDay(e.target.value)} required />
            </label>
            <label style={{ ...FIELD, flex: "1 1 110px" }}>
              <span className="lab">Miles</span>
              <input className="input" value={miles} onChange={(e) => setMiles(e.target.value)} inputMode="decimal" placeholder="12.5" required />
            </label>
          </div>
          <label style={FIELD}>
            <span className="lab">What it was for</span>
            <input className="input" value={purpose} onChange={(e) => setPurpose(e.target.value)} maxLength={200} placeholder="Meeting a client about the new site" required />
          </label>
          <div style={{ display: "flex", gap: 10, flexWrap: "wrap" }}>
            <label style={{ ...FIELD, flex: "1 1 150px" }}>
              <span className="lab">From</span>
              <input className="input" value={start} onChange={(e) => setStart(e.target.value)} maxLength={120} placeholder="Home office" />
            </label>
            <label style={{ ...FIELD, flex: "1 1 150px" }}>
              <span className="lab">To</span>
              <input className="input" value={end} onChange={(e) => setEnd(e.target.value)} maxLength={120} placeholder="Client's shop" />
            </label>
          </div>
          <div style={{ display: "flex", gap: 8, flexWrap: "wrap", alignItems: "center" }}>
            <button type="submit" className="btn dark" disabled={busy} style={{ display: "inline-flex", alignItems: "center", gap: 6 }}>
              <Plus size={15} aria-hidden="true" /> {busy ? "Saving…" : "Log the trip"}
            </button>
            {note && <Said text={note.text} tone={note.tone} />}
          </div>
        </form>
        <aside style={{ flex: "1 1 200px", minWidth: 0, display: "flex", gap: 8, alignItems: "flex-start", padding: "12px 14px", borderRadius: 12, background: "var(--surface-2)", fontSize: 14, lineHeight: 1.5 }}>
          <Ban size={16} aria-hidden="true" style={{ flexShrink: 0, marginTop: 3 }} />
          <span>{never}</span>
        </aside>
      </div>
    </section>
  );
}

function TripList({ t }: { t: TripsOut }) {
  return (
    <section className="card" style={CARD} aria-labelledby="trips-title">
      <h2 id="trips-title" style={H2}>Every trip in {t.year}</h2>
      {t.rates.length > 0 && (
        <div style={{ display: "flex", gap: "4px 14px", flexWrap: "wrap", fontSize: 13, color: "var(--ink-2)" }}>
          {t.rates.map((r) => <span key={r.from}>{r.cents}¢ a mile from {dayWithYear(r.from)} to {dayWithYear(r.to)}</span>)}
        </div>
      )}
      <SourceLine source={t.source} />
      {t.trips.length === 0 ? (
        <div style={{ fontSize: 13, color: "var(--ink-2)" }}>No trips logged in {t.year} yet. Log one above, on the day you drive it.</div>
      ) : (
        <div style={{ overflowX: "auto", position: "relative" }}>
          <table className="ledger">
            <thead>
              <tr><th>Day</th><th>What it was for</th><th style={{ textAlign: "right" }}>Miles</th><th style={{ textAlign: "right" }}>Rate</th><th style={{ textAlign: "right" }}>Counts for</th><th><span className="sr-only">Remove</span></th></tr>
            </thead>
            <tbody>
              {[...t.trips].reverse().map((x) => (
                <tr key={x.id}>
                  <td style={{ whiteSpace: "nowrap" }}>{dayWithYear(x.day)}</td>
                  <td style={{ minWidth: 160 }}>
                    <div style={{ overflowWrap: "anywhere" }}>{x.purpose}</div>
                    {(x.start || x.end) && (
                      <div style={{ fontSize: 12, color: "var(--muted)" }}>
                        <MapPin size={12} aria-hidden="true" style={{ display: "inline-block", verticalAlign: -2 }} /> {x.start || "?"} → {x.end || "?"}
                      </div>
                    )}
                  </td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap" }}>{x.miles}</td>
                  <td style={{ textAlign: "right", whiteSpace: "nowrap", color: x.cents === null ? "var(--muted)" : undefined }}>{x.cents === null ? "not known yet" : `${x.cents}¢`}</td>
                  <td className="num" style={{ textAlign: "right", whiteSpace: "nowrap", fontWeight: 800 }}>{money(x.amount, { cents: true })}</td>
                  <td style={{ textAlign: "right" }}>
                    <ConfirmButton label="Remove" name={`Remove the trip on ${dayWithYear(x.day)}`} question="Remove this trip?" yes="Yes, remove it" style={{ minHeight: 32, padding: "4px 10px", fontSize: 12 }} onConfirm={() => api(`/api/v1/business/trips/${x.id}`, { method: "DELETE" })} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </section>
  );
}
