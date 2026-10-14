# FinancialAuthority

A self-hosted financial context engine that runs itself. It reads live bank and card transactions, paystubs, monthly bills, payments, debts (including the ones in third-party apps such as Affirm, Klarna, Afterpay and paycheck-advance apps), subscriptions and both of the owner's mailboxes, keeps all of it current without being asked, and hands one model of it to any language model, from anywhere, at any time, over MCP, a REST API and its own chat.

It answers three questions:

1. **What should this paycheck go on?** What the advance apps take first, what must be paid before the next paycheck, what to set aside, what is safe to spend.
2. **Should I buy this?** Yes, wait, or no, with the bill or buffer it would break.
3. **Which billing dates should move?** So bills land after paydays, with the request to each biller drafted and sent once approved.

And every morning it says what needs attention: what is due and not yet funded, what is past due, a paystub that does not add up, a new installment plan or advance, a connector that stopped.

It never moves money. It reads, plans, drafts, and sends only with an approval.

**Status: planning.** Nothing runs yet.

- [docs/PLAN.md](docs/PLAN.md): what it is, how it operates, where the data comes from, the engine, reach from anywhere, security, decisions.
- [docs/ROADMAP.md](docs/ROADMAP.md): milestones M0 to M8 and what comes later.
- [docs/ACCESS.md](docs/ACCESS.md): what the coding agent already reaches and the few things only the owner can grant.
- [AGENTS.md](AGENTS.md): rules for working in this repository, for people and coding agents.
- [deploy/boxpilot/financialauthority.yaml](deploy/boxpilot/financialauthority.yaml): the draft BoxPilot catalog manifest, with the Proton Mail Bridge as a sidecar.

It is built to run on BigBox as a [BoxPilot](https://github.com/AES256Afro/BoxPilot) app, published to the internet through BoxPilot's Cloudflare Tunnel so Claude can reach it from a phone, and anywhere else as one Docker image.

FinancialAuthority is not financial advice. It is a calculator that shows its work.
