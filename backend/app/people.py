"""A second person (M33): who they are, the password they set themselves, the invitation that lets them set it,
and what their role lets them see.

The owner signs in with FA_USERNAME and FA_PASSWORD, as before. A second person is added by the owner on
Settings › People, with a role:

- everything: sees and does what the owner does, except what only the owner may do (add or remove people,
  erase everything, restore the database, set the off-box copy, connect an app through OAuth) and except
  approving the owner's sends;
- shared: sees the accounts marked shared, and their own, through one page; nothing else.

The owner never types or sees the person's password: the owner hands over a one-time link, good for three days,
and the person chooses their password there. Passwords are stored as scrypt hashes. Each person's sends are
theirs to approve; nobody approves another's."""

from __future__ import annotations

import base64
import hashlib
import hmac
import json
import re
import secrets
from datetime import timedelta

from sqlmodel import Session, select

from .config import settings
from .models import Account, Passkey, Person, WebSession, utcnow

ROLES = {
    "everything": "Sees and does what you do, except adding people, erasing everything, restoring a backup, connecting apps, and approving your sends.",
    "shared": "Sees only the accounts you mark shared (and their own), on one page: balances, bills paid from them, recent spending.",
}
# The same roles in the words the person reads when they join.
ROLES_FOR_THEM = {
    "everything": "You see and do what {owner} does, except adding people, erasing everything, restoring a backup, connecting apps, and approving {owner}'s sends.",
    "shared": "You see the accounts {owner} marks shared, and your own, on one page: balances, the bills paid from them, recent spending.",
}
INVITE_HOURS = 72
MIN_PASSWORD = 12
USERNAME = re.compile(r"^[a-z0-9][a-z0-9._-]{1,31}$")
_N, _R, _P = 2**14, 8, 1


def hash_key(token: str) -> str:
    return hashlib.sha256(token.encode("utf-8")).hexdigest()


def hash_password(password: str) -> str:
    salt = secrets.token_bytes(16)
    digest = hashlib.scrypt(password.encode("utf-8"), salt=salt, n=_N, r=_R, p=_P, dklen=32)
    return f"scrypt${_N}${_R}${_P}${base64.b64encode(salt).decode()}${base64.b64encode(digest).decode()}"


def check_password(stored: str, password: str) -> bool:
    try:
        kind, n, r, p, salt, digest = stored.split("$")
        if kind != "scrypt":
            return False
        got = hashlib.scrypt(password.encode("utf-8"), salt=base64.b64decode(salt), n=int(n), r=int(r), p=int(p), dklen=32)
        return hmac.compare_digest(got, base64.b64decode(digest))
    except (ValueError, TypeError):
        return False


_DUMMY: list[str] = []


def check_nobody(password: str) -> None:
    """The same work as a real check, for a username nobody has, so the answer's timing does not tell who exists."""
    if not _DUMMY:
        _DUMMY.append(hash_password(secrets.token_urlsafe(12)))
    check_password(_DUMMY[0], password)


def password_problem(username: str, password: str) -> str | None:
    if len(password) < MIN_PASSWORD:
        return f"Choose a password of at least {MIN_PASSWORD} characters. A few words in a row are easy to remember and hard to guess."
    if len(password) > 256:
        return "That password is longer than 256 characters."
    if username and username.lower() in password.lower():
        return "Leave your username out of the password."
    return None


def username_problem(session: Session, username: str, *, except_id: int | None = None) -> str | None:
    if not USERNAME.fullmatch(username):
        return "A username is 2 to 32 lower-case letters, digits, dots, dashes or underscores, starting with a letter or digit."
    if username == settings.username.lower():
        return "That is the owner's username."
    taken = session.exec(select(Person).where(Person.username == username)).first()
    if taken is not None and taken.id != except_id:
        return "Someone already has that username."
    return None


def password_mark(person: Person) -> str:
    """Changes when the person's password changes, so a new password ends every session they had."""
    return hashlib.sha256((settings.signing_secret() + "\x00person\x00" + str(person.id) + "\x00" + person.password_hash).encode()).hexdigest()[:16]


def new_invite(session: Session, person: Person) -> str:
    token = secrets.token_urlsafe(32)
    person.invite_hash = hash_key(token)
    person.invite_expires_at = utcnow() + timedelta(hours=INVITE_HOURS)
    session.add(person)
    session.commit()
    return token


def by_invite(session: Session, token: str) -> Person | None:
    if not token or len(token) > 200:
        return None
    person = session.exec(select(Person).where(Person.invite_hash == hash_key(token))).first()
    if person is None or not person.active or person.invite_expires_at is None:
        return None
    expires = person.invite_expires_at if person.invite_expires_at.tzinfo else person.invite_expires_at.replace(tzinfo=utcnow().tzinfo)
    return person if expires > utcnow() else None


def by_username(session: Session, username: str) -> Person | None:
    person = session.exec(select(Person).where(Person.username == (username or "").strip().lower())).first()
    return person if person is not None and person.active and person.password_hash else None


def end_sessions(session: Session, person_id: int) -> int:
    ended = 0
    for row in session.exec(select(WebSession).where(WebSession.person_id == person_id, WebSession.revoked_at == None)).all():  # noqa: E711
        row.revoked_at = utcnow()
        session.add(row)
        ended += 1
    session.commit()
    return ended


def reset_sign_in(session: Session, person: Person) -> str:
    """Everything the person signs in with is cleared, every session ended, and a new invitation made."""
    person.password_hash, person.password_set_at = "", None
    person.totp_enc, person.totp_pending_enc, person.totp_recovery, person.totp_last_step, person.totp_since = "", "", "[]", 0, ""
    for key in session.exec(select(Passkey).where(Passkey.person_id == person.id)).all():
        session.delete(key)
    end_sessions(session, person.id)
    return new_invite(session, person)


def visible_account_ids(session: Session, person_id: int | None, role: str) -> set[int] | None:
    """The accounts a sign-in may see: every one (None) for the owner and for "everything"; for "shared", the
    accounts marked shared and the person's own."""
    if person_id is None or role != "shared":
        return None
    rows = session.exec(select(Account).where(Account.active == True)).all()  # noqa: E712
    return {a.id for a in rows if a.shared or a.person_id == person_id}


def allowances(person: Person) -> list[str]:
    try:
        return [str(a).lower() for a in json.loads(person.allowances or "[]")]
    except json.JSONDecodeError:
        return []


def person_out(person: Person) -> dict:
    now = utcnow()
    expires = person.invite_expires_at.replace(tzinfo=now.tzinfo) if person.invite_expires_at and person.invite_expires_at.tzinfo is None else person.invite_expires_at
    return {
        "id": person.id, "username": person.username, "display_name": person.display_name or person.username, "role": person.role, "role_words": ROLES.get(person.role, ""),
        "active": person.active, "password_set": bool(person.password_hash), "second_step": bool(person.totp_enc),
        "invite_open": bool(person.invite_hash and expires and expires > now), "invite_expires_at": expires.isoformat() if expires else None,
        "created_at": person.created_at.isoformat(), "last_sign_in_at": person.last_sign_in_at.isoformat() if person.last_sign_in_at else None,
        "source": f"person:{person.id}",
    }
