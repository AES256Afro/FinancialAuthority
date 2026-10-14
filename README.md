# FinancialAuthority

A self-hosted financial context engine. It reads a household's accounts, transactions, paychecks, bills, debts (including the ones in third-party apps such as Affirm, Klarna and Afterpay), subscriptions and bill-carrying emails, turns them into one model, and hands that model to any language model over MCP, a REST API and its own chat.

It answers three questions:

1. **What should this paycheck go on?** What must be paid before the next one, what to set aside, what is safe to spend.
2. **Should I buy this?** Yes, wait, or no, with the bill or buffer it would break.
3. **Which billing dates should move?** So bills land after paydays, with the request to each biller drafted.

It never moves money. It reads, plans and drafts.

**Status: planning.** Nothing runs yet.

- [docs/PLAN.md](docs/PLAN.md): what it is, where the data comes from, the engine, the access layer, security, decisions.
- [docs/ROADMAP.md](docs/ROADMAP.md): milestones M0 to M8 and what comes later.
- [AGENTS.md](AGENTS.md): rules for working in this repository, for people and coding agents.
- [deploy/boxpilot/financialauthority.yaml](deploy/boxpilot/financialauthority.yaml): the draft BoxPilot catalog manifest.

It is built to run on BigBox as a [BoxPilot](https://github.com/AES256Afro/BoxPilot) app, and anywhere else as one Docker image.

FinancialAuthority is not financial advice. It is a calculator that shows its work.
