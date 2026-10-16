// The words the app uses, each in one or two plain sentences. Linked from the header on every page.
import { useLocation } from "react-router-dom";
import { useEffect } from "react";

// Each id stays as it is: other screens link to /glossary#<id>.
export const TERMS: { id: string; word: string; means: string }[] = [
  { id: "safe-to-spend", word: "Safe to spend", means: "What you can spend before the next payday without touching a bill, an advance or your safety cushion. It never counts the next paycheck early." },
  { id: "due-before-payday", word: "Due before payday", means: "What leaves checking between today and the next payday. Bills charged to a card are paid with the card payment instead, so they are listed but not counted here." },
  { id: "window", word: "This paycheck, the next paycheck", means: "The days one paycheck has to pay for: from the day it lands to the day before the next one lands. \"This paycheck\" is the one coming next; \"the next paycheck\" is the one after it." },
  { id: "set-aside", word: "Set aside (kept for the next paycheck)", means: "Money a paycheck keeps for the next paycheck's bills, so a big bill like rent is ready when it comes: half of a large bill, all of a small one." },
  { id: "buffer", word: "Safety cushion (buffer)", means: "Money kept in checking that the plan never spends, for the surprise you did not see coming. You set its size on Settings." },
  { id: "carried-in", word: "Carried in", means: "What a paycheck's days start with besides the paycheck itself: what the paycheck before kept for them, or today's checking less what is due." },
  { id: "pace", word: "Usual spending", means: "What you spend on everyday things in an ordinary day, from the last eight weeks of your transactions, with bills and borrowing left out." },
  { id: "honest-line", word: "Your balance day by day", means: "The sixty-day picture of what is free to spend if you keep spending your usual amount each day. The lowest balance is the day it dips lowest." },
  { id: "cover", word: "Cover", means: "Whether each account holds enough for what comes out of it before each of the next two paydays. When one is short, the payday sheet lists what to move into it. The app never moves money itself." },
  { id: "sheet", word: "Payday sheet", means: "The moves that would cover every account, in date order, for you to make and tick off. The app never moves money itself." },
  { id: "ladder", word: "Saving ladder", means: "Five savings goals that build on each other: a starter, one paycheck's worth of bills you must pay, then one, three and six months of costs." },
  { id: "share", word: "Share", means: "The part of what each paycheck has left after its bills that you choose to save. Suggested, never automatic." },
  { id: "rollover", word: "Rollover", means: "When a pay-later plan ends, its payment keeps going into savings instead of being spent." },
  { id: "advance", word: "Advance", means: "Money an app like Dave or Earnin lends before payday and takes back on payday, usually with a fee or a tip. The app counts the fee as money lost." },
  { id: "pay-later", word: "Pay-later plan", means: "A purchase split into payments by Affirm, Klarna, Afterpay and the like. Some charge interest; the statement says how much." },
  { id: "credit-limit", word: "Credit limit", means: "The most a card lets you owe. You can save each card's limit on Debts." },
  { id: "utilisation", word: "Share of the limit used", means: "How much of a card's credit limit you owe, as a percent. The figure on the statement day is the one the credit bureaus see; under 30 percent is the usual target." },
  { id: "statement-day", word: "Statement day", means: "The day the card company closes the month and reports the balance. Paying before it lowers what is reported." },
  { id: "minimum", word: "Minimum payment", means: "The least a card accepts by its due day. Paying only the minimum keeps the account current but the balance keeps charging interest." },
  { id: "apr", word: "APR", means: "The yearly interest rate on a balance. At 30 percent, a thousand dollars costs about twenty-five dollars a month to carry." },
  { id: "pending", word: "Needs you", means: "Things found in your mail or your transactions that are not counted until you confirm them: a new bill, a paystub, a plan." },
  { id: "approval", word: "Approval", means: "Nothing is sent on your behalf until you tap Approve, or you allowed that recipient ahead of time." },
  { id: "not-advice", word: "Not financial advice", means: "Every figure is arithmetic on your own records. The app shows what the numbers say; the choices are yours." },
];

export function Glossary() {
  const { hash } = useLocation();
  useEffect(() => {
    if (hash) document.getElementById(hash.slice(1))?.scrollIntoView({ block: "start" });
  }, [hash]);
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 12, maxWidth: 760 }}>
      <h1 style={{ margin: 0, fontSize: 20, fontWeight: 800 }}>The words the app uses</h1>
      <dl style={{ margin: 0, display: "flex", flexDirection: "column", gap: 8 }}>
        {TERMS.map((t) => (
          <div key={t.id} id={t.id} className="card" style={{ padding: "10px 14px", outline: hash === `#${t.id}` ? "2px solid var(--ink)" : "none" }}>
            <dt style={{ fontWeight: 800 }}>{t.word}</dt>
            <dd style={{ margin: "2px 0 0", color: "var(--ink-2)", fontSize: 14 }}>{t.means}</dd>
          </div>
        ))}
      </dl>
    </div>
  );
}
