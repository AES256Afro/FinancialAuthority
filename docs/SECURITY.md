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

The sign-in form locks out after repeated failures per address; the second step, passkeys, join links and the OAuth consent page share the lockout, and a forwarding header moves the count only when the owner named the proxy in `FA_TRUSTED_PROXIES` (otherwise every forwarded request counts as one address, so neither a spoofed header nor uvicorn's own proxy-header rewriting opens a fresh count). The MCP transport's DNS-rebinding check is off, since the app is reached at whatever name the box, the tailnet or the tunnel gives it; a foreign Host header reaches only the public surface, because `/mcp` takes a bearer token and never a cookie. Rate limits at the edge come from Cloudflare when published through the tunnel. *Left: verification from outside once published (M3.3), rate limits inside the app (M8.4).*

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
| Security headers | `app/security.py`, plain ASGI: nosniff, no referrer, `X-Frame-Options: DENY`, a permissions policy, HSTS over https; the pages get a content-security policy (scripts from the app, Plaid Link and Teller Connect only, no plugins, no outside forms, no framing); the OAuth consent page gets its own, whose form rule allows the client's https or local callback | test, and `curl -I` against BigBox |
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


## The fifth sweep (rounds one to three), 2026-10-29

Run overnight by coding agents in parallel areas, each finding written as a failing test first (`backend/tests/test_sweep5_*.py`, `test_sweep6_*.py`, `test_sweep7_*.py`, `frontend/src/session.test.ts`). Rounds continue until a round finds nothing.

| Found | Severity | Control now |
|---|---|---|
| A mailbox's folder names went into the IMAP command line as typed, so a folder of `INBOX"\r\nA1 UID STORE ...` could flag and expunge the owner's mail through a read-only client (an Everything person can set folders) | high | folder names are letters, digits, spaces and punctuation without quotes, backslashes or control characters, refused at the API, by the connector and by the IMAP client itself |
| A second person's API key or OAuth token queued sends as the owner, so the owner's standing allowances sent them untouched | medium | key and token principals carry the person; each person's allowances cover only their own sends |
| A standing allowance covered a draft a model or script queued, so a hostile email read by a confirm-scoped client could send anything to an allowed address in the owner's name | medium | an allowance covers what the owner or a second person queues themselves; a draft from a key or a connected app always waits for a tap, and says why |
| Two approve requests at once sent twice; a fact confirmed twice at once made two bills; a join link redeemed twice at once set two passwords; an authorization code exchanged twice at once minted two token pairs | medium | each is claimed in one write under a lock before anything else happens |
| `FA_CORS_ORIGINS=*` reflected any asking origin with the owner's cookie | low | a wildcard is ignored with a warning; origins are named |
| Dynamic registration accepted `javascript:`, `data:` and `file:` callbacks | low | refused; https, local http and an app's own scheme stay |
| The passkey options route grew its challenge table without bound and had no lockout | low | capped at a thousand, oldest out; the sign-in lockout applies |
| The citation check missed `$1.2k`, `1200USD` and amounts in words, so a model could state an invented figure unmarked | medium | scale suffixes and number words are parsed and checked |
| At the aggregates and merchants levels, the advisor's prompt carried the brief unredacted, and `data_check`, `jobs_status` and `facts_pending` named senders, subjects, accounts and hosts | medium | every line is redacted the same way as the tools; sender findings reduce to counts for non-full callers |
| Refused and failed tool calls wrote no audit row | low | `<tool>_refused` and `<tool>_failed` rows, arguments hashed |
| The audit log's argument hash was unkeyed, so a low-entropy argument could be read back by trying values | low | keyed with the session secret |
| A changed push address kept the old ntfy or Gotify token, so an Everything person could point pushes at their own server and read the owner's token | medium | a new address drops the token unless one is typed with it |
| Erase ran beside running jobs and left the Teller certificate in the runtime folder | low | erase takes the writer lock and forgets the certificate |
| Provider streams and error bodies were read without bound; reviewer actions accepted NaN buffers and book-length rules; a push title with accents raised out of the job | low | 2 MB stream cap, 600-byte error bodies, finite bounds, RFC 2047 headers |
| Chat history and the Plaid link token stayed in the browser after sign-out; amounts in the SVG pictures ignored Hide amounts; a push notification could open any origin | medium | sign-out and a 401 clear device state; every picture label is a `.num`; links off-origin fall back to Today |
| Teller Connect was blocked by the content-security policy | functional | `cdn.teller.io` and `*.teller.io` allowed for scripts, frames and connections |
| A CSV with one field over 128 KB answered 500; the Plaid webhook's rate table grew per address without end; an email with a hundred PDFs held the mail job for ten minutes | low | 400 with a sentence; the table forgets quiet addresses and caps at five thousand; five PDFs per message |
| Round four: the facts route claimed a confirmation under the writer lock, but the `facts_confirm` tool and Needs you's bulk answer did not, so two confirms in the same instant through either still made two bills | medium | every path that confirms a fact reads it again under the writer lock (`backend/tests/test_sweep8_security.py`) |
| A sender's display name in look-alike letters ("Capitаl One" with a Cyrillic а, a zero-width space inside the word, full-width letters) read as no brand at all, so the "calls itself X but comes from Y" check never ran | low | names are folded first: NFKC, invisible characters out, look-alike letters to the Latin ones they draw like |
| An installed copy with `FA_PASSWORD` empty started with sign-in off and a log line (the compose `.env.example` ships it empty), answering anyone who reached its port as the owner | medium | a copy that says how it was installed (`FA_INSTALL`: boxpilot, compose, and now docker from the image itself) refuses to start without a password unless it is the demo |
| The image gave the runtime user the code tree; the workflow left the token at the repository's default permissions | low | the runtime user owns `/data` and `/imports` only; the workflow token reads the repository and the publish job alone writes packages |
| Round five (`backend/tests/test_sweep9_security.py`): a refresh token presented twice in the same instant minted two token pairs (the authorization code was claimed in one statement in round three; the refresh was not) | medium | the exchange spends the refresh row in one statement; the second gets `invalid_grant` |
| A new invitation (Settings › People › new link, which also clears the password) ran beside a join on the old link, so a join landing in the same instant could keep a password after the reset | low | the reset and the join take the same invitation lock and read the person again under it |
| Editing a pending draft read it without the decision lock, so an edit beside an approve could change the recipient after the send and leave a record of a send the owner never saw | low | the edit reads the row again under the decision lock; one decided meanwhile answers 409 |
| At the aggregates level, names rode out under keys the redactor did not list: the advance apps since payday, the debts in payoff order, a plan's lender, the loans a tax check saw, a business, the glance's one thing, the cover sheet's "Pay X from Y" | low | those keys are held back; the sheet keeps its verb (pay, keep, move) and loses the names |
| A key refused on a REST route (no scope, approve, a key that hides details) left no audit row, unlike a refused tool call | low | every REST call a key makes writes a row, `_refused` when it was |
| A bill, an income, a plan, a goal, a home or a rule typed while a detection or a sync ran could come out twice: the job read the registers before the route wrote | low | every register write route takes the writer lock, waits three seconds, then answers 409 "try again in a moment" rather than holding the request |

Round four also read the Apple app (`apple/`, not yet compiled): PKCE with S256 over 48 random bytes, the state checked, an ephemeral browser session, the token in the Keychain and never on disk elsewhere, the widget holding only the glance. Two things to change when it is built: the phone and the watch share one refresh token, so after the first rotation the second device's refresh trips the reuse rule and signs both out (the watch should hold its own client registration); and the Keychain item is `AfterFirstUnlock`, not `ThisDeviceOnly`, so the token rides along in an encrypted backup restored to another device.

Verified sound in the same sweep, beyond the hardening table above: the route list against PLAN section 12 (308 routes walked), scope intersections at every entry, the Shared role's path lock, CSRF on every cookie write, lockouts on every sign-in path, timing-safe compares, OAuth PKCE and rotation, passkeys, join links, erase and restore ownership, demo mode, Home Assistant and watch routes, path traversal in restore points, documents, the off-box copy and uploads, PDF, XLSX and OCR bombs, SSRF through `netguard`, the Plaid webhook's signature, AES-GCM use, export cell escaping, mail boxing, send header injection, allowance address matching, secrets in logs and exports, enumeration messages, the frontend's absence of HTML sinks, the service worker, the dependency audits (0 findings).

## The sixth sweep (black box), 2026-10-29

Run from outside the process against the real server (sign-in on, `FA_INSTALL=docker`, the synthetic household), as a stranger, a read key at the aggregates level, a confirm-scoped OAuth client, an Everything person and a Shared person. Each finding is a test in `backend/tests/test_sweep10_security.py`.

| Found | Severity | Control now |
|---|---|---|
| uvicorn believes `X-Forwarded-For` from a loopback peer by default and rewrites the client address before the app sees it, so wherever the app is reached through something on its own host (the dev launcher, a reverse proxy on localhost, host networking) each wrong sign-in could carry a fresh address and the per-address lockout never closed (the published image is reached through the Docker bridge, where the peer is not loopback, so the tunnel was not open to this) | medium | with no proxy named, every request carrying a forwarding header counts as one address, whatever the peer |
| The passkey routes kept their own failure count, so an address locked out of the password was still handed passkey challenges (harmless to guess, but not the shared lockout the plan states) | low | the passkey routes refuse under the sign-in lockout as well as their own |
| Every response carried `Server: uvicorn` | low | the image's command and the dev launcher start uvicorn without its banner (verify with `curl -I` against the published address once the image is rebuilt) |

Held, from outside: every one of the 306 routes refused a stranger except the public ones in PLAN section 12 (the sign-in, join, second-step and passkey routes, the Plaid webhook, the calendar feed, the OAuth endpoints, the metadata documents, `/mcp` with its `WWW-Authenticate` challenge and no data, `/api/health`); malformed, huge, nested, form-encoded and binary bodies to each public route answered under 500; the 1 MB JSON and 60 MB upload ceilings, stated or chunked, answered 413; every response carried the security headers and no HSTS over plain http; a foreign Host header reached nothing more; PKCE S256 required, the registered redirect enforced at authorize and at token, a wrong verifier and a reused code refused, the consent page under its own policy with no script and refusing a cross-site form, a cookie set only after the owner's password, `approve` and `admin` dropped from a consent that asked for them, refresh rotation with the reuse rule ending the client's tokens after the minute's grace, another client refused the first's refresh token, the twenty-first registration in a day refused; a read key at aggregates refused on all 296 REST routes with a `_refused` audit row each, answered by all 44 tools with no account name, institution, last four, sender, subject, excerpt or merchant in any result, and refused `facts_confirm` and `mail_send_request`; a confirm-scoped client's `mail_send_request` to an address on the owner's allowance waited for a tap and nothing was sent, and its `approve` by REST was refused; the Everything person was refused people, erase (with the owner's password too), restore, the off-box target and the owner's drafts and allowances, and could not consent an app or sign in to the consent page with their own password; the Shared person reached only `/api/v1/shared`, `/api/v1/about`, `/api/v1/appearance` and their own sign-in settings; every export format was the owner's only with `Cache-Control: no-store`; the calendar feed carried no amounts until asked, a rotated token answered 404, and the sixty-first request in an hour answered 429.

## The seventh sweep, at the wire and on the published path, 2026-10-29

Scripted IMAP and SMTP servers logged every command the app sent; a simulated tunnel (forwarded headers from a named proxy, a public address) checked the published path from outside. Tests: `backend/tests/test_sweep11_security_wire.py`, `test_sweep11_security_published.py`, `test_sweep11_security_exports.py`.

| Found | Severity | Control now |
|---|---|---|
| The IMAP sign-in sent the mailbox username unquoted, so a username holding a line break (settable by an Everything person, or typed into the mailbox test) ended the LOGIN and ran its own STORE and EXPUNGE on the "read-only" connection | high | usernames and passwords with control characters are refused at the API and in the client; the username goes as an IMAP quoted string |
| A server dripping a literal a byte at a time held the mail sync, and the writer lock with it, without end | medium | a 120-second budget per command and a 64 MB ceiling per literal, refused before a byte is read |
| The calendar feed's limit was keyed by the token, so a guesser got a fresh count per guess | low | keyed by the caller's address, forwarded address behind a named proxy |
| A correctly signed Plaid notice was accepted again within its five minutes | low | signatures seen are kept for twice the window and refused on repeat |
| A subject with a line break was queued and failed only at send time | low | refused when the draft is made |
| Queuing, approving and denying a send wrote no activity entry | low | `approval_queued`, `approval_approved`, `approval_denied` rows, the id hashed, no text |

Held at the wire: every folder open is EXAMINE and every fetch is BODY.PEEK, never a STORE, COPY, MOVE, EXPUNGE, APPEND, DELETE, CREATE, RENAME, SUBSCRIBE or SETACL, across sync, search, read, attachment, the data check's header fetch, the export-link follow and a receipt confirmed on Needs you; hostile answers (a 50 MB literal, a fetch naming another UID, a thousand folders, BAD on everything) end in under a minute with no crash; one recipient, no injected header, the 1 MB draft ceiling, STARTTLS where configured. Held on the published path: HSTS and the Secure cookie only on forwarded https, the OAuth issuer and every metadata document at the public address, the consent redirect to a claude.ai-shaped callback and the warning for any other, the passkey relying party is the public host, lockouts and limits count the forwarded address and a spoofed `CF-Connecting-IP` beside the proxy's chain loses, the Plaid webhook accepts a signed notice once and refuses an old, a wrong-key, a wrong-body and a replayed one.

## The eighth sweep, at the other wires, 2026-10-29

Scripted Plaid, Teller, OpenAI-shaped, Anthropic-shaped, Ollama-shaped, ntfy and Gotify servers answered the app with hostile replies; the OCR reader, the document store, Home Assistant and the import folder were tried with crafted files. Tests: `backend/tests/test_sweep12_security_banks.py`, `_llm.py`, `_edges.py`.

| Found | Severity | Control now |
|---|---|---|
| A bank vendor's answer was read whole with a per-read timeout, so a 100 MB answer sat in memory and a dripping one held the sync, and the writer lock, without end | medium | 16 MB and 120 seconds per call, then the link says the vendor could not be read |
| A model provider's stream kept open with keep-alive comments reset the read timeout forever, holding the chat request or the hourly reviewer job | medium | a ten-minute wall clock per stream beside the byte cap |
| A vendor error echoing the access token or the Plaid secret landed in the link's error, the job summary and a log line; a provider echoing the key showed it in the chat to whoever was there; an HTML error page was shown raw | low | secrets scrubbed and tags stripped on every error path |
| A null, NaN or 1e308 amount, a 10,000-character merchant, a 1 MB cursor or an account id with a line break were written as they came, and a newline in an id broke the link's comma-joined account list | low | amounts must be finite, ids and names are cleaned and capped, the cursor capped, rows left out and said so |
| A 200 answer that was not an object raised out of the exchange and the settings check (a 500 to the owner); a non-JSON error body went raw into the link's error | low | plain sentences |
| A reviewer's proposed rule kept line breaks, so once applied it could draw a second paragraph under the owner's rules | low | one line |
| A push server's error body was logged with its line breaks and escape codes | low | one bounded line |
| The import folder followed symlinks, blocked for good on a FIFO, failed every run after one dangling entry, read files of any size whole, and rewrote its record per file | low to medium | symlinks and non-files skipped, over 50 MB refused unread, a cap per run, one record per run |

Held: the token only ever in its header for every vendor; a provider reply holding a fake tool call, a tool-result block, a system-prompt line or a JSON "approve", "send", "confirm" or "fetch" is shown as text and acts on nothing; the reviewer's schema refuses extra keys and unknown kinds; the aggregates-level request body carries no account, bank, employer, bill, merchant, last-four, sender or subject; a push redirect to another host is not followed; the VAPID private key reaches no response; Home Assistant's aggregates answer is figures only; a 20,000 by 20,000 picture, a zip named as a PDF, a 10,000-page TIFF, a path with `..` and a file over the limit are refused or bounded, the reader runs with no shell under a resource limit and a timeout, its temp folder is gone even on a crash, and the stored blob and pages are sealed on disk.

## The ninth sweep, the data at rest and the operator, 2026-10-29

Every secret the app can hold was seeded into a household, then looked for in the database file, its journal, every file under the data folder, the restore points, the off-box copy, the twelve exports, the restore command's output, every page and error page, sixteen days of logs at DEBUG, and the data folder after "delete everything". Tests: `backend/tests/test_sweep13_security_rest.py`, `_operator.py`, `_logs.py`, `_keys.py`.

| Found | Severity | Control now |
|---|---|---|
| After "delete everything" the database file still held every account name, merchant, subject, sender and amount: VACUUM under write-ahead logging rebuilds into the journal, which was never truncated, so a copy of the data folder taken afterwards (the nightly archive) carried the household | medium | secure delete on before the wipe, the journal truncated after the rebuild, and the test reads the file bytes |
| A changed `FA_DATA_KEY` was silent: the app started and each feature failed on its own, and anything saved was sealed under the new key | low | a sealed canary is checked at startup; a key that does not open it stops the app with a sentence naming the variable |
| Rotating `FA_SESSION_SECRET` silently killed the ten recovery codes, which were hashed with it | low | each code is its own salted scrypt hash; codes kept the old way still sign in |
| The MCP transport and the SSE library logged each session id and each tool result verbatim at DEBUG | low | both loggers pinned above DEBUG |
| A Shared person was told the whole household's transaction count | low | counts only the accounts they may see |
| The database, its journal, the off-box copy, the restore points and the stored documents were world-readable on disk | low | 0600 for the database, the journal and the off-box copy; restore points and document blobs follow |

Held: no seeded secret in the clear anywhere; password hashes are scrypt; invites, keys and tokens hashed; the TOTP secret sealed; the manual account keeps four digits; document pages sealed; the off-box copy sealed with no plaintext scratch left; exports carry no sealed blob; the restore command prints names, sizes and verdicts only; the health route says up and demo only; no environment value reaches a page; the audit rows hold who, which tool, how many rows and a keyed hash. There is still no job to rotate `FA_DATA_KEY` (M8.5): a rotation would re-seal every `*_enc` column, the sealed settings and every document blob.

## The tenth sweep, every input fuzzed, crashes injected, a second person's week, 2026-10-29

Every route with a body, query or path input (200 of 296) and every MCP tool was sent missing, null, wrong-typed, enormous, control-character, bidi, NaN, Infinity, past-2^63, impossible-date and 1,000-deep inputs, as the owner and as an Everything person (about 16,000 requests in a full run; a sampled 1,300 in the suite). Every job and multi-step write was crashed at each commit boundary and killed mid-run thirty times, then run again. A second person's week, two tabs and a failing network were driven in the browser. Tests: `backend/tests/test_sweep14_security_inputs.py`, `test_sweep14_security_fuzz.py`, `test_sweep14_bugs*.py`.

| Found | Severity | Control now |
|---|---|---|
| A restore copied the point over the live database first and carried today's keys and sessions in afterwards, so a failure in between left a revoked key working again | high | the point is assembled in a staged copy and put in place in one step |
| An income dated 9999-12-31, a goal with that target, or a balance, limit or APR of 1e308 was stored and broke the snapshot, Today or every later write for good | medium | every API input and every tool argument passes one guard: finite numbers within 1e12, whole numbers within 2^31, dates between 1900 and 2199, one-line strings of at most 1,000 characters without control or bidi characters |
| A NaN, Infinity or deeply nested body, a number past 2^63 in a path, or an impossible year answered 500 on most routes | low | 400 or 422 with a sentence; the 422 no longer echoes the input |
| Names were stored with line breaks, NUL, bidi overrides or 100,000 characters in about thirty tables | low | the same guard |
| A mail fetch that timed out or a dropped connection was treated as an unreadable message, so the mailbox's mark moved past the rest of the batch for good while the run said ok | high | the folder stops at the last message read and the run fails, so the next one reads on from there |
| Several writes committed half: the interest mark before the interest, an "I paid it" claim before its tick, a plan before its fact, a paystub before its paycheck figure; a paystub file read again after a crash made a second fact | medium | each lands in one commit, and a file read again is recognised |
| A run that stored rows and then failed its follow-up left bills counted as owed for hours, because the retry stored nothing new and skipped the follow-up | medium | each job keeps how far detection and matching have run, and catches up |
| Naming a charge or answering "is it paid?" from a stale tab made a second bill or overwrote the answer; erase left the import folder to be read in again | low | 409 with a sentence; the import record survives an erase |
| A second person's strip counted only the owner's waiting sends; a Shared person was blurred by someone else's Hide amounts with no way out; a session ended mid-use landed on sign-in with no sentence | low | counts follow whoever reads them; Shared is never blurred by others; the sign-in says why |

Held: integrity checks after every kill, no lock left on disk, every listed restore point whole, the Plaid cursor stored with its rows, two job threads beside owner taps for a week with every answer a success or a 4xx sentence; the network failures in the browser (timeouts, a proxy's 502, the writer lock's 409, offline) each end in a sentence and a retry.

## The eleventh sweep, the MCP protocol, a model that obeys the mail, and round trips, 2026-10-29

The MCP endpoint was driven with raw JSON-RPC and the SDK client: sessions crossed between credentials, resumed from another stream, cancelled mid-write, flooded, batched and sent methods outside the tools. A scripted model that does whatever the latest untrusted text says drove the chat, the reviewer and a confirm-scoped connected app against hostile mail, a lease and a merchant name. Every export format was taken out and brought back into an empty install. Tests: `backend/tests/test_sweep15_*.py`.

| Found | Severity | Control now |
|---|---|---|
| One leaked read key could open ten thousand MCP sessions and lock every other client, the owner's included, out for half an hour | medium | sixteen sessions per credential; the seventeenth ends the oldest |
| A figure inside a merchant name, a sender name or a subject counted as an engine number, so a model repeating it passed the citation check | medium | text from third parties is never an engine number, in the chat and in the reviewer |
| A confirm-scoped app steered by a forged email could confirm the facts that very email made | medium | facts from mail judged forged are the owner's to confirm |
| Two keys with the same name shared MCP sessions | low | a session is tied to the credential itself |
| A cancelled `facts_confirm` or `mail_send_request` answered "cancelled" and committed anyway | low | the tool checks for cancellation before it writes, and the audit log says so |
| Refused chat tools and unknown MCP tools wrote no audit row; `prompts/get` answered error code 0 with a traceback | low | `<tool>_refused` rows; clean JSON-RPC errors |
| The push for a model-queued send did not say a model queued it; a connected app's confirmation was never pushed | low | both are pushed and named |
| A confirmed fact kept no record of who confirmed it, and nothing could take a confirmation back | low | each answer records you, a person, a key, a connected app, a rule or the data check, and Needs you offers Undo where nothing has been built on it since |
| A past-due notice from a forged sender still reached the brief and the reviewer by name | low | left to the data check |

What a confirm-scoped connected app can be made to do by mail alone, after this round: confirm a pending fact from mail that is not judged forged, exactly as the message stated it, which the owner sees pushed and can undo; and queue sends, which always wait for a tap, ignore standing allowances and say which app asked. Nothing else: not the buffer, the rules, the reviewer's notes or an approval.

Round trips (not security, recorded here because they decide what "take your data back" means): the off-box copy and a restore point come back whole; the app's own transactions CSV, OFX and Rocket Money exports read back exactly and add nothing when read twice or beside the bank link (before this round the CSV flipped every sign and the Rocket Money file doubled the ledger); splits and business marks do not travel in CSV; JSON, XLSX, QIF, Beancount and Ledger are ways out, not ways back, and say so.

## The twelfth sweep, cross-origin attacks from a real browser, 2026-10-29

Chrome, resolving every name to a local TLS proxy that forwards to the app the way the tunnel does, signed in as the owner on the app's host, then opened attacker pages on another site and on a sibling subdomain of the same domain: form posts, credentialed fetches, script and image tags on JSON routes, framing, popups, cookie tossing, login and logout CSRF, an OAuth flow started by the attacker, cross-origin exports and streams, a service worker over the app's paths, and timing and frame-count leaks. Seventy-two probes; the harness is a script, the controls are tests in `backend/tests/test_sweep16_security_browser.py`.

| Found | Severity | Control now |
|---|---|---|
| A form posted from any page signed the owner out: the logout route asked for no header, and the browser applied the cookie deletion on a top-level answer | low | logout needs the app's own header |
| A sibling subdomain could set a cookie with the session's name for the parent domain; the app read the planted one, which signed the owner out, and a real session planted the same way would have signed the owner into someone else's | low | over https the session cookie is `__Host-fa_session`, which no sibling can set, and the plain name is not read there (signing in once more after the upgrade) |
| Script and image tags on a sibling's page reached the API with the owner's cookie and were answered in full (the browser kept them from running, but the answer was work done and a timing signal) | low | an `/api` request from another site that is neither CORS nor a page load is refused before any route runs; every `/api` answer says `Cross-Origin-Resource-Policy: same-origin` |

Held: no attacker page could approve, confirm, erase, add a person or a key, read a byte of data, or obtain an OAuth code; no GET route changes state; the app and the consent page refuse framing; popups are severed from their opener; MCP takes bearer tokens only; a sibling cannot register a service worker over the app; nothing told a signed-in owner from a signed-out one after the fixes.

## The thirteenth sweep, the box around the app, 2026-10-29

The image (0.10.4 and its Bridge sidecar, run as their users and as root, with no network), the containers as BoxPilot runs them, the server's users and groups, BoxPilot's stored values, its checkpoints and its cloud mirror, the image workflow and the lockfiles, and BoxPilot's two FinancialAuthority operations. Read-only on the server. Tests: `backend/tests/test_sweep17_security_box.py`.

| Found | Severity | Control now |
|---|---|---|
| BoxPilot's nightly cloud mirror copied every app archive to the bucket unencrypted, and never deletes there: ten FinancialAuthority archives were in it, each holding the app's `.env` (`FA_DATA_KEY`, `FA_PASSWORD`, `FA_SESSION_SECRET`) beside the database those keys seal, so the sealing meant nothing to whoever reads the bucket. Since the Bridge state moved under BoxPilot each archive also carries the Bridge's vault, the key that opens it (a GPG key without a passphrase) and its mail cache, about 3 GB, due to go up at the next mirror | high | BoxPilot (branch `security/financialauthority-box-cloud-mirror`): the mirror goes through an rclone crypt remote under the owner's passphrase and refuses a destination saved without one. The owner's steps (stop the schedule, set the passphrase, delete the plain copies, rotate) are in `docs/ACCESS.md` section 15 |
| The image installed uv with `pip install uv` (the newest release, no version or hash) and shipped it with pip; installing the app as a package fetched hatchling unlocked | low | uv comes from a digest-pinned image, mounted for the install only; only the locked, hash-checked dependencies are installed; pip is removed |
| The image's Debian packages were as old as the pinned base: two security updates (liblzma5 among them) were missing in 0.10.4 | low | the build takes Debian's updates; the weekly rebuild keeps taking them |
| The test suite (with its password) and the developer scripts shipped in the image; a local build would also ship a dev machine's `backend/imports` | info | kept out of the build context |
| Eleven setuid or setgid binaries (su, mount, passwd and the like) shipped; inert under `no-new-privileges`, but nothing needs them | info | no file in the image is setuid or setgid |
| Every action in the workflow was pinned by a moving tag, the third-party ones in the job that holds the package write; no image carried provenance | low | each action pinned to a commit (Dependabot moves the pin); each pushed image carries a signed build provenance attestation (`gh attestation verify oci://ghcr.io/aes256afro/financialauthority@<digest> --owner AES256Afro`) |
| Restore points, their `-shm` files and a kept document made before 0.10.2 were still 0644 on the server; a restore point's description was always 0644 | low | the start sets every file in the restore and documents folders to 0600; a new description is made 0600 |
| BoxPilot put the app's printed text (a restore point's name, an error) into its job log as it came: no HTML sink, but a newline or bidi control could forge a line | info | BoxPilot (branch `security/financialauthority-box-ops-text`): one plain line, bounded, counts must be numbers |
| The standalone compose file kept every default capability and a writable root | info | `cap_drop: [ALL]`, `read_only: true` and a `/tmp` tmpfs; the image was run this way (health, a restore point, OCR of a photo and of a scanned PDF) |

Held: the app runs as uid 1000 with `no-new-privileges`, writing only `/data`, `/imports`, `/restore-points`, `/offbox` and `/tmp`; its port is published on loopback and reached through Tailscale Serve, so tailnet-only as the catalog state says; the Bridge publishes nothing and shares its network with the app alone, its `/root` is not mounted in the app and `/data` is not mounted in the Bridge; the Bridge's state files are root 0600; BoxPilot keeps the three secrets in a 0600 root `.env` and writes `${NAME}` references into the compose file; the project folders and the checkpoints are root-only (0700 folders, 0600 archives); checkpoint retention is five plus five nightly, so the disk (300 GB free) does not fill; no source map, `.env`, `.git` or key ships in the image; both lockfiles carry integrity hashes; no workflow uses `pull_request_target`, interpolates event text into a shell or gives a pull request a secret or a write token; BoxPilot's operations run a fixed argv as the `app` user with no parameter but the app id, through `docker compose exec` with no shell, and its UI renders the app's output as text (no HTML sink anywhere in BoxPilot's UI).

Left, on the server and in BoxPilot (not changed, read-only round): reading the app's environment needs root or the docker group, and the docker group is root on this server: two users are in it, and three containers hold the Docker socket (a dashboard read-only, two container managers read-write), so each of them can read `FA_DATA_KEY`; the app's uid 1000 is also the server's first user, who can read the app's environment and data through `/proc`. Four containers use the host's network and can reach the Bridge's IMAP and SMTP, which still need the vault password. BoxPilot's manifest cannot yet ask for `read_only` or `cap_drop: [ALL]` without adding a capability; the Bridge image is a Debian testing snapshot running as root. `FA_OLLAMA_URL` on the server points at port 8430 (the app itself), so the built-in chat cannot reach Ollama until it is set back to 11434.
