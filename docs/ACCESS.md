# Access: what Claude reaches, what the owner grants

*Written 2026-10-14. The working rule: the coding agent does everything it can reach, and asks only for what needs the owner's identity. Specifics that must not be committed (addresses, account names, sender lists) live in `docs/INVENTORY.local.md`, which is gitignored.*

## What Claude can already reach from this PC

| Thing | How | State on 2026-10-14 |
| --- | --- | --- |
| This repository | `gh` CLI signed in as the owner; push to `main` | Works. |
| BigBox over the tailnet | Tailscale SSH as the `bigbox` user (docker group; sudo needs a password, so nothing here uses sudo) | Works since 2026-10-14. The app runs from `~/financialauthority` on BigBox by hand (compose, built from the shipped source) until the catalog entry lands. |
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

- **Plaid, Trial plan.** Create a developer account at Plaid's dashboard (free; it asks for a name and an app name). Copy the client id and the secret into the app's Connectors page, never into chat. The app then shows the bank's own login page for each institution; the first ten logins are free, and removing one does not free its slot, so connect the ones that matter: the bank and the credit union. Two more things Plaid wants, both in its dashboard: **production access** (the sandbox keys reach test banks only; ask for production on the Trial plan, then switch the app's environment to production and paste the production secret) and, for banks that sign in with OAuth (Capital One among them), the **redirect URI** the app shows in its next steps (the Connectors page at the published https address) added under Team Settings › API › Allowed redirect URIs. Until it is registered, the app still connects banks without OAuth.
- **Teller, developer tier.** Create a developer account at Teller (free up to a hundred logins, rate-limited). Download the application certificate it issues and paste the application id and the certificate into the Connectors page. The app uses Teller for whatever Plaid's trial does not cover.
- **Era Context, free tier.** One slot holds a healthy connection to the bank; the other is a redundant copy of the same bank that can be removed to make room for the credit union. The coding agent can remove the redundant one and produce the credit union's connection link on request; the sign-in on that link is the owner's.
- **Rocket Money's own mail.** Keep its weekly "This week's upcoming bills" and its balance and income alerts turned on in Rocket Money's notification settings, to an address the app reads. Each listed charge becomes a bill fact to confirm with one tap, so the bill schedule stays current without an export.
- **Affirm's own mail.** Affirm has no API and no export. Its "Your loan confirmation" email carries each loan's full schedule and cost, so the mailbox it lands in is the sync: keep those emails coming to an address the app reads, and each becomes a plan to confirm with one tap.
- **Bank alerts by mail.** In the bank's alert settings, turn on email alerts for every transaction, deposit, low balance and declined charge, to the address that lands in Proton. The app reads them the same quarter-hour.

### 3a. Retirement and investment accounts (M1.3)

The same Plaid keys reach the investment vendors: on the Investing page, **Sign in at the vendor** opens Plaid Link asking for the investments product, and Fidelity (NetBenefits), Vanguard, Schwab, Empower, Principal, Voya, TIAA, T. Rowe Price, John Hancock, Transamerica, Merrill, E*TRADE, Robinhood, Betterment, Wealthfront and Coinbase sign the owner in on their own page (their OAuth where they offer it) and hand back a read token with balances and holdings. A vendor Plaid does not reach (the Thrift Savings Plan, a pension) is added by hand with the balance from its last statement, and each statement email the vendor sends becomes a pending balance fact that updates it when confirmed. Nothing here asks for the vendor's password.

### 4. Gmail app password (M4.3)

Google Account, Security, 2-Step Verification, App passwords: create one named for the app and paste it into the Connectors page. Never into chat.

### 5. Proton Mail Bridge sign-in (M4.2)

The Bridge runs as a sidecar of the app on BigBox and needs the owner to sign in once, interactively, with the Proton password and second factor. The coding agent prepares the container and gives the exact command; the owner runs it over SSH from any device on the tailnet. The Bridge then prints a local IMAP password, which goes into the app's Connectors page. The sign-in survives restarts and is included in backups.

### 6. A public address (M3.3)

In BoxPilot, the Cloudflare Tunnel app's **Tunnel** tab: **Connect** with a Cloudflare API token holding Account · Cloudflare Tunnel · Edit, Zone · DNS · Edit and Zone · Zone · Read (BoxPilot asks for the owner's password; this is a high-risk operation there), then **Publish** FinancialAuthority under a name on one of the owner's domains. Tell the coding agent the name; it sets `FA_PUBLIC_URL`.

### 7. Connecting Claude (M3.4)

- claude.ai (web, desktop, phone): Settings, Connectors, Add custom connector, the public address with `/mcp`. The app's own consent page appears once: sign in with the owner's password, tick what the app may do (read; optionally confirm), Allow. Revoke it any time on Settings › Clients.
- Claude Code: `claude mcp add --transport http financialauthority https://<name>.<domain>/mcp`; the browser opens the same consent page once. Inside the tailnet, the BigBox address with port 8430 works the same way.
- Anything without a browser: an API key from Settings › Clients, sent as a bearer token.

### 8. Paystubs (M2.6)

Upload a paystub PDF on the Paystubs page, keep forwarding them to the Proton mailbox, or drop them into the `/imports/paystubs` share BoxPilot can publish on the LAN; each waits on Needs you for one tap. Tell the coding agent which payroll provider issues them, so the first parser is the right one.

### 9. Push notifications (M6.3)

Install ntfy or Gotify from BoxPilot's catalog and subscribe the phone to a topic, or use ntfy.sh with a long private topic name. Enter the server address, the topic and, if the server needs one, the token on Settings › Notifications, press Send a test, and the morning brief arrives from the chosen hour each day. Approvals for sends will arrive there too once sending exists (M4.5).

### 10. Send policy (M4.5)

The app ships with approve-each: a draft, from a model or from the Bills page, waits on Needs you and on the phone until the owner taps Approve and send. Ticking "always allow sends to this address" on an approval grants a standing allowance for that recipient; allowances are listed and removable on the same page. Sending goes through the mailbox's own SMTP (the Bridge, Gmail), so no extra account is needed.

### 11. A reviewer model (M3.7)

Optional. An Anthropic API key pasted into Settings › Models (never into chat) makes Claude the reviewer; set the reviewer's provider to Anthropic and press Test. Enter the model's prices per million tokens there and every review shows its cost in dollars; without them, tokens only. Any OpenAI-compatible endpoint works the same way. With no key, Ollama on BigBox advises and can review for nothing; the advisor uses it by default.

## What the coding agent never asks for

Passwords, second factors, bank logins, card numbers, government identifiers. Anything that must be typed into a sign-in form is typed by the owner. Values for the app's own test accounts come from the fixture, never from the owner.
