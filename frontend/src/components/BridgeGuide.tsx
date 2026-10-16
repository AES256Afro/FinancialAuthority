// The Proton Mail Bridge, step by step, for whoever installed the app with no one beside them: which computer
// each command runs on, which password goes where, and every prompt the Bridge shows on the way.
import { AlertTriangle, Copy, ExternalLink } from "lucide-react";
import { type ReactNode, useState } from "react";

export interface BridgeInfo {
  container: string; // the Bridge's container on the server, e.g. bp-financialauthority-bridge on BoxPilot
  install: string; // boxpilot | compose | docker | ""
  in_container: boolean; // whether the app itself runs in Docker
}

const PROTON_BRIDGE = "https://proton.me/mail/bridge";
const PROTON_ACCOUNT = "https://account.proton.me";
const PROTON_PLANS = "https://proton.me/support/proton-plans";
const PROTON_BRIDGE_PASSWORD = "https://proton.me/support/invalid-password-error-setting-email-client";
const PROTON_VERIFY = "https://proton.me/support/human-verification";

function Cmd({ text }: { text: string }) {
  const [copied, setCopied] = useState(false);
  return (
    <div style={{ display: "flex", alignItems: "stretch", gap: 0, background: "var(--surface)", borderRadius: 6, border: "1px solid var(--hairline)", overflow: "hidden" }}>
      <code style={{ flex: 1, padding: "6px 8px", overflowX: "auto", whiteSpace: "nowrap", fontSize: 12 }}>{text}</code>
      <button type="button" className="btn" aria-label="Copy the command" title="Copy" style={{ borderRadius: 0, border: 0, borderLeft: "1px solid var(--hairline)", display: "inline-flex", alignItems: "center", gap: 4, fontSize: 12 }}
        onClick={async () => { try { await navigator.clipboard.writeText(text); setCopied(true); window.setTimeout(() => setCopied(false), 1500); } catch { /* the text can still be selected */ } }}>
        <Copy size={13} aria-hidden />{copied ? "Copied" : "Copy"}
      </button>
    </div>
  );
}

function Step({ n, title, children }: { n: number; title: string; children?: ReactNode }) {
  return (
    <li style={{ display: "grid", gridTemplateColumns: "26px minmax(0, 1fr)", gap: 8, alignItems: "start" }}>
      <span aria-hidden style={{ width: 22, height: 22, borderRadius: 11, background: "var(--ink)", color: "var(--inverse)", fontSize: 12, fontWeight: 800, display: "grid", placeItems: "center" }}>{n}</span>
      <div style={{ display: "flex", flexDirection: "column", gap: 6, minWidth: 0 }}>
        <strong style={{ fontSize: 13 }}>{title}</strong>
        {children && <div style={{ fontSize: 13, color: "var(--ink-2)", display: "flex", flexDirection: "column", gap: 6 }}>{children}</div>}
      </div>
    </li>
  );
}

function Out({ href, children }: { href: string; children: ReactNode }) {
  return <a href={href} target="_blank" rel="noreferrer noopener" style={{ display: "inline-flex", alignItems: "center", gap: 3 }}>{children}<ExternalLink size={12} aria-hidden /></a>;
}

export function BridgeGuide({ info, onDesktop }: { info: BridgeInfo; onDesktop?: (host: string, port: string) => void }) {
  const [where, setWhere] = useState<"server" | "desktop">("server");
  const c = info.container || "financialauthority-bridge";
  const serverWord = info.install === "boxpilot" ? "the server BoxPilot runs on" : "the server FinancialAuthority runs on";
  return (
    <div style={{ display: "flex", flexDirection: "column", gap: 10, background: "var(--surface-2)", borderRadius: 10, padding: 12 }}>
      <div style={{ fontSize: 13 }}>
        Proton Mail is read through the <strong>Proton Mail Bridge</strong>, Proton's own program that hands your mail to email apps. It needs a paid Proton plan: Mail Plus, Unlimited, Duo, Family or Visionary (<Out href={PROTON_PLANS}>plans</Out>); the free plan has no Bridge.
        It is signed in <strong>once</strong>, with your real Proton password; it then makes a <strong>separate Bridge password</strong>, and only that one goes in this form.
      </div>
      <div role="radiogroup" aria-label="Where the Bridge runs" style={{ display: "flex", gap: 6, flexWrap: "wrap" }}>
        <button type="button" role="radio" aria-checked={where === "server"} className={where === "server" ? "btn dark" : "btn"} onClick={() => setWhere("server")}>The app's own Bridge, on {info.install === "boxpilot" ? "BoxPilot" : "the server"} (usual)</button>
        <button type="button" role="radio" aria-checked={where === "desktop"} className={where === "desktop" ? "btn dark" : "btn"} onClick={() => setWhere("desktop")}>The Bridge app on this Windows or Mac computer</button>
      </div>

      {where === "server" ? (
        <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 12 }}>
          <Step n={1} title={`Open a terminal and connect to ${serverWord}.`}>
            <span>The Bridge runs there, not on the computer you are using now (unless the app is installed on this one). On Windows use PowerShell or Terminal; on a Mac, Terminal. Use your own server user and address:</span>
            <Cmd text="ssh you@your-server" />
            {info.install === "boxpilot" && <span>No SSH yet? In BoxPilot, open Users &amp; SSH to add your user and your key.</span>}
          </Step>
          <Step n={2} title="Pause the app's Bridge for a minute.">
            <Cmd text={`sudo docker stop ${c}`} />
            <span>If your user is in the docker group, leave out <code>sudo</code> in every command here.</span>
          </Step>
          <Step n={3} title="Open the Bridge's prompt.">
            <Cmd text={`sudo docker run --rm -it --volumes-from ${c} --entrypoint protonmail-bridge $(sudo docker inspect -f '{{.Config.Image}}' ${c}) --cli`} />
            <span>A drawing of a bridge appears, then a <code>&gt;&gt;&gt;</code> prompt. A warning about <em>dbus</em> is normal.</span>
          </Step>
          <Step n={4} title="Type login, then answer its questions.">
            <span><strong>Username:</strong> your Proton address.</span>
            <span><strong>Password:</strong> your <strong>real Proton password</strong>, the one you use at <Out href={PROTON_ACCOUNT}>account.proton.me</Out>. Not a Bridge password: there is none yet, and one from a Bridge on another computer will not work. Nothing shows while you type; type it rather than paste it.</span>
            <span><strong>A link to verify.proton.me:</strong> open it in your browser, solve the check, come back and press Enter. If the puzzle will not pass, Proton offers email or text verification (<Out href={PROTON_VERIFY}>human verification</Out>).</span>
            <span><strong>"Do you want to use a security key?"</strong> Type <code>no</code>, then the six-digit code from your authenticator app. A hardware key plugged into your own computer cannot reach the server; if that is your only second step, add an authenticator app in Proton's security settings first.</span>
            <span><strong>A mailbox password:</strong> asked only if your account has a second password; enter it.</span>
            <span>It says <em>Account … was added successfully</em> and starts copying your mail, which can take hours. You do not have to wait.</span>
          </Step>
          <Step n={5} title="Type info, and copy two things from it into this form.">
            <span>It shows a <strong>Username</strong> and a <strong>Password</strong>. That password is the <strong>Bridge password</strong>: it goes in the Bridge password box below. Leave the host and port here as they are, even though the screen shows 127.0.0.1 and 1143.</span>
          </Step>
          <Step n={6} title="Type exit, then start the Bridge again.">
            <Cmd text={`sudo docker start ${c}`} />
            <span>It keeps the sign-in through restarts and backups and carries on copying in the background.</span>
          </Step>
          <Step n={7} title="Paste, Test first, then Save mailbox." />
        </ol>
      ) : (
        <ol style={{ listStyle: "none", margin: 0, padding: 0, display: "flex", flexDirection: "column", gap: 12 }}>
          <Step n={1} title="Only when the app runs on this same computer, in Docker Desktop.">
            <span>If FinancialAuthority runs on a server, the Bridge on your computer is a different Bridge with a different password, and the app cannot reach it: use the other tab.</span>
          </Step>
          <Step n={2} title="Install the Proton Mail Bridge app and sign in to it.">
            <span>Download it from <Out href={PROTON_BRIDGE}>proton.me/mail/bridge</Out>. Sign in with your real Proton password; the check and your second step happen in its own window.</span>
          </Step>
          <Step n={3} title="Find the Bridge password in the app.">
            <span>In the Bridge app, select your account, then <strong>Mailbox details</strong>. Copy the <strong>username</strong> and the IMAP <strong>password</strong> (the copy icon beside it): that password is the Bridge password (<Out href={PROTON_BRIDGE_PASSWORD}>Proton's page on it</Out>).</span>
          </Step>
          <Step n={4} title="Fill in this form like this.">
            <span><strong>IMAP host:</strong> <code>{info.in_container ? "host.docker.internal" : "127.0.0.1"}</code>{info.in_container ? " (the app runs inside Docker, where 127.0.0.1 would mean the app itself)" : ""}. <strong>Port:</strong> <code>1143</code>. Then the username and the Bridge password.</span>
            {onDesktop && <button type="button" className="btn" style={{ alignSelf: "flex-start" }} onClick={() => onDesktop(info.in_container ? "host.docker.internal" : "127.0.0.1", "1143")}>Put that host and port in the form</button>}
          </Step>
          <Step n={5} title="Keep the Bridge app running.">
            <span>The app reads mail only while this computer is on and the Bridge app is open. For mail around the clock, use the app's own Bridge on a server.</span>
          </Step>
        </ol>
      )}

      <div style={{ display: "flex", gap: 6, alignItems: "flex-start", fontSize: 12, color: "var(--ink)", borderTop: "1px solid var(--hairline)", paddingTop: 8 }}>
        <AlertTriangle size={15} color="var(--warning-ink)" aria-hidden style={{ flexShrink: 0, marginTop: 1 }} />
        <span><strong>Two passwords, never swapped.</strong> Your Proton password goes only into the Bridge's sign-in. The Bridge password goes only into this form.
          If Proton answers <em>Incorrect login credentials</em>, check the password at <Out href={PROTON_ACCOUNT}>account.proton.me</Out> in a private window before trying again: several failures in a row block sign-in for a while.</span>
      </div>
    </div>
  );
}
