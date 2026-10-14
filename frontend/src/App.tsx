import { Navigate, Route, Routes } from "react-router-dom";
import { useApi } from "./api";
import { Shell } from "./components/Shell";
import { Accounts } from "./pages/Accounts";
import { Appearance } from "./pages/Appearance";
import { Audit } from "./pages/Audit";
import { Bills } from "./pages/Bills";
import { Chat } from "./pages/Chat";
import { Clients } from "./pages/Clients";
import { Confirm } from "./pages/Confirm";
import { Debts } from "./pages/Debts";
import { Connectors } from "./pages/Connectors";
import { Home } from "./pages/Home";
import { Investments } from "./pages/Investments";
import { Jobs } from "./pages/Jobs";
import { Mail } from "./pages/Mail";
import { Models } from "./pages/Models";
import { Notifications } from "./pages/Notifications";
import { Paystubs } from "./pages/Paystubs";
import { Reports } from "./pages/Reports";
import { SettingsIndex } from "./pages/Settings";
import { SignIn } from "./pages/SignIn";
import { Today } from "./pages/Today";
import { Transactions } from "./pages/Transactions";
import type { Me, Snapshot } from "./types";

function Loading({ text }: { text: string }) {
  return <div style={{ padding: 24, color: "var(--muted)" }}>{text}</div>;
}

export default function App() {
  const me = useApi<Me>("/api/auth/me");
  const snapshot = useApi<Snapshot>(me.data ? "/api/v1/snapshot" : null);

  if (me.loading) return <Loading text="Opening…" />;
  if (me.error && me.error.status === 401) return <SignIn onDone={me.reload} />;
  if (me.error) return <Loading text={`The server answered: ${me.error.message}`} />;

  const snap = snapshot.data;
  const body = (content: (s: Snapshot) => React.ReactNode) => {
    if (snapshot.error) return <Loading text={`Could not load the snapshot: ${snapshot.error.message}`} />;
    if (!snap) return <Loading text="Computing…" />;
    return content(snap);
  };

  return (
    <Shell snapshot={snap}>
      <Routes>
        <Route path="/" element={body((s) => <Home snapshot={s} />)} />
        <Route path="/today" element={body((s) => <Today snapshot={s} />)} />
        <Route path="/transactions" element={<Transactions />} />
        <Route path="/bills" element={<Bills onChanged={snapshot.reload} />} />
        <Route path="/mail" element={<Mail />} />
        <Route path="/chat" element={body((s) => <Chat snapshot={s} onChanged={snapshot.reload} />)} />
        <Route path="/paystubs" element={<Paystubs />} />
        <Route path="/debts" element={<Debts />} />
        <Route path="/accounts" element={<Accounts />} />
        <Route path="/investments" element={<Investments />} />
        <Route path="/reports" element={<Reports />} />
        <Route path="/confirm" element={<Confirm onChanged={snapshot.reload} />} />
        <Route path="/settings" element={<SettingsIndex />} />
        <Route path="/settings/appearance" element={<Appearance />} />
        <Route path="/settings/clients" element={<Clients />} />
        <Route path="/settings/models" element={<Models />} />
        <Route path="/settings/notifications" element={<Notifications />} />
        <Route path="/settings/connectors" element={<Connectors />} />
        <Route path="/settings/jobs" element={<Jobs />} />
        <Route path="/settings/audit" element={<Audit />} />
        <Route path="*" element={<Navigate to="/" replace />} />
      </Routes>
    </Shell>
  );
}
