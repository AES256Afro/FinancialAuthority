# Access: what Claude reaches, what the owner grants

*Written 2026-10-14. The working rule: the coding agent does everything it can reach, and asks only for what needs the owner's identity. Specifics that must not be committed (addresses, account names, sender lists) live in `docs/INVENTORY.local.md`, which is gitignored.*

## What Claude can already reach from this PC

| Thing | How | State on 2026-10-14 |
| --- | --- | --- |
| This repository | `gh` CLI signed in as the owner; push to `main` | Works. |
| BigBox over the tailnet | Tailscale is up on this PC; BigBox is online; Tailscale SSH is in use | Reachable. The tailnet policy already permits SSH as the PC's username, but that account on BigBox has no login shell, so every attempt ends with "This account is currently not available". |
| Docker builds | GitHub Actions (there is no Docker on this PC) | Works once the workflow exists (M0.2). |
| Gmail | The claude.ai Gmail connector: search, read, draft, send | Works. |
| Proton Mail | The `protonmail` MCP server through Proton Mail Bridge on this PC: list, search, read, draft | Works while this PC and the Bridge are running. Not always-on; the Bridge sidecar on BigBox fixes that (M4.2). |
| Transactions, historical | The `rocketmoney` MCP server over CSV exports | Works; the newest export is about two weeks old. |
| Transactions, live | The Era Context connector | Works for one bank on a free tier with a short history window. |
| Credit factors | The Credit Karma connector | Works from Claude only. |
| Calendar | The Google Calendar connector | Works. |
| Local toolchain | Python 3.14, uv, Node 24, gh | Present. |

## What only the owner can grant

Each item says when it is needed, so nothing has to be done early.

### 1. A shell on BigBox (needed first, M0.1)

Tailscale SSH already lets this PC in; the Linux account just needs a shell and the right groups. Either in BoxPilot, **Users & SSH**: add a user for the coding agent, give it sudo, and import this PC's public key from GitHub (or paste it; it is `~/.ssh/id_ed25519.pub` here). Or on BigBox's console, giving the existing locked account a shell:

```bash
sudo usermod -s /bin/bash <username> && sudo usermod -aG sudo,docker <username>
```

Then, if the tailnet SSH policy names users rather than allowing any non-root user, add the new username to it. Check from this PC with:

```bash
ssh <username>@bigbox 'whoami && docker ps --format "{{.Names}}" | head'
```

### 2. The image's visibility (M0.2)

BoxPilot's catalog cannot pull from a private registry. The published package `ghcr.io/aes256afro/financialauthority` must be public; the repository can stay private. The first publish creates the package private; make it public once in GitHub, Packages, Package settings, Change visibility. The image holds code and no data.

### 3. Bank data, at no cost (M1.1)

Three free sources, stacked. Do the first two when M1 starts; the third is already half done.

- **Plaid, Trial plan.** Create a developer account at Plaid's dashboard (free; it asks for a name and an app name). Copy the client id and the secret for the Trial plan into the app's Connectors page, never into chat. The app then shows the bank's own login page for each institution; the first ten logins are free, and removing one does not free its slot, so connect the ones that matter: the bank and the credit union.
- **Teller, developer tier.** Create a developer account at Teller (free up to a hundred logins, rate-limited). Download the application certificate it issues and paste the application id and the certificate into the Connectors page. The app uses Teller for whatever Plaid's trial does not cover.
- **Era Context, free tier.** One slot holds a healthy connection to the bank; the other is a redundant copy of the same bank that can be removed to make room for the credit union. The coding agent can remove the redundant one and produce the credit union's connection link on request; the sign-in on that link is the owner's.
- **Bank alerts by mail.** In the bank's alert settings, turn on email alerts for every transaction, deposit, low balance and declined charge, to the address that lands in Proton. The app reads them the same quarter-hour.

### 4. Gmail app password (M4.3)

Google Account, Security, 2-Step Verification, App passwords: create one named for the app and paste it into the Connectors page. Never into chat.

### 5. Proton Mail Bridge sign-in (M4.2)

The Bridge runs as a sidecar of the app on BigBox and needs the owner to sign in once, interactively, with the Proton password and second factor. The coding agent prepares the container and gives the exact command; the owner runs it over SSH from any device on the tailnet. The Bridge then prints a local IMAP password, which goes into the app's Connectors page. The sign-in survives restarts and is included in backups.

### 6. A public address (M3.3)

In BoxPilot, the Cloudflare Tunnel app's **Tunnel** tab: **Connect** with a Cloudflare API token holding Account · Cloudflare Tunnel · Edit, Zone · DNS · Edit and Zone · Zone · Read (BoxPilot asks for the owner's password; this is a high-risk operation there), then **Publish** FinancialAuthority under a name on one of the owner's domains. Tell the coding agent the name; it sets `FA_PUBLIC_URL`.

### 7. Connecting Claude (M3.4)

- claude.ai (web, desktop, phone): Settings, Connectors, Add custom connector, the public address with `/mcp`. The app's own sign-in page appears once per device.
- Claude Code: one command, documented in M3.4; the browser opens the same sign-in once.

### 8. Paystubs (M2.6)

Keep forwarding paystub PDFs to the Proton mailbox, or drop them into the `/imports/paystubs` share BoxPilot can publish on the LAN. Tell the coding agent which payroll provider issues them, so the first parser is the right one.

### 9. Push notifications (M6.3)

Install ntfy or Gotify from BoxPilot's catalog and subscribe the phone to a topic; tell the coding agent the topic. Approvals for sends arrive there with an approve action.

### 10. Send policy (M4.5)

Decide the default: approve each send, or grant standing allowances to known recipients as they come up. The app ships with approve-each and lets allowances be granted one tap at a time.

### 11. A reviewer model (M3.7)

Optional. An Anthropic API key pasted into Settings › Models makes Claude the reviewer; reviews cost cents and the app shows each one's cost. Any OpenAI-compatible endpoint works the same way. With no key, the local model advises and reviews for nothing.

## What the coding agent never asks for

Passwords, second factors, bank logins, card numbers, government identifiers. Anything that must be typed into a sign-in form is typed by the owner. Values for the app's own test accounts come from the fixture, never from the owner.
