# Security: the threat model and what the app does about it

*Written 2026-10-14 (M8.3, first cut). This is the document a reviewer reads before trusting the app with a household's money picture. It states what the app holds, who can reach it, what could go wrong, and the control for each. Where a control is not built yet, it says so.*

## What the app holds

- The ledger: transactions, balances, bills, debts, installment plans, advances, paystubs (gross, net, deductions), the registers the engine plans from.
- Read tokens for banks (Plaid access tokens, Teller access tokens), mailbox passwords (Proton Bridge, Gmail app password), model API keys, push tokens. Each is encrypted at rest with AES-GCM under a key derived from `FA_DATA_KEY`; the key lives only in the environment.
- Message references and short excerpts from the mailboxes; never whole bodies, never attachments beyond the paystub lines extracted.
- Receipts kept from mail are fetched once, read-only, when the owner confirms one, and stored like an upload; statements and paystubs handed over are kept with their month.
- Documents the owner uploads (a lease, a charge sheet, a utility's terms, receipts): each file encrypted with the data key under the data folder, its text kept encrypted page by page. The file itself downloads to the owner only; models read the text only through `documents_search` and `document_read`, boxed as untrusted, and only with the full redaction level. The terms a lease states wait on Needs you, and only the owner signed in applies them. Erasing everything removes the files too. Nothing is used to train a model.
- OAuth client registrations and hashed tokens; hashed API keys; the owner's password hash; the audit log.

It never holds a bank password, a card number in the clear, or a government identifier.

## Who can reach it

| Principal | How | May |
| --- | --- | --- |
| The owner | A passkey (at the https address), or the password and, once turned on, a six-digit code from an authenticator app (each code accepted once; ten hashed recovery codes); then a session after the sign-in form, recorded on the server and ended by signing out (Settings › Clients lists every signed-in device and ends any); CSRF header on writes; a lockout per address and a doubling wait per account | Everything, including approve |
| A second person | A one-time link from the owner (three days, used once), where they choose their own password (scrypt; the owner never sees it); then the same sign-in as the owner, with their own second step, passkeys and sessions; a new link clears all three and ends their sessions | "Everything": what the owner may, except adding people, erasing, restoring, the off-box copy, connecting apps through OAuth and approving the owner's sends. "Shared": one page, the accounts marked shared and their own. Each person approves only their own sends; every write and sign-in is logged |
| A connected app (claude.ai, Claude Code, Open WebUI) | OAuth 2.1: dynamic registration (at most twenty a day and a hundred live), authorization code with PKCE, a consent page that names where it sends you and warns when that is not Claude, the owner's sign-in, access tokens for a day, refresh tokens for ninety, rotated | `read`, and `confirm` only if granted on the consent page; never `approve` or `admin` |
| A script | API key as a bearer token, scoped at creation; a key that hides details reads through `/mcp` only, and every REST read by a key is in the activity log | `read`, `confirm`; never `approve` or `admin`; never the built-in chat |
| The reviewer model | Called by the app, never calling in | Sees the plan, rules, goals, alerts; never mail, attachments, account names or numbers |
| The advisor model | Called by the app in the chat | The read tools, redaction applied; cannot confirm, approve or send |
| Plaid | `POST /api/plaid/webhook`: a signed notice (ES256 over the body's SHA-256, five minutes at most, key fetched from Plaid by id) | Start a sync of a link the owner already made; nothing else |
| Anyone else | The public surface: the sign-in page, `/mcp`, `/api`, the OAuth endpoints (`/authorize`, `/token`, `/register`, `/revoke`, `/oauth/consent`), `/.well-known/*`, `/api/health` (up or not, no version) | Nothing without a credential |

No tool, job or button initiates a payment, a transfer or an email. Push notifications go to the owner's own ntfy or Gotify.

## What could go wrong, and the control

### A hostile email tries to steer a model

Mail is read by rules first; a model sees message text only boxed as data, with a frame that says where it came from and that it is not an instruction. Facts extracted from mail are pending until the owner confirms them; a `read` credential cannot confirm. The reviewer never sees mail at all. *Test suite: the malicious-mailbox cases in `backend/tests/test_mail.py`; grows under M8.4.*

### A hostile file tries to break the reader

Documents and attachments are untrusted. A PDF is read under a page, stream and time budget, and a spreadsheet's unzipped size and rows are checked before it opens. A photo or a scanned PDF is read by Tesseract (with poppler's pdftoppm drawing a scan's pages) only as a separate process with no shell, a 25-second limit per picture and 60 seconds per file, a 1 GB memory cap and a CPU cap, and a 15 MB size limit; one that cannot be read in time simply keeps no text. What is read from a receipt photo is offered to keep, never applied on its own. *Test: `backend/tests/test_ocr.py`, `backend/tests/test_security3_data.py`.*

### A forged email tries to slip a bill or a loan into the plan

Every message is judged as it arrives from what it already carries: the receiving server's DMARC, DKIM and SPF verdict, whether a sender calling itself a known company comes from that company's own domains (a table of common senders plus the domains the owner's own verified mail used), and look-alike domains (a swapped character, a bait word such as "-alerts"). A message judged forged keeps its items out of "answer together", its cards say why, and the data check asks the owner to discard it or mark it genuine. Mail already waiting is judged from its headers, fetched read-only. Bulk confirming follows one plan shown before it runs: only a bill's newest notice counts, an old notice never starts a bill, and a notice from someone counted another way (Housing, Debts, Borrowing) never becomes a second bill. *Test: `backend/tests/test_datacheck.py`, `backend/tests/test_needsyou.py`.*

### Business records hold identifiers

A business is kept without its EIN: only whether one is on file. Invoices print from escaped values under a page policy that runs nothing. Tax rules are shown with their official sources, as general rules, never advice. *Test: `backend/tests/test_business.py`.*

### A model invents a number

Every figure in a reply is checked against the numbers the engine returned for that conversation; anything else is marked on screen and in the API (`unverified`). The engine computes; models explain. *Test: `backend/tests/test_llm.py`, with a scripted model that invents a figure.*

### A token leaks

Bank and mail tokens are encrypted at rest and decrypted only for the call that needs them. API keys and OAuth tokens are stored hashed. A leaked API key or OAuth token can read and, if scoped, confirm; it cannot approve, cannot change settings, cannot reach the keys page. Revocation is immediate: an app's revoke removes every token it holds. *Left: rotation of `FA_DATA_KEY` (M8.5).*

### The public address is attacked

The sign-in form locks out after repeated failures; the OAuth consent page shares the lockout. The MCP transport refuses unknown hosts when DNS-rebinding protection is on (it is off only behind the reverse proxy that sets the host). Rate limits at the edge come from Cloudflare when published through the tunnel. *Left: verification from outside once published (M3.3), rate limits inside the app (M8.4).*

### A vendor is compromised or changes behaviour

Plaid and Teller hold the bank sessions; the app holds only read tokens that the owner can revoke at the vendor and in the app. The app pulls; it exposes no webhook endpoint yet, so there is nothing for a vendor to push into. Ollama is reached on the host; Anthropic and OpenAI-compatible servers are reached only with the owner's own keys, with the plan and never the mail.

### The owner's machine is lost

Nothing lives on the device: the app runs on the home server, data under `/data`, backed up by BoxPilot with the secrets encrypted inside. A phone that is lost still needs the app's sign-in; connected apps are revoked on Settings › Clients from any other device.

### Logs or the bundle leak a secret

Secrets never reach logs; the frontend receives only "set" or "not set" for any secret. The audit log records who called which tool and how many rows, never the rows. *Test: the settings routes assert the secret is absent from every response.*

## Secret handling, summarised

- `FA_DATA_KEY`, `FA_SESSION_SECRET`, `FA_PASSWORD`: environment only; BoxPilot generates them at install.
- Everything the owner pastes into the app (tokens, keys, certificates, passwords): encrypted with the data key, never shown again, replaced or removed from the same form.
- Nothing is committed: the repository holds a synthetic household and no real address, account, balance or merchant.

## Dependency audit

CI runs pip-audit over the locked Python dependencies and `npm audit --omit=dev --audit-level=high` over the frontend on every push and every Monday, and the weekly run rebuilds `:edge` so a base-image fix lands without a code change. A grep in the same job fails the build if a committed file holds an `FA_` secret, a PEM private key or an API key. 2026-10-15: both audits clean.

## The hardening sweep (M14), 2026-10-15

Each line is a test in `backend/tests/test_hardening.py` or `test_exports.py`, or a check run against the app from outside.

| Control | How it is held | Checked |
|---|---|---|
| Security headers | `app/security.py`, plain ASGI: nosniff, no referrer, `X-Frame-Options: DENY`, a permissions policy, HSTS over https; the pages get a content-security policy (scripts from the app and Plaid Link only, no plugins, no outside forms, no framing); the OAuth consent page gets its own, whose form rule allows the client's https or local callback | test, and `curl -I` against BigBox |
| Body ceiling | requests over 60 MB refused with 413, whether they state a length or arrive in chunks; each upload route still refuses over 50 MB | test |
| Refresh tokens | rotated on every use; a spent one presented again more than a minute later ends every token that client holds | test |
| Small things from the first sweep | the calendar token is hidden in the access log and its limit counts the real address; the Teller key is written only to a private runtime folder, never the data folder; a manual account keeps four digits at most; one recipient per approval, as the mail library reads it; no cross-origin access by default; OFX blocks found in one pass; PDFs read to forty pages; base images pinned by digest, with Dependabot proposing new ones | test |
| Restores | a restore puts the money data back but keeps keys, connected apps, sessions, approvals, the calendar feed and the activity log as they are now | test |
| Mail in the plan | sender names and subjects reach the plan and the brief on one short line, and the advisor is told they are written by the sender | test |
| Malformed uploads | empty, truncated, binary and script-laden files to every upload route answer under 500 | test |
| Session cookie | HttpOnly, SameSite=Lax, Secure whenever the request came over https; writes need the app's own header | test |
| Sign-in | lockout after repeated failures per address | test (existing); not re-run against the live box, since it would lock the owner's own address out |
| Secrets in logs | the jobs run with a mailbox password and a Plaid secret on file; neither, nor any `FA_` secret, appears in the logs or the job records | test |
| Secrets in exports | every one of the twelve export formats is scanned for the same secrets and for any encrypted blob | test |
| Exports | owner session only (an API key gets 403), `Cache-Control: no-store`, an audit row each | test |
| The calendar feed | the one unauthenticated route: a 256-bit token stored hashed, replaced or revoked in one tap, amounts off by default, sixty requests an hour per address | test, and PLAN section 12 |
| Appearance | colours checked against the palette rules before they are kept; a mark must be a real PNG, JPEG, WebP or a plain SVG (scripts, handlers and outside links refused), under 300 KB | test |
| Report stylesheets | styling only: imports, urls, scripts and bindings refused; previews render in a sandboxed frame with no scripts | test |
| Every page at three widths | phone, tablet and desktop, no sideways scroll, no console error: 29 routes | browser pass, 2026-10-15 |
| Dates at the edges | the 31st in February and in a leap year, a year turning over, semi-monthly paydays on a weekend, rent on the 1st paid from the check before | test |
| Keyboard reach | a visible focus ring on every control; status always carries an icon and a word | CSS and review |
| Plain words | a glossary of the twenty-two words the app uses, linked from every page's header | page |

