# Setting up FinancialAuthority

For someone installing it from this repository with nobody beside them. The same guide is inside the app at
**Settings › Setting up**, with the commands for your own install filled in.

Two words used throughout:

- **The server** is the machine FinancialAuthority runs on: a BoxPilot box, an Ubuntu server, or a Windows or
  Mac computer with Docker Desktop. You reach it from your own computer over SSH.
- **Your computer** and **your phone** are what you open the app on, in a browser.

A step that says *on the server* runs there, in a terminal connected over SSH, not on the computer you are
reading this on (unless that is the server).

## 1. Install

Pick one.

### On BoxPilot

App catalog › Finance › FinancialAuthority › Install. BoxPilot makes the three secrets (below), the folders,
the Proton Mail Bridge beside it, and backs up its data. Open it from its card; sign in with the username and
the password shown on the card.

### With Docker Compose on Linux (Ubuntu, Debian, Arch…)

1. Install Docker Engine: <https://docs.docker.com/engine/install/ubuntu/> (or your distribution's page).
2. Get the compose folder: copy `deploy/compose/` from this repository to the server, for example
   `~/financialauthority`.
3. Make the settings file: `cp .env.example .env`, then fill `FA_PASSWORD`, `FA_SESSION_SECRET` and
   `FA_DATA_KEY`, each with a long random value:

   ```bash
   openssl rand -base64 32
   ```

4. Start it: `docker compose up -d`. It answers on the server at port 8430 (`http://127.0.0.1:8430` from the
   server itself; see section 5 to reach it from elsewhere).

### With Docker Desktop on Windows or Mac

1. Install Docker Desktop: Windows <https://docs.docker.com/desktop/setup/install/windows-install/> (needs WSL 2),
   Mac <https://docs.docker.com/desktop/setup/install/mac-install/>. It is free for personal use.
2. Then the same steps as Linux, in PowerShell or Terminal. The app then runs only while this computer is on.

## 2. The app's own secrets

| Setting | What it is | Lose it and |
| --- | --- | --- |
| `FA_PASSWORD` | your sign-in password | change it in the settings and restart |
| `FA_SESSION_SECRET` | signs your sessions | everyone is signed out; nothing else |
| `FA_DATA_KEY` | encrypts the passwords and keys you paste into the app | the ledger stays, but every pasted password and key must be pasted again |

Keep a copy of `FA_DATA_KEY` with your backups. BoxPilot keeps all three on the app's card.

## 3. Mail

Bills, statements, paystubs and payment confirmations read themselves from a mailbox. The app only reads; it
never marks, moves or deletes.

### Proton Mail, through the Proton Mail Bridge

The Bridge is Proton's program that hands your mail to email apps. It needs a paid Proton plan (Mail Plus,
Unlimited, Duo, Family or Visionary): <https://proton.me/support/proton-plans>.

There are **two passwords**, and they are never swapped:

- your **real Proton password**, typed only into the Bridge's sign-in, once;
- the **Bridge password** the Bridge then makes, typed only into the app.

**The app's own Bridge, on the server (BoxPilot and Docker Compose):**

1. From your computer, connect to the server: `ssh you@your-server`.
2. Pause the Bridge (its container is `bp-financialauthority-bridge` on BoxPilot, `financialauthority-bridge`
   with Docker Compose):

   ```bash
   sudo docker stop financialauthority-bridge
   ```

3. Open the Bridge's prompt, with its own image and saved state:

   ```bash
   sudo docker run --rm -it --volumes-from financialauthority-bridge --entrypoint protonmail-bridge $(sudo docker inspect -f '{{.Config.Image}}' financialauthority-bridge) --cli
   ```

   A drawing of a bridge appears, then a `>>>` prompt. A warning about *dbus* is normal.
4. Type `login`:
   - **Username:** your Proton address.
   - **Password:** your real Proton password, the one you use at <https://account.proton.me>. Nothing shows as
     you type; type it rather than paste it.
   - **A link to verify.proton.me:** open it in your browser, solve the check, come back, press Enter.
     Several failed sign-ins in a row block sign-in for a while; check the password at account.proton.me in a
     private window before trying again.
   - **"Do you want to use a security key?"** Type `no`, then the six-digit code from your authenticator app. A
     hardware key on your own computer cannot reach the server; add an authenticator app in Proton first if
     that is your only second step.
   - **A mailbox password** is asked only if your account has two passwords.
   - It says *Account … was added successfully* and starts copying your mail (it can take hours; you need not
     wait).
5. Type `info`. Copy the **Username** and the **Password** it shows: that password is the Bridge password.
   Ignore its 127.0.0.1 and 1143.
6. Type `exit`, then start the Bridge again:

   ```bash
   sudo docker start financialauthority-bridge
   ```

7. In the app: Settings › Connectors › Mailboxes › Proton Mail, through the Bridge. Paste the username and the
   Bridge password; leave host `bridge` and port `143`. Test first, then Save.

If your user is in the `docker` group, leave out `sudo`.

**The Bridge app on the same Windows or Mac computer** (only when the app runs there in Docker Desktop):
install it from <https://proton.me/mail/bridge>, sign in there, then select your account › Mailbox details and
copy the username and IMAP password. In the app use host `host.docker.internal`, port `1143`. Mail is read only
while that computer is on and the Bridge app is open.

### Gmail

An app password: 16 letters Google makes for one app, not your Google password. It needs 2-Step Verification:
<https://myaccount.google.com/apppasswords>. Username: your Gmail address. Gmail's IMAP is always on.

### Any other mailbox

Your provider's help pages list its IMAP server and port (usually 993); many want an app password.

## 4. Your bank

Live transactions come through **Plaid** or **Teller** with free developer keys you make once. Your bank's own
login opens in their window; the app never sees your bank password and never moves money.

- **Plaid:** sign up at <https://dashboard.plaid.com/signup>, apply for the free Trial plan
  (<https://dashboard.plaid.com/trial-plan>: ten real bank logins over the life of your account; removing one
  does not free it), copy the client id and the Production secret from <https://dashboard.plaid.com/developers/keys>.
  For banks that sign in with OAuth (Capital One, Chase…), add `https://<your-address>/settings/connectors`
  under API › Allowed redirect URIs (<https://dashboard.plaid.com/developers/api>); this needs section 5 first.
- **Teller:** a developer account from <https://teller.io> gives `teller.zip` with a certificate and key;
  the application id is at <https://teller.io/settings/application>. Free for a hundred bank logins.

The app reads each bank every four hours by default (Connectors › Banks › How fresh). On Plaid's Trial that
costs nothing; on a paid plan Plaid bills transactions per bank login per month, however often it is read.
A bank's own CSV or OFX download can always be imported instead (Connectors › Import a file); an import can be
undone on Settings › Backups.

## 5. Reach it from anywhere

On your home network the app is at the server's address, port 8430. To use it away from home, and for Claude on
the web or the phone, it needs an https address:

- **BoxPilot:** install the Cloudflare Tunnel app, publish FinancialAuthority, and put the address it gives in
  the app's `FA_PUBLIC_URL` on its card.
- **Elsewhere:** Tailscale (<https://tailscale.com/download>) gives your devices private https addresses with no
  ports opened; or any reverse proxy with a certificate. Set `FA_PUBLIC_URL` and restart.

## 6. Your phone

Open the app's address in Safari (iPhone, iPad) or Chrome (Android) and add it to the Home Screen. On iPhone:
the … menu › Share › Add to Home Screen › turn on Open as Web App › Add
(<https://support.apple.com/guide/iphone/open-as-web-app-iphea86e5236/ios>). Web notifications need iOS 16.4 or
later and the Home Screen app. Then Settings › Notifications in the app.

## 7. Models and Claude

- **The built-in chat** (Settings › Models): Ollama on the server, free (<https://ollama.com/download>, then
  `ollama pull llama3.2`); any OpenAI-compatible server; or Claude with an Anthropic API key from
  <https://platform.claude.com/settings/keys> (billed per use, separate from a claude.ai subscription).
- **Claude reading your plan:** claude.ai or the Claude desktop app › Customize › Connectors › + Add › Add
  custom connector, with `https://<your-address>/mcp`; the app's sign-in page opens once. Claude Code:

  ```bash
  claude mcp add --transport http financialauthority https://<your-address>/mcp
  ```

  then `/mcp` inside Claude Code to sign in.

## 8. Backups

The app keeps its own copies of its database: before every import, once a day, and on request
(Settings › Backups). Any copy can be put back; a copy of the moment before is kept first. On BoxPilot the app's
data is also archived with the server's backups, and the app's sheet has *Take a restore point* and *Check the
database and its copies*. Keep `FA_DATA_KEY` with your backups.

**A sealed copy off the disk.** On Settings › Backups, choose a folder on another disk (a USB drive the server
mounts, or a folder Syncthing or rclone syncs to a cloud: `/offbox` inside the app, set on BoxPilot's card or
with `FA_OFFBOX_HOST_DIR` in `.env`) and a passphrase only you know. The app seals a copy there daily or weekly;
without the passphrase nobody can open it. A weekly drill opens the newest copy on its own and proves it holds
what it should. The page's **Recovery kit** says what to keep and how to bring everything back:

```bash
docker compose run --rm financialauthority /app/backend/.venv/bin/python -m app.offbox open /offbox/<copy>.fabackup /data/financialauthority.db
```

## 9. Updating

- **BoxPilot:** the app's card offers the new version; it backs up first.
- **Docker Compose:** set `FA_IMAGE_TAG` in `.env` to the new version, then `docker compose pull` and
  `docker compose up -d`.

The app never moves money, sends nothing without your approval, and reads every source read-only.
