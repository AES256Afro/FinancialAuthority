"""Who answered a fact, and taking a confirmation back.

A fact read from mail or a document waits until someone confirms or rejects it. `resolve_fact` records the answer
with who gave it (the owner, a second person, a key or a connected app), and, given the high-water marks taken
just before the fact was applied, what confirming it created as against what it changed. `unconfirm_fact` takes
back what a confirmation created, where nothing has happened to it since, and puts the fact back to waiting; it
refuses, with a sentence saying why and what to do, where taking it back would lose something real.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func
from sqlmodel import Session, select

from ..models import Bill, Document, ExtractedFact, Home, HomeCharge, HomeCost, InstallmentPlan, MoveItem, Payment, Paystub, Utility, utcnow

TABLES = {"bill": Bill, "plan": InstallmentPlan, "paystub": Paystub}


def who(principal: Any, app: bool | None = None) -> str:
    """The answerer as a fact keeps it: owner, person:<id>, key:<name>, or app:<client> for a connected app."""
    kind = getattr(principal, "kind", "")
    if kind == "owner":
        return "owner"
    if kind == "person":
        return f"person:{getattr(principal, 'person_id', '')}"
    via_app = getattr(principal, "via_oauth", False) if app is None else app
    return f"{'app' if via_app else 'key'}:{str(getattr(principal, 'name', '') or '')[:60]}"


def worded(session: Session, by: str, viewer: Any = None) -> str:
    """Who answered, as a screen says it: you, the second person's name, the app or key's name, a rule, the data check."""
    kind, _, rest = (by or "").partition(":")
    if kind == "owner":
        return "you" if getattr(viewer, "kind", "owner") == "owner" else "the owner"
    if kind == "person":
        if rest.isdigit() and getattr(viewer, "kind", "") == "person" and getattr(viewer, "person_id", None) == int(rest):
            return "you"
        from ..models import Person

        person = session.get(Person, int(rest)) if rest.isdigit() else None
        return (person.display_name or person.username) if person is not None else "a second person"
    if kind in ("app", "key"):
        return rest or ("a connected app" if kind == "app" else "a key")
    return {"rule": "a rule", "datacheck": "the data check"}.get(kind, "")


def undoable(fact: ExtractedFact) -> bool:
    """Whether the Undo button is worth showing: unconfirm_fact may still refuse, with its reason, where something
    happened to what the confirmation made since."""
    if fact.status == "confirmed":
        return True
    if fact.status != "applied":
        return False
    kind, _, rest = (fact.applied_to or "").partition(":")
    return (kind == "paystub" and rest.isdigit()) or (kind in ("bill", "plan") and fact.made.split(":")[:2] == [kind, rest])


def watermark(session: Session) -> dict[str, int]:
    """The highest id of each register a confirmation may add to, taken just before it is applied."""
    return {kind: int(session.exec(select(func.max(model.id))).one() or 0) for kind, model in TABLES.items()}


def resolve_fact(session: Session, fact: ExtractedFact, status: str, *, applied_to: str = "", by: str = "", before: dict[str, int] | None = None) -> None:
    """Record an answer: the status, what it applied to, when and by whom; the caller commits."""
    fact.status, fact.applied_to, fact.resolved_at, fact.resolved_by = status, applied_to, utcnow(), (by or "")[:80]
    fact.made = ""
    kind, _, ident = applied_to.partition(":")
    if kind == "paystub" and ident.isdigit():
        # A paystub names the fact that kept it, which holds when a tap is tried again after a failed write:
        # record_paystub commits on its own, so the retry's high-water mark already counts it.
        stub = session.get(Paystub, int(ident))
        if stub is not None and stub.fact_id == fact.id:
            fact.made = applied_to
    elif before is not None and kind in TABLES and ident.isdigit() and int(ident) > before.get(kind, 0):
        fact.made = applied_to
        if kind == "plan":
            plan = session.get(InstallmentPlan, int(ident))
            fact.made = f"plan:{ident}:{plan.remaining if plan else 0}"  # a payment ticked since is a reason not to take it back
    session.add(fact)


def unconfirm_fact(session: Session, fact_id: int, by: str = "") -> dict[str, Any]:
    """Take back what confirming a fact created, and put the fact back to waiting. Raises ValueError with the
    sentence to show when it cannot be taken back safely. Commits."""
    fact = session.get(ExtractedFact, fact_id)
    if fact is None:
        raise ValueError("No such fact")
    if fact.status not in ("applied", "confirmed"):
        raise ValueError(f"This one is {fact.status}; there is no confirmation to take back.")
    tag = fact.applied_to or ""
    kind, _, rest = tag.partition(":")
    said = "Nothing in the plan had changed; it waits for an answer again."
    if fact.status == "applied":
        if kind == "paystub" and rest.isdigit():
            said = _paystub(session, fact, int(rest))
        elif kind in ("bill", "plan") and fact.made.split(":")[:2] == [kind, rest]:
            said = _bill(session, int(rest), fact) if kind == "bill" else _plan(session, fact, int(rest))
        elif kind in ("bill", "plan"):
            raise ValueError(f"Confirming it changed a {kind} you already had; change that {kind} back where it is kept, then reject this one if you like.")
        else:
            raise ValueError("What confirming it did cannot be taken back here; change it where it is kept.")
    fact.status, fact.applied_to, fact.resolved_at, fact.resolved_by, fact.made = "pending", "", None, "", ""
    session.add(fact)
    session.commit()
    return {"ok": True, "undone": tag, "by": by, "said": said}


def _changed_since(session: Session, fact: ExtractedFact, tag: str) -> bool:
    """Another confirmation applied to what this one made (a later notice for the same biller, the same loan): taking
    this one back would delete what that one still names, and SQLite gives the id to the next row made (sweep 19)."""
    return session.exec(select(ExtractedFact.id).where(ExtractedFact.applied_to == tag, ExtractedFact.status == "applied", ExtractedFact.id != fact.id)).first() is not None


def _bill(session: Session, bill_id: int, fact: ExtractedFact | None = None) -> str:
    bill = session.get(Bill, bill_id)
    if bill is None:
        return "The bill it made is already gone; it waits for an answer again."
    if fact is not None and _changed_since(session, fact, f"bill:{bill_id}"):
        raise ValueError(f"A later message for {bill.name} changed it since; it stays. Change it on Bills if it is not yours.")
    if session.exec(select(Payment).where(Payment.bill_id == bill_id, (Payment.status == "paid") | (Payment.transaction_id != None))).first() is not None:  # noqa: E711
        raise ValueError(f"{bill.name} has a payment matched or marked since; it stays. Remove it on Bills if it is not yours.")
    held = [m for m, field in ((Home, Home.bill_id), (HomeCharge, HomeCharge.bill_id), (HomeCost, HomeCost.bill_id), (Utility, Utility.bill_id)) if session.exec(select(m).where(field == bill_id)).first() is not None]
    if held or session.exec(select(MoveItem).where(MoveItem.ref == f"bill:{bill_id}")).first() is not None or session.exec(select(Document).where(Document.attach_to == f"bill:{bill_id}")).first() is not None:
        raise ValueError(f"{bill.name} is used elsewhere since (a home, a move or a kept file); it stays. Remove it on Bills if it is not yours.")
    for p in session.exec(select(Payment).where(Payment.bill_id == bill_id)).all():
        session.delete(p)
    session.flush()  # the periods go first: the database refuses a bill its periods still name
    session.delete(bill)
    return f"{bill.name}, which confirming it made, is gone; it waits for an answer again."


def _plan(session: Session, fact: ExtractedFact, plan_id: int) -> str:
    plan = session.get(InstallmentPlan, plan_id)
    if plan is None:
        return "The plan it made is already gone; it waits for an answer again."
    if _changed_since(session, fact, f"plan:{plan_id}"):
        raise ValueError(f"A later message for the {plan.provider} plan changed it since; it stays. End it on Borrowing if it is not yours.")
    left = fact.made.split(":")[2] if fact.made.count(":") >= 2 else ""
    if plan.debt_id is not None or not left.isdigit() or plan.remaining != int(left):
        raise ValueError(f"The {plan.provider} plan has a payment ticked or a debt tied to it since; it stays. End it on Borrowing if it is not yours.")
    session.delete(plan)
    return f"The {plan.provider} plan, which confirming it made, is gone; it waits for an answer again."


def _paystub(session: Session, fact: ExtractedFact, stub_id: int) -> str:
    stub = session.get(Paystub, stub_id)
    if stub is None:
        return "The paystub it kept is already gone; it waits for an answer again."
    if stub.fact_id != fact.id:
        raise ValueError("That paystub was kept before this message was confirmed; it stays.")
    if stub.transaction_id is not None:
        raise ValueError("That paystub is matched to its deposit since; it stays. Remove it on Paystubs if it is not yours.")
    for doc in session.exec(select(Document).where(Document.attach_to == f"paystub:{stub_id}")).all():
        doc.attach_to = ""  # the file is kept, with nothing to belong to
        session.add(doc)
    session.delete(stub)
    return "The paystub is gone; the usual net it may have set on your paycheck stays (change it on Paystubs if it is wrong). It waits for an answer again."
