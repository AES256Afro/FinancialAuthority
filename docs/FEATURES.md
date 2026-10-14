# Features: what the leading apps do, and what FinancialAuthority takes from them

*Written 2026-10-14 from public reviews and product pages of Monarch Money, Empower Personal Dashboard, Quicken Simplifi, YNAB, Copilot Money, Cleo, Origin, Rocket Money, and the open-source Actual Budget and Firefly III. Prices and details change; the point here is the feature shapes, mapped to our milestones.*

## 1. The table

| Feature | Who does it well | What FinancialAuthority does | Where |
| --- | --- | --- | --- |
| Safe-to-spend after bills, goals and set-asides | Simplifi's spending plan | The paycheck plan, by paycheck window rather than by calendar month, with advances taken first. | M5.2 |
| Flexible budgeting: fixed bills, flexible spending, non-monthly set-asides | Monarch | Bills are fixed, set-asides are the non-monthly bucket, the aqua band is the flexible one. No per-category caps unless the owner adds a watchlist. | M2, M5 |
| Watchlists: soft caps on a category or merchant with a month-end projection | Simplifi | Watchlists feed the don't-buy list and the brief; they never block, they project. | M5.4 |
| Rules that categorise, rename, tag, split and hide | Monarch, YNAB, Actual | A rules engine with the same verbs, seeded from the export's categories, learning from each correction (merchant memory, like Copilot). | M1.4 |
| Recurring and subscription detection, price-change alerts | Rocket Money, Monarch, Simplifi | Recurring series, subscriptions with price history, duplicate-service detection, cancel instructions per service. No negotiation service. | M1.5, M2.7 |
| Bill calendar and upcoming-bills view | Simplifi, Rocket Money | The month grid and the bill board, both as switchable home views. | M2.8, M7 |
| Cash-flow page: income, spending, net, savings rate, by category, merchant or tag; Sankey | Monarch | Where-it-goes is a home view; the reports page carries the period breakdowns and comparisons. | M7.8 |
| Save-up and pay-down goals with target, date, pace and on-track state | Monarch, Simplifi | Goals in the engine, funded from the aqua band at a pace the owner sets, with on-track, ahead and at-risk states; pay-down goals drive the debt mountain. | M5.6 |
| Debt payoff order and interest cost | Monarch (pay-down goals), YNAB | The debt mountain: order by rate, monthly interest, the effect of a bump. | M2.2, M7.2 |
| Net worth over time, every account in one place | Empower, Monarch | An accounts overview with balances by connector and net worth history; investments read-only when a connector offers them. | M2.9 |
| AI assistant that explains spending and answers in plain language | Monarch, Cleo, Origin, Copilot | Any model, through MCP, REST and the built-in chat, over the engine's numbers; a local advisor for free and a reviewer model for a second opinion. | M3, M3.7 |
| AI categorisation that learns from one correction | Copilot | Merchant memory in the rules engine; a correction becomes a rule. | M1.4 |
| Weekly recap and monthly report | Monarch, Simplifi | The daily brief, a Monday recap and a month-end report, delivered to the phone and readable by any model. | M6.2 |
| Household collaboration | Monarch | A second owner with shared and private accounts. | L4 |
| Retirement planner with scenarios | Empower, Origin | A long-range projection from the same engine, later. | L9 |
| Fee analyzer and investment checkup | Empower | Holdings and fee read-outs when an investment connector exists. | L8 |
| Receipt scanning | Monarch | Statement and paystub PDFs are read; receipts are not a v1 target. | L10 |
| CSV import with column mapping; OFX/QFX | Monarch, Actual, Firefly | The same, plus the Rocket Money export captured from mail. | M1.2 |
| Reconciliation against statements | Actual, YNAB, Firefly | Statement PDFs reconcile balances monthly and flag drift. | M1.3 |
| Professional or advisor access | Monarch | A read-only client with its own redaction level, if ever needed. | L11 |
| Chat-first interface for younger users | Cleo | The phone's chat tab and the ask box on every screen; same engine, plain words. | M7.4 |

## 2. What none of them do, which this app does

- **Plans by paycheck window, not by calendar month**, and takes paycheck-advance repayments first. Nobody models the advance apps.
- **Reads both mailboxes** for bills, statements, paystubs, installment schedules and past-due notices, and shows the message beside the fact it produced.
- **Parses paystubs** and reconciles net pay against the deposit, so a short paycheck is caught on payday.
- **Moves billing dates on purpose**, with the request drafted and sent on approval.
- **Lets any model in**: Claude on the phone through a custom connector, a local model on the home server, or any OpenAI-compatible model, all over the same engine, with every number traceable.
- **Shows its arithmetic**, every time, and lets a reviewer model push back.
- **Runs on the owner's own server**, at no monthly cost, with the data under their roof.
- **Eight home views and three looks**, so the owner picks the shape that reads best for them.

## 3. Deliberately skipped

- **Bill negotiation and subscription cancellation as a service** (Rocket Money). Paid, and the app's job is to show where the money goes; cancel instructions per service are enough.
- **Cash advances and credit-building products** (Cleo, Rocket Money). The app exists to make advances unnecessary.
- **Investment advice, crypto tracking, tax filing.** Out of scope; the app is not an adviser.
- **Ads, upsells, data sharing.** None. The data never leaves the server except to a model the owner chose, at the redaction level they set.
