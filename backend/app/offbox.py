"""Backups that prove themselves (M27): an encrypted copy of the database in a folder off the data disk, and a
weekly restore drill.

The off-box copy goes to a folder the server mounts: a USB drive, a second disk, or a folder another program
syncs to a cloud (Syncthing, rclone, a NAS share). Each copy is the newest restore point, sealed with AES-256-GCM
under a key made from the owner's own passphrase (scrypt), so the file is useless without it and does not depend
on FA_DATA_KEY. The passphrase is kept encrypted with the data key only so the daily job can run; the owner keeps
it too, because it is what opens a copy after a disaster:

    python -m app.offbox open financialauthority-20261016.fabackup restored.db

asks for the passphrase and writes the database back out. The drill opens the newest copy on its own, has SQLite
check every page, and compares what it holds with what was counted when it was taken."""

from __future__ import annotations

import json
import os
import sqlite3
import tempfile
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlmodel import Session

from . import restore
from .config import settings
from .store import get_setting, set_setting

MAGIC = b"FABK1"
SETTING = "backups.offbox"
DRILL = "backups.drill"
KEEP = 14
SUFFIX = ".fabackup"


def _key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


def seal(data: bytes, passphrase: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    return MAGIC + salt + nonce + AESGCM(_key(passphrase, salt)).encrypt(nonce, data, MAGIC)


def unseal(blob: bytes, passphrase: str) -> bytes:
    if not blob.startswith(MAGIC):
        raise ValueError("Not a FinancialAuthority backup")
    salt, nonce, body = blob[5:21], blob[21:33], blob[33:]
    return AESGCM(_key(passphrase, salt)).decrypt(nonce, body, MAGIC)


def config(session: Session) -> dict[str, Any]:
    return {"folder": "", "passphrase_enc": "", "every_days": 1, "last_at": None, "last_file": "", "last_error": "", **(get_setting(session, SETTING, {}) or {})}


def state(session: Session) -> dict[str, Any]:
    c = config(session)
    folder = Path(c["folder"]) if c["folder"] else None
    copies = sorted((p.name for p in folder.glob(f"*{SUFFIX}")), reverse=True) if folder and folder.is_dir() else []
    return {"folder": c["folder"], "passphrase_set": bool(c["passphrase_enc"]), "every_days": c["every_days"], "last_at": c["last_at"], "last_file": c["last_file"],
            "last_error": c["last_error"], "copies": copies[:KEEP], "folder_ok": bool(folder and folder.is_dir() and os.access(folder, os.W_OK)),
            "suggested": settings.offbox_dir or "", "drill": get_setting(session, DRILL, None)}


def save(session: Session, *, folder: str | None = None, passphrase: str | None = None, every_days: int | None = None) -> dict[str, Any]:
    from . import crypto

    c = config(session)
    if folder is not None:
        path = Path(folder.strip())
        if folder.strip() and not path.is_absolute():
            raise ValueError("Give the folder's full path as the server sees it, for example /offbox")
        c["folder"] = str(path) if folder.strip() else ""
    if passphrase is not None:
        if len(passphrase) < 12:
            raise ValueError("Use a passphrase of at least 12 characters; four or five words are easy to keep")
        c["passphrase_enc"] = crypto.encrypt(passphrase)
    if every_days is not None:
        if every_days not in (1, 7):
            raise ValueError("Every day or every week")
        c["every_days"] = every_days
    set_setting(session, SETTING, c)
    return state(session)


def copy_now(session: Session) -> dict[str, Any]:
    """Seal the newest restore point (a fresh one if none is from today) into the folder, keep the newest few."""
    from . import crypto

    c = config(session)
    if not c["folder"] or not c["passphrase_enc"]:
        raise ValueError("Choose a folder and a passphrase first")
    folder = Path(c["folder"])
    try:
        if not folder.is_dir():
            raise ValueError(f"{folder} is not a folder the server can see; mount the drive or the synced folder there")
        point = restore.make_point("manual", "For the off-box copy")
        blob = seal((restore.folder() / point["name"]).read_bytes(), crypto.decrypt(c["passphrase_enc"]))
        name = f"financialauthority-{datetime.now(UTC):%Y%m%d-%H%M%S}{SUFFIX}"
        partial = folder / f".{name}.partial"
        partial.write_bytes(blob)
        partial.replace(folder / name)
        for old in sorted(folder.glob(f"financialauthority-*{SUFFIX}"), reverse=True)[KEEP:]:
            old.unlink(missing_ok=True)
        c.update(last_at=datetime.now(UTC).isoformat(), last_file=name, last_error="")
        set_setting(session, SETTING, c)
        return {"file": name, "bytes": len(blob), "folder": str(folder)}
    except Exception as error:
        c["last_error"] = f"{type(error).__name__}: {error}"[:300]
        set_setting(session, SETTING, c)
        raise


def due(session: Session) -> bool:
    c = config(session)
    if not c["folder"] or not c["passphrase_enc"]:
        return False
    if not c["last_at"]:
        return True
    return datetime.now(UTC) - datetime.fromisoformat(c["last_at"]) >= timedelta(days=int(c["every_days"])) - timedelta(hours=1)


def drill(session: Session) -> dict[str, Any]:
    """Open the newest daily copy (else the newest of any kind) on its own, check every page, and compare what it
    holds with the count taken when it was made. The result is kept for the Backups page and the self-check."""
    points = restore.list_points()
    point = next((p for p in points if p["kind"] == "daily"), points[0] if points else None)
    if point is None:
        result = {"at": datetime.now(UTC).isoformat(), "ok": False, "detail": "There is no copy to test yet."}
        set_setting(session, DRILL, result)
        return result
    with tempfile.TemporaryDirectory() as scratch:
        copy = Path(scratch) / "drill.db"
        copy.write_bytes((restore.folder() / point["name"]).read_bytes())
        verdict = restore.check(copy)
        con = sqlite3.connect(str(copy))
        try:
            accounts = con.execute("SELECT count(*) FROM accounts").fetchone()[0]
            total = con.execute("SELECT round(coalesce(sum(amount), 0), 2) FROM transactions").fetchone()[0]
        finally:
            con.close()
    expected = point.get("transactions")
    same = expected is None or expected == verdict["transactions"]
    ok = bool(verdict["ok"] and same)
    detail = (f"The copy of {point['created_at'][:16].replace('T', ' ')} opened on its own, passed SQLite's check of every page, and holds "
              f"{verdict['transactions']} transactions in {accounts} accounts (net {total:,.2f}), as counted when it was made.") if ok else (
              f"The copy {point['name']} did not pass: {verdict['result']}" if not verdict["ok"] else
              f"The copy {point['name']} holds {verdict['transactions']} transactions; {expected} were counted when it was made.")
    result = {"at": datetime.now(UTC).isoformat(), "ok": ok, "point": point["name"], "detail": detail}
    set_setting(session, DRILL, result)
    return result


def main(argv: list[str] | None = None) -> int:
    """Open a sealed copy after a disaster: python -m app.offbox open <file> <out.db>. Asks for the passphrase."""
    import getpass
    import sys

    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 3 or args[0] != "open":
        print("Usage: python -m app.offbox open <copy.fabackup> <restored.db>")
        return 2
    passphrase = getpass.getpass("Passphrase: ")
    try:
        data = unseal(Path(args[1]).read_bytes(), passphrase)
    except Exception:  # noqa: BLE001 - the one message that helps
        print("That passphrase does not open this copy, or the file is damaged.")
        return 1
    Path(args[2]).write_bytes(data)
    print(json.dumps({"written": args[2], "check": restore.check(Path(args[2]))["result"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
