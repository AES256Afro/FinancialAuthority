import { lazy, Suspense, useEffect } from "react";
import { Navigate, Route, Routes, useLocation } from "react-router-dom";
import { useApi } from "./api";
import { loadAppearance } from "./appearance";
import { Shell } from "./components/Shell";
import { Failed, Loading as Wait } from "./components/State";
import { Home } from "./pages/Home";
import { SignIn } from "./pages/SignIn";
import { Today } from "./pages/Today";
import type { Snapshot } from "./types";
import { roleOf, type MePerson } from "./types-people";

// Pages load when first opened, so the app opens fast on a phone; Home and Today come with the first load.
const Accounts = lazy(() => import("./pages/Accounts").then((m) => ({ default: m.Accounts })));
const Appearance = lazy(() => import("./pages/Appearance").then((m) => ({ default: m.Appearance })));
const Audit = lazy(() => import("./pages/Audit").then((m) => ({ default: m.Audit })));
const Backups = lazy(() => import("./pages/Backups").then((m) => ({ default: m.Backups })));
const Setup = lazy(() => import("./pages/Setup").then((m) => ({ default: m.Setup })));
const Bills = lazy(() => import("./pages/Bills").then((m) => ({ default: m.Bills })));
const Chat = lazy(() => import("./pages/Chat").then((m) => ({ default: m.Chat })));
const Clients = lazy(() => import("./pages/Clients").then((m) => ({ default: m.Clients })));
const Confirm = lazy(() => import("./pages/Confirm").then((m) => ({ default: m.Confirm })));
const Borrowing = lazy(() => import("./pages/Borrowing").then((m) => ({ default: m.Borrowing })));
const Housing = lazy(() => import("./pages/Housing").then((m) => ({ default: m.Housing })));
const Move = lazy(() => import("./pages/Move").then((m) => ({ default: m.Move })));
const CheckIn = lazy(() => import("./pages/CheckIn").then((m) => ({ default: m.CheckIn })));
const CoverPage = lazy(() => import("./pages/CoverPage").then((m) => ({ default: m.CoverPage })));
const Saving = lazy(() => import("./pages/Saving").then((m) => ({ default: m.Saving })));
const Welcome = lazy(() => import("./pages/Welcome").then((m) => ({ default: m.Welcome })));
const Exports = lazy(() => import("./pages/Exports").then((m) => ({ default: m.Exports })));
const Switch = lazy(() => import("./pages/Switch").then((m) => ({ default: m.Switch })));
const Glossary = lazy(() => import("./pages/Glossary").then((m) => ({ default: m.Glossary })));
const Debts = lazy(() => import("./pages/Debts").then((m) => ({ default: m.Debts })));
const Connectors = lazy(() => import("./pages/Connectors").then((m) => ({ default: m.Connectors })));
const Investments = lazy(() => import("./pages/Investments").then((m) => ({ default: m.Investments })));
const Jobs = lazy(() => import("./pages/Jobs").then((m) => ({ default: m.Jobs })));
const Mail = lazy(() => import("./pages/Mail").then((m) => ({ default: m.Mail })));
const Models = lazy(() => import("./pages/Models").then((m) => ({ default: m.Models })));
const Outlook = lazy(() => import("./pages/Outlook").then((m) => ({ default: m.Outlook })));
const Notifications = lazy(() => import("./pages/Notifications").then((m) => ({ default: m.Notifications })));
const Paystubs = lazy(() => import("./pages/Paystubs").then((m) => ({ default: m.Paystubs })));
const People = lazy(() => import("./pages/People").then((m) => ({ default: m.People })));
const Shared = lazy(() => import("./pages/Shared").then((m) => ({ default: m.Shared })));
const Join = lazy(() => import("./pages/Join").then((m) => ({ default: m.Join })));
const More = lazy(() => import("./pages/More").then((m) => ({ default: m.More })));
const Budgets = lazy(() => import("./pages/Budgets").then((m) => ({ default: m.Budgets })));
const Security = lazy(() => import("./pages/Security").then((m) => ({ default: m.Security })));
const Privacy = lazy(() => import("./pages/Privacy").then((m) => ({ default: m.Privacy })));
const Recap = lazy(() => import("./pages/Recap").then((m) => ({ default: m.Recap })));
const Reports = lazy(() => import("./pages/Reports").then((m) => ({ default: m.Reports })));
const SettingsIndex = lazy(() => import("./pages/Settings").then((m) => ({ default: m.SettingsIndex })));
const Transactions = lazy(() => import("./pages/Transactions").then((m) => ({ default: m.Transactions })));

function Loading({ text }: { text: string }) {
  return <div style={{ padding: 24, color: "var(--muted)" }}>{text}</div>;
}

export default function App() {
  const { pathname } = useLocation();
  // The one-time link a second person was sent opens before anyone is signed in.
  const joining = pathname === "/join";
  const me = useApi<MePerson>(joining ? null : "/api/auth/me");
  const role = roleOf(me.data);
  // A sign-in that sees the shared accounts only reaches one page; it never asks for the plan.
  const snapshot = useApi<Snapshot>(me.data && role !== "shared" ? "/api/v1/snapshot" : null);
  useEffect(() => {
    if (me.data) loadAppearance();
  }, [me.data]);

  if (joining) return <Suspense fallback={<Loading text="Opening…" />}><Join /></Suspense>;
  if (me.loading) return <Loading text="Opening…" />;
  if (me.error && me.error.status === 401) return <SignIn onDone={me.reload} />;
  if (me.error) return <div style={{ padding: 24, maxWidth: 640 }}><Failed error={me.error} what="the app" onRetry={me.reload} /></div>;

  if (role === "shared") {
    return (
      <Shell snapshot={null} me={me.data}>
        <Suspense fallback={<Loading text="Opening…" />}>
        <Routes>
          <Route path="/shared" element={<Shared me={me.data} />} />
          <Route path="/settings/security" element={<Security />} />
          <Route path="*" element={<Navigate to="/shared" replace />} />
        </Routes>
        </Suspense>
      </Shell>
    );
  }

  const snap = snapshot.data;
  const body = (content: (s: Snapshot) => React.ReactNode) => {
    if (snapshot.error && !snap) return <Failed error={snapshot.error} what="your plan" onRetry={snapshot.reload} />;
    if (!snap) return <Wait what="your plan" />;
    return content(snap);
  };

  return (
    <Shell snapshot={snap} me={me.data}>
      <Suspense fallback={<Loading text="Opening…" />}>
      <Routes>
        <Route path="/" element={body((s) => <Home snapshot={s} />)} />
        <Route path="/today" element={body((s) => <Today snapshot={s} />)} />
        <Route path="/transactions" element={<Transactions />} />
        <Route path="/bills" element={<Bills onChanged={snapshot.reload} />} />
        <Route path="/mail" element={<Mail />} />
        <Route path="/chat" element={body((s) => <Chat snapshot={s} onChanged={snapshot.reload} />)} />
        <Route path="/paystubs" element={<Paystubs />} />
        <Route path="/debts" element={<Debts />} />
        <Route path="/borrowing" element={<Borrowing />} />
        <Route path="/housing" element={<Housing onChanged={snapshot.reload} />} />
        <Route path="/move" element={<Move onChanged={snapshot.reload} />} />
        <Route path="/cover" element={<CoverPage />} />
        <Route path="/saving" element={<Saving />} />
        <Route path="/checkin" element={<CheckIn />} />
        <Route path="/welcome" element={<Welcome />} />
        <Route path="/exports" element={<Exports />} />
        <Route path="/glossary" element={<Glossary />} />
        <Route path="/switch" element={<Switch />} />
        <Route path="/accounts" element={<Accounts />} />
        <Route path="/investments" element={<Investments />} />
        <Route path="/outlook" element={<Outlook />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/confirm" element={<Confirm onChanged={snapshot.reload} />} />
        <Route path="/shared" element={<Shared me={me.data} />} />
        <Route path="/settings" element={<SettingsIndex me={me.data} />} />
        <Route path="/more" element={<More />} />
        <Route path="/budgets" element={<Budgets />} />
        <Route path="/recap" element={<Recap />} />
        <Route path="/settings/security" element={<Security />} />
        <Route path="/settings/people" element={<People me={me.data} />} />
        <Route path="/settings/privacy" element={<Privacy />} />
        <Route path="/settings/appearance" element={<Appearance />} />
        <Route path="/settings/clients" element={<Clients />} />
        <Route path="/settings/models" element={<Models />} />
        <Route path="/settings/notifications" element={<Notifications />} />
        <Route path="/settings/connectors" element={<Connectors />} />
        <Route path="/settings/jobs" element={<Jobs />} />
        <Route path="/settings/audit" element={<Audit />} />
        <Route path="/settings/backups" element={<Backups />} />
        <Route path="/settings/setup" element={<Setup />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
      </Suspense>
    </Shell>
  );
}
