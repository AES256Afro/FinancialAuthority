"""Ledger rules: the owner's standing instructions for naming, categorising and hiding transactions.

A rule matches a substring of the merchant (or the raw name, or any text on the row) and renames, recategorises
or hides the row. Rules apply to every row already in the ledger when they are made, and to every row a
connector or an import adds afterwards, so a merchant is cleaned once and stays clean."""

from __future__ import annotations

import re
from functools import lru_cache

from sqlmodel import Session, select

from ..models import Rule, Transaction

KINDS = ("rename", "categorise", "hide")
FIELDS = ("merchant", "raw_name", "any")
CACHE_KEY = "ledger_rules"


def load_rules(session: Session) -> list[Rule]:
    return list(session.exec(select(Rule).where(Rule.kind.in_(KINDS)).order_by(Rule.id)).all())  # type: ignore[attr-defined]


def rules_for(session: Session) -> list[Rule]:
    """The rules, loaded once per session: connectors call this for every row they add."""
    if CACHE_KEY not in session.info:
        session.info[CACHE_KEY] = load_rules(session)
    return session.info[CACHE_KEY]


def forget(session: Session) -> None:
    session.info.pop(CACHE_KEY, None)


NESTED_QUANTIFIER = re.compile(r"\([^)]*(?:[*+?}]|\|)[^)]*\)\s*[*+?{]")


@lru_cache(maxsize=256)
def _rule_regex(source: str) -> re.Pattern | None:
    """A saved rule's pattern, compiled once. A quantified group that holds a quantifier or an alternation (the shape
    that backtracks without end) is refused, as is one that does not compile."""
    if NESTED_QUANTIFIER.search(source):
        return None
    try:
        return re.compile(source, re.IGNORECASE)
    except re.error:
        return None


def matches(rule: Rule, t: Transaction) -> bool:
    field = rule.match_field or "merchant"
    haystack = {"merchant": t.merchant, "raw_name": t.raw_name}.get(field, f"{t.merchant} {t.raw_name} {t.description}").lower()
    pattern = rule.pattern.strip()
    if not pattern:
        return False
    if pattern.startswith("re:"):
        compiled = _rule_regex(pattern[3:])
        # Bounded: a saved pattern runs on every row in every detection, under the writer lock.
        return compiled is not None and compiled.search(haystack[:400]) is not None
    return pattern.lower() in haystack


def apply_rules(rules: list[Rule], t: Transaction) -> list[str]:
    """Apply every matching rule to one row, in creation order. Returns what changed."""
    changed: list[str] = []
    for rule in rules:
        if not matches(rule, t):
            continue
        if rule.kind == "rename" and t.merchant != rule.value:
            t.merchant = rule.value
            changed.append("merchant")
        elif rule.kind == "categorise" and t.category != rule.value:
            t.category = rule.value
            t.is_transfer = rule.value == "Transfer"
            t.is_card_payment = rule.value == "Credit Card Payment"
            t.is_income = rule.value == "Income"
            changed.append("category")
        elif rule.kind == "hide" and not t.is_ignored:
            t.is_ignored = True
            changed.append("hidden")
    return changed


def apply_to_ledger(session: Session, rules: list[Rule] | None = None) -> int:
    """Run the rules over every row; return how many rows changed."""
    rules = load_rules(session) if rules is None else rules
    if not rules:
        return 0
    count = 0
    for t in session.exec(select(Transaction)).all():
        if apply_rules(rules, t):
            session.add(t)
            count += 1
    session.commit()
    forget(session)
    return count


def create_rule(session: Session, *, kind: str, pattern: str, value: str = "", match_field: str = "merchant", source: str = "owner") -> tuple[Rule, int]:
    if kind not in KINDS:
        raise ValueError("kind is rename, categorise or hide")
    if match_field not in FIELDS:
        raise ValueError("match_field is merchant, raw_name or any")
    if not pattern.strip():
        raise ValueError("a rule needs text to match")
    if kind != "hide" and not value.strip():
        raise ValueError("a rename or categorise rule needs a value")
    rule = Rule(kind=kind, match_field=match_field, pattern=pattern.strip(), action=kind, value=value.strip(), source=source)
    session.add(rule)
    session.commit()
    session.refresh(rule)
    forget(session)
    changed = apply_to_ledger(session, [rule])
    return rule, changed


def rule_out(rule: Rule) -> dict:
    return {"id": rule.id, "kind": rule.kind, "match_field": rule.match_field, "pattern": rule.pattern, "value": rule.value, "source": rule.source, "created_at": rule.created_at.isoformat()}
