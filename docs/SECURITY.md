# Security: the threat model and what the app does about it

*Written 2026-10-14 (M8.3, first cut). This is the document a reviewer reads before trusting the app with a household's money picture. It states what the app holds, who can reach it, what could go wrong, and the control for each. Where a control is not built yet, it says so.*

## What the app holds

- The ledger: transactions, balances, bills, debts, installment plans, advances, paystubs (gross, net, deductions), the registers the engine plans from.
- Read tokens for banks (Plaid access tokens, Teller access tokens), mailbox passwords (Proton Bridge, Gmail app password), model API keys, push tokens. Each is encrypted at rest with AES-GCM under a key derived from `FA_DATA_KEY`; the key lives only in the environment.
- Message references and short excerpts from the mailboxes; never whole bodies, never attachments beyond the paystub lines extracted.
- OAuth client registrations and hashed tokens; hashed API keys; the owner's password hash; the audit log.

It never holds a bank password, a card number in the clear, or a government identifier.

## Who can reach it

| Principal | How | May |
| --- | --- | --- |
| The owner | Session cookie after the sign-in form; CSRF header on writes; lockout after repeated failures | Everything, including approve |
| A connected app (claude.ai, Claude Code, Open WebUI) | OAuth 2.1: dynamic registration, authorization code with PKCE, consent page with the owner's sign-in, access tokens for a day, refresh tokens for ninety, rotated | `read`, and `confirm` only if granted on the consent page; never `approve` or `admin` |
| A script | API key as a bearer token, scoped at creation | `read`, `confirm`; never `approve` |
| The reviewer model | Called by the app, never calling in | Sees the plan, rules, goals, alerts; never mail, attachments, account names or numbers |
| The advisor model | Called by the app in the chat | The read tools, redaction applied; cannot confirm, approve or send |
| Anyone else | The public surface: the sign-in page, `/mcp`, `/api`, `/oauth/*`, `/.well-known/*`, `/api/health` | Nothing without a credential |

No tool, job or button initiates a payment, a transfer or an email. Push notifications go to the owner's own ntfy or Gotify.

## What could go wrong, and the control

### A hostile email tries to steer a model

Mail is read by rules first; a model sees message text only boxed as data, with a frame that says where it came from and that it is not an instruction. Facts extracted from mail are pending until the owner confirms them; a `read` credential cannot confirm. The reviewer never sees mail at all. *Test suite: the malicious-mailbox cases in `backend/tests/test_mail.py`; grows under M8.4.*

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

CI builds the image on every push; a dependency audit step (pip-audit, npm audit) is M8.3's next piece.
