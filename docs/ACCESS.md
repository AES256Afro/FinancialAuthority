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

The Bridge runs as a sidecar of the app on BigBox and needs the owner to sign in once, interactively, with the Proton password and second factor. The coding agent prepares the container (the image is pulled) and gives the exact command; the owner runs it over SSH from any device on the tailnet:

1. `ssh -t` to the server, then in the app's folder: `docker compose run --rm bridge init`. At the Bridge prompt: `login`, then the Proton address, password and code; `info` prints the IMAP username and password; `exit`.
2. `docker compose --profile bridge up -d bridge` starts it for good.
3. On Connectors › Mailboxes: remove the Proton row and add it again with the username and password `info` printed (not the Proton password). Host `bridge` and port 143 are already filled: the Bridge's screen says 1143, which inside the server is 143, and the app accepts either.

Installed from the BoxPilot catalog, the Bridge's state folder is the app's `bridge` volume (`/var/lib/boxpilot-managed/catalog/financialauthority/bridge` on the server). If the mailbox says "The Bridge has no account signed in" after a reinstall or a move between the hand-run copy and the catalog install, the sign-in is in the other copy's folder: stop both Bridges, copy that folder (it holds `.config`, `.gnupg`, `.password-store` and `.local`) over this one, start the sidecar, and the next quarter-hour read succeeds with the same username and password. 2026-10-29: this is what had stopped mail for a week; the fix needed no new sign-in.

Run `init` only once: a second run makes a new keychain key. To see the username and password again, stop the sidecar (`docker compose --profile bridge stop bridge`), open the same screen with `docker compose run --rm --entrypoint protonmail-bridge bridge --cli`, type `info`, `exit`, and repeat step 2.

Until step 1 is done the mailbox shows "The Bridge is not running on the server yet". The Bridge's screen also prints host 127.0.0.1; that is the Bridge speaking from inside its own container, and the app takes it to mean `bridge`. The sidecar is built from `deploy/bridge/Dockerfile`, the community image plus `libfido2`, because the Bridge updates itself and its newer releases need that library. The sign-in survives restarts and is included in backups.

### 6. A public address (M3.3)

In BoxPilot, the Cloudflare Tunnel app's **Tunnel** tab: **Connect** with a Cloudflare API token holding Account · Cloudflare Tunnel · Edit, Zone · DNS · Edit and Zone · Zone · Read (BoxPilot asks for the owner's password; this is a high-risk operation there), then **Publish** FinancialAuthority under a name on one of the owner's domains. Tell the coding agent the name; it sets `FA_PUBLIC_URL`.

### 7. Connecting Claude (M3.4)

- claude.ai (web, desktop, phone): Customize, Connectors, + Add, Add custom connector, the public address with `/mcp`. The app's own consent page appears once: sign in with the owner's password, tick what the app may do (read; optionally confirm), Allow. Revoke it any time on Settings › Clients.
- Claude Code: `claude mcp add --transport http financialauthority https://<name>.<domain>/mcp`; the browser opens the same consent page once. Inside the tailnet, the BigBox address with port 8430 works the same way.
- Anything without a browser: an API key from Settings › Clients, sent as a bearer token.

### 8. Paystubs (M2.6)

Upload a paystub PDF on the Paystubs page, keep forwarding them to the Proton mailbox, or drop them into the `/imports/paystubs` share BoxPilot can publish on the LAN; each waits on Needs you for one tap. Tell the coding agent which payroll provider issues them, so the first parser is the right one.

### 9. The app on the phone, the tablet and the watch (M15)

The app installs from Safari and sends its own notifications; no store, no ntfy, nothing paid.

1. **iPhone and iPad** (iOS 16.4 or later): open the tailnet https address in Safari, tap Share, Add to Home Screen, Add. Open it from the Home Screen icon, sign in once, then Settings › Notifications › Turn on notifications here, and Allow.
2. **Apple Watch**: nothing to do; it shows what the iPhone shows.
3. **Reminders**: on the same page, choose which (a bill some days before, payday, a card's statement day, the Sunday check-in) and from what hour.
4. **Siri, a widget, the watch face**: on Settings › Clients make a key with the read scope only, then follow "Ask Siri" on Settings › Notifications: one Shortcut that reads `/api/v1/glance` and speaks its sentence. Add it as a Shortcuts widget or a watch complication.
5. **The calendar**: on Exports, make the calendar feed address and add it in the phone's Settings › Calendar › Accounts › Add Subscribed Calendar.

### 9a. ntfy or Gotify, instead or as well (M6.3)

Install ntfy or Gotify from BoxPilot's catalog and subscribe the phone to a topic, or use ntfy.sh with a long private topic name. Enter the server address, the topic and, if the server needs one, the token on Settings › Notifications, press Send a test, and the morning brief arrives from the chosen hour each day. Approvals for sends will arrive there too once sending exists (M4.5).

### 10. Send policy (M4.5)

The app ships with approve-each: a draft, from a model or from the Bills page, waits on Needs you and on the phone until the owner taps Approve and send. Ticking "always allow sends to this address" on an approval grants a standing allowance for that recipient; allowances are listed and removable on the same page. Sending goes through the mailbox's own SMTP (the Bridge, Gmail), so no extra account is needed.

### 11. A reviewer model (M3.7)

Optional. An Anthropic API key pasted into Settings › Models (never into chat) makes Claude the reviewer; set the reviewer's provider to Anthropic and press Test. Enter the model's prices per million tokens there and every review shows its cost in dollars; without them, tokens only. Any OpenAI-compatible endpoint works the same way. With no key, Ollama on BigBox advises and can review for nothing; the advisor uses it by default.

### 12. Housing and documents (M37, M38)

Housing (in the menu under Plan) holds each home: what is paid with the rent each month, the bills of its own, the lease's end and the notice it needs, and each utility and where it comes from. Moving home shows only the cost of getting into the next place. What only you can add: amounts the app cannot know (a renter's insurance quote, the first water bill), whether a fee was already paid, the day you gave notice (tap "I gave notice"), and your documents: open a home on Housing, choose Documents, and upload the lease or the property's charge sheet (PDF, photo, spreadsheet or text, 25 MB at most). The terms a lease states then wait on Needs you until you tap Apply.

### 13. A second person (M33)

Settings › People adds someone with a role: **Everything** (sees and does what you do, except adding people, erasing, restoring, connecting apps and approving your sends) or **Shared** (one page: the accounts you mark shared, and theirs). The app shows a one-time link; send it to them yourself. They choose their own password there; you never see it. Mark accounts Shared or theirs on the same page or on Accounts. If they lose their phone or password, press New link: it signs them out and lets them start again.

### 14. Receipts (M31)

Forward a store's receipt email to a connected mailbox, or add a photo or PDF on Documents. It waits on Needs you with the purchase it matches; Keep the receipt attaches it. Its last day to return and its warranty come back as reminders (Settings › Notifications sets how many days before).

### 15. BoxPilot's cloud copy of the backups (round thirteen, 2026-10-29)

BoxPilot mirrors its backups to the cloud bucket every night at 04:15 UTC, unencrypted, and the app's archives hold `FA_DATA_KEY` beside the database it seals. The next mirror would also carry the Bridge's vault and the key that opens it. In order:

1. Done by the coding agent on 2026-10-29 at 14:29 UTC: the nightly cloud mirror schedule is switched off (BoxPilot › Backups › Schedules shows it off), so nothing more has gone up. The encrypted mirror is BoxPilot pull request #599; once it is merged and BoxPilot updated, it refuses to copy until step 2 is done.
2. With the encrypted mirror: Backups › Off-box › Cloud bucket › Change destination; enter the bucket's keys again and an encryption passphrase of at least 12 characters. Keep the passphrase in your password manager: without it nobody, you included, can read the copies.
3. In the bucket provider's dashboard, delete the plain copies an older mirror left: the folders `application-backups` and `machine-snapshots` beside the new `sealed` folder (the encrypted copies go only there).
4. Decide whether to rotate what those copies held, depending on who could have read the bucket: `FA_SESSION_SECRET` and `FA_PASSWORD` from the app's BoxPilot settings (a new session secret signs everyone out); the keys sealed under `FA_DATA_KEY` at their sources, since the data key itself cannot be rotated yet (M8.5): the Plaid and Teller links (remove and link again), a Gmail app password, and any model keys.
5. Optional, on GitHub: a tag ruleset that lets only you create `v*` tags, since a tag publishes the image the catalog pins.

### 16. The other ways to the app's secrets (round fourteen, 2026-10-29)

Round fourteen looked for every path to `FA_DATA_KEY` besides the cloud mirror. Four need you; do them in this order.

1. **Dockge: done.** It had no admin account, so the first visitor would have become root on the server. On the owner's word it was uninstalled through BoxPilot on 2026-10-29 at 17:12 UTC, keeping its small data folder (`/var/lib/boxpilot-managed/catalog/dockge`; `/opt/stacks` was empty, so no stack was lost). One leftover: Tailscale Serve still forwards the tailnet's port 5001 to the server's 127.0.0.1:5001, where nothing listens now; remove it with `tailscale serve --https=5001 off` so a later app on that port is not published by accident.
2. **The backup drive is readable by every user on the server.** BoxPilot's nightly "Copy backups to the backup drive" (04:15 UTC) copies every app archive, each FinancialAuthority one with its `.env` beside the database it seals, onto the NAS share mounted at `/mnt/boxpilot/backup`, and that share is mounted with uid 1000 and mode 0664, so any account or service on the server can read them. BoxPilot pull request #602 mounts a new backup share for root alone; the existing line has to be changed once, as root on the server:
   1. `cp /etc/fstab /etc/fstab.bak-round14`
   2. `sed -i '/ \/mnt\/boxpilot\/backup cifs /s/uid=1000,gid=1000,file_mode=0664,dir_mode=0775/uid=0,gid=0,file_mode=0600,dir_mode=0700/' /etc/fstab`
   3. `findmnt --verify` (no errors), then `systemctl daemon-reload`
   4. Outside the 04:15 copy: `systemctl stop mnt-boxpilot-backup.mount`, then `ls -ld /mnt/boxpilot/backup` mounts it again and shows `drwx------ root root`.
   5. `runuser -u nobody -- ls /mnt/boxpilot/backup` must answer "Permission denied". The `Win2CachyOS` folder on the same share is then root's to read on this server too.
3. **The same copies sit unencrypted on the NAS.** Ten FinancialAuthority archives and every Portainer archive (Portainer's database keeps each container's environment, `FA_DATA_KEY` included) are in `boxpilot-local-mirror/application-backups` on that share. Whoever can open the share, on the NAS, in the NAS maker's app or its remote access, can unseal the database. On the NAS: make sure that folder is reachable by your account alone and that the NAS maker's cloud or remote access does not reach it. Encrypting this copy the way the cloud mirror now is is a BoxPilot follow-up, not yet built.
4. **One password is used in many places.** `FA_PASSWORD` is also the password of all five NAS shares BoxPilot mounts, of BaselineWatch's admin (published to the internet through the tunnel), of the two Plaud apps, and sits in Portainer's database. Give FinancialAuthority a password of its own: in BoxPilot open FinancialAuthority, press Settings, replace Owner password, apply, then sign in with it (every session ends). Then change the NAS account's password on the NAS and in BoxPilot › Storage for each share, and give BaselineWatch its own.

Optional, smaller:

5. Portainer listens on every interface (port 9443, the LAN included) and also holds the Docker socket. If Dockge and BoxPilot cover what you use it for, uninstall Portainer from BoxPilot; that also stops its database and backups collecting every app's environment.
6. The hand-run install from before BoxPilot is still in `/home/bigbox/financialauthority` (an older database and a `.env` with the same `FA_DATA_KEY`) and its stopped containers `financialauthority` and `financialauthority-bridge` still carry that environment. When BoxPilot's install has run a week without needing them: `docker rm financialauthority financialauthority-bridge`, then move that folder off the server or delete it.
7. SSH on port 22 accepts passwords from the LAN, and the `bigbox` account has a password and sudo. Root already comes in through Tailscale SSH, so a file `/etc/ssh/sshd_config.d/10-no-passwords.conf` holding `PasswordAuthentication no` (it must sort before the existing `50-cloud-init.conf`, since the first setting wins), checked with `sshd -t` and applied with `systemctl reload ssh`, closes the LAN guess.
8. Before taking a machine snapshot, know that it holds every app's `.env` and goes to the same backup drive (none has been taken since FinancialAuthority was installed). The SSH mirror (Backups › Off-box › SSH) is not set up; if you set it up, it copies the same archives unencrypted to the other machine.

## What the coding agent never asks for

Passwords, second factors, bank logins, card numbers, government identifiers. Anything that must be typed into a sign-in form is typed by the owner. Values for the app's own test accounts come from the fixture, never from the owner.
