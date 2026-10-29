"""People (M33): the owner adds a second person with a role and hands over a one-time link; accounts are marked
shared or whose; the shared page is what a "shared" sign-in sees."""

from __future__ import annotations

from fastapi import APIRouter, Depends, HTTPException, Request
from sqlmodel import Session, select

from .inputs import In
from .. import auth, people, services
from ..config import settings
from ..db import get_session
from ..engine.shared import shared_view
from ..models import Account, Passkey, Person
from .routes import today, under_writer

router = APIRouter(prefix="/api/v1", tags=["people"])


def _owner(principal: auth.Principal = Depends(auth.require_owner)) -> auth.Principal:
    if principal.kind != "owner":
        raise HTTPException(403, "Only the owner adds, changes or removes people.")
    return principal


def _link(request: Request, token: str) -> str:
    base = (settings.public_url or str(request.base_url)).rstrip("/")
    return f"{base}/join#{token}"  # after the #, so the token never reaches a server log or a referrer


def _overview(session: Session) -> dict:
    rows = session.exec(select(Person).order_by(Person.created_at)).all()
    accounts = session.exec(select(Account).where(Account.active == True).order_by(Account.name)).all()  # noqa: E712
    return {
        "people": [people.person_out(p) for p in rows], "roles": people.ROLES, "owner": settings.username,
        "accounts": [{"id": a.id, "name": a.name, "institution": a.institution, "kind": a.kind, "shared": a.shared, "person_id": a.person_id, "source": f"account:{a.id}"} for a in accounts],
        "how": "The person opens the link and chooses their own password; you never see it. The link works once, for three days.",
    }


@router.get("/people")
def list_people(session: Session = Depends(get_session), _: auth.Principal = Depends(_owner)) -> dict:
    return _overview(session)


class PersonIn(In):
    username: str
    display_name: str = ""
    role: str = "shared"


@router.post("/people")
def add_person(body: PersonIn, request: Request, session: Session = Depends(get_session), principal: auth.Principal = Depends(_owner)) -> dict:
    username = body.username.strip().lower()
    problem = people.username_problem(session, username)
    if problem:
        raise HTTPException(400, problem)
    if body.role not in people.ROLES:
        raise HTTPException(400, "The role is everything or shared")
    person = Person(username=username, display_name=body.display_name.strip()[:60], role=body.role)
    session.add(person)
    session.commit()
    session.refresh(person)
    token = people.new_invite(session, person)
    services.audit(session, client=principal.name, client_kind="owner", tool="person_add", args={"person": person.id, "role": person.role}, rows=1)
    return {**_overview(session), "invite": {"person_id": person.id, "link": _link(request, token), "expires_at": person.invite_expires_at.isoformat()}}


class PersonEdit(In):
    display_name: str | None = None
    role: str | None = None
    active: bool | None = None


@router.put("/people/{person_id}")
def change_person(person_id: int, body: PersonEdit, session: Session = Depends(get_session), principal: auth.Principal = Depends(_owner)) -> dict:
    person = session.get(Person, person_id)
    if person is None:
        raise HTTPException(404, "No such person")
    if body.role is not None:
        if body.role not in people.ROLES:
            raise HTTPException(400, "The role is everything or shared")
        person.role = body.role
    if body.display_name is not None:
        person.display_name = body.display_name.strip()[:60]
    if body.active is not None:
        person.active = body.active
    session.add(person)
    session.commit()
    if body.active is False or body.role is not None:
        people.end_access(session, person.id)  # a new role starts with a new sign-in, and nothing they set up carries over
    services.audit(session, client=principal.name, client_kind="owner", tool="person_change", args=body.model_dump(exclude_none=True) | {"person": person_id}, rows=1)
    return _overview(session)


@router.post("/people/{person_id}/invite")
def reinvite(person_id: int, request: Request, session: Session = Depends(get_session), principal: auth.Principal = Depends(_owner)) -> dict:
    """A new one-time link. Their password, second step and passkeys are cleared and every session ends, so this
    is also how a lost password, phone or passkey is put right."""
    person = session.get(Person, person_id)
    if person is None or not person.active:
        raise HTTPException(404, "No such person, or they are turned off")
    token = people.reset_sign_in(session, person)
    services.audit(session, client=principal.name, client_kind="owner", tool="person_reinvite", args={"person": person_id}, rows=1)
    return {**_overview(session), "invite": {"person_id": person.id, "link": _link(request, token), "expires_at": person.invite_expires_at.isoformat()}}


@router.delete("/people/{person_id}")
def remove_person(person_id: int, session: Session = Depends(get_session), principal: auth.Principal = Depends(_owner)) -> dict:
    person = session.get(Person, person_id)
    if person is None:
        raise HTTPException(404, "No such person")
    people.end_access(session, person.id)
    for key in session.exec(select(Passkey).where(Passkey.person_id == person.id)).all():
        session.delete(key)
    from ..models import Approval

    for row in session.exec(select(Approval).where(Approval.for_person_id == person.id)).all():
        row.for_person_id = -person.id  # theirs in the history; a new person given the same number never inherits it
        session.add(row)
    for a in session.exec(select(Account).where(Account.person_id == person.id)).all():
        a.person_id = None  # the account stays; it is no longer marked as theirs
        session.add(a)
    session.delete(person)
    session.commit()
    services.audit(session, client=principal.name, client_kind="owner", tool="person_remove", args={"person": person_id}, rows=1)
    return _overview(session)


class SharingIn(In):
    shared: bool
    person_id: int | None = None


@router.put("/accounts/{account_id}/sharing")
@under_writer
def account_sharing(account_id: int, body: SharingIn, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_admin)) -> dict:
    """Mark an account shared, or whose it is (the owner's or the second person's)."""
    if not principal.full:
        raise HTTPException(403, "Only a person signed in marks accounts")
    account = session.get(Account, account_id)
    if account is None:
        raise HTTPException(404, "No such account")
    if body.person_id is not None and session.get(Person, body.person_id) is None:
        raise HTTPException(400, "No such person")
    account.shared, account.person_id = body.shared, body.person_id
    session.add(account)
    session.commit()
    return {"id": account.id, "shared": account.shared, "person_id": account.person_id}


@router.get("/shared")
def get_shared(person_id: int | None = None, session: Session = Depends(get_session), principal: auth.Principal = Depends(auth.require_read)) -> dict:
    """A "shared" sign-in gets its own view; the owner (or a person with everything) may look at any person's."""
    if principal.kind == "person" and principal.role == "shared":
        person_id = principal.person_id
    elif not principal.full:
        raise HTTPException(403, "The shared page is for a person signed in")
    return shared_view(session, today(), person_id)
