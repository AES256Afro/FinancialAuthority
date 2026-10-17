"""Backups that prove themselves (M27): an encrypted copy of the database and the kept documents in a folder off
the data disk, and a weekly restore drill.

The off-box copy goes to a folder the server mounts: a USB drive, a second disk, or a folder another program
syncs to a cloud (Syncthing, rclone, a NAS share). Each copy is a restore point with every document file its
database names, sealed with AES-256-GCM under a key made from the owner's own passphrase (scrypt), so the file is
useless without it. The point is the newest one when it is a daily, by-hand or off-box point under two hours old,
else one taken for the copy, of the off-box kind (never a by-hand one, so the copies never push out the points the
owner took). The passphrase is kept encrypted with the data key only so the daily job can run; the owner keeps it
too, because it is what opens a copy after a disaster:

    python -m app.offbox open financialauthority-20261016.fabackup restored.db

asks for the passphrase, writes the database back out, and puts the documents in a documents folder beside it.
The database's secrets and the document files stay sealed with FA_DATA_KEY, as they are on the server, so the data
key is needed too; put restored.db and documents/ in the data folder of an app started with the same FA_DATA_KEY.

The file format. A copy made before documents were carried starts with FABK1 and seals the database alone in one
piece; it still opens. A copy now starts with FABK2: the scrypt salt, then the sealed contents in pieces of one
megabyte, each with its own nonce and its length, and its number and whether it is the last bound into the seal,
so a piece cut off, swapped or added fails to open. The contents are a zip: financialauthority.db, every document
as documents/<sha256>.bin, and manifest.json. The drill opens the newest copy on its own, has SQLite check every
page, and compares what it holds with what was counted when it was taken."""

from __future__ import annotations

import io
import json
import os
import sqlite3
import struct
import tempfile
import zipfile
from collections.abc import Callable
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, BinaryIO

from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt
from sqlmodel import Session

from . import restore
from .config import settings
from .store import get_setting, set_setting

MAGIC = b"FABK1"  # the database alone, sealed in one piece
MAGIC2 = b"FABK2"  # the database and the documents, a zip sealed in pieces
PIECE = 1024 * 1024
DB_NAME = "financialauthority.db"
SETTING = "backups.offbox"
DRILL = "backups.drill"
KEEP = 14
SUFFIX = ".fabackup"
REUSE_HOURS = 2  # a daily point this fresh is sealed as it is; an older one gets a fresh point of its own


def _key(passphrase: str, salt: bytes) -> bytes:
    return Scrypt(salt=salt, length=32, n=2**15, r=8, p=1).derive(passphrase.encode("utf-8"))


def seal(data: bytes, passphrase: str) -> bytes:
    salt, nonce = os.urandom(16), os.urandom(12)
    return MAGIC + salt + nonce + AESGCM(_key(passphrase, salt)).encrypt(nonce, data, MAGIC)


def unseal(blob: bytes, passphrase: str) -> bytes:
    """The database a copy holds, from either format."""
    if blob.startswith(MAGIC2):
        out = io.BytesIO()
        _open_pieces(io.BytesIO(blob), passphrase, out.write)
        with zipfile.ZipFile(out) as z:
            return z.read(DB_NAME)
    if not blob.startswith(MAGIC):
        raise ValueError("Not a FinancialAuthority backup")
    salt, nonce, body = blob[5:21], blob[21:33], blob[33:]
    return AESGCM(_key(passphrase, salt)).decrypt(nonce, body, MAGIC)


def _aad(n: int, last: bool) -> bytes:
    return MAGIC2 + struct.pack(">Q?", n, last)


def _seal_pieces(source: BinaryIO, passphrase: str, target: BinaryIO) -> int:
    """FABK2: the salt, then each piece as nonce, length and sealed bytes. Returns the bytes written."""
    salt = os.urandom(16)
    aes = AESGCM(_key(passphrase, salt))
    target.write(MAGIC2 + salt)
    written, n = len(MAGIC2) + 16, 0
    piece = source.read(PIECE)
    while True:
        following = source.read(PIECE)
        last = not following
        nonce = os.urandom(12)
        sealed = aes.encrypt(nonce, piece, _aad(n, last))
        target.write(nonce + struct.pack(">I", len(sealed)) + sealed)
        written += 12 + 4 + len(sealed)
        if last:
            return written
        piece, n = following, n + 1


def _open_pieces(source: BinaryIO, passphrase: str, write: Callable[[bytes], Any]) -> None:
    if source.read(len(MAGIC2)) != MAGIC2:
        raise ValueError("Not a FinancialAuthority backup with documents")
    aes = AESGCM(_key(passphrase, source.read(16)))
    n, head = 0, source.read(16)
    while True:
        if len(head) < 16:
            raise ValueError("The copy is cut short")
        nonce, size = head[:12], struct.unpack(">I", head[12:])[0]
        sealed = source.read(size)
        if len(sealed) != size:
            raise ValueError("The copy is cut short")
        following = source.read(16)
        last = not following  # a copy cut at a piece's end fails here: that piece was not sealed as the last
        write(aes.decrypt(nonce, sealed, _aad(n, last)))
        if last:
            return
        head, n = following, n + 1


def open_copy(copy: Path, passphrase: str, out_db: Path) -> dict[str, Any]:
    """Write a copy's database to out_db and its documents to a documents folder beside it."""
    with copy.open("rb") as f:
        head = f.read(len(MAGIC2))
    if head != MAGIC2:
        out_db.write_bytes(unseal(copy.read_bytes(), passphrase))
        return {"written": str(out_db), "documents": 0, "format": "FABK1 (the database alone)"}
    folder = out_db.parent / "documents"
    with tempfile.TemporaryDirectory(dir=out_db.parent) as scratch:
        plain = Path(scratch) / "copy.zip"
        with copy.open("rb") as f, plain.open("wb") as z:
            _open_pieces(f, passphrase, z.write)
        count = 0
        with zipfile.ZipFile(plain) as z:
            for info in z.infolist():
                if info.filename == DB_NAME:
                    with z.open(info) as src, out_db.open("wb") as dst:
                        _pipe(src, dst)
                elif info.filename.startswith("documents/") and restore.SHA.fullmatch(Path(info.filename).stem) and info.filename.endswith(".bin"):
                    folder.mkdir(parents=True, exist_ok=True)
                    with z.open(info) as src, (folder / Path(info.filename).name).open("wb") as dst:
                        _pipe(src, dst)
                    count += 1
    return {"written": str(out_db), "documents": count, "documents_folder": str(folder), "format": "FABK2 (the database and the documents)"}


def _pipe(src: BinaryIO, dst: BinaryIO) -> None:
    while chunk := src.read(PIECE):
        dst.write(chunk)


def _write_copy(point: Path, passphrase: str, target: Path) -> dict[str, Any]:
    """Seal a restore point and the document files its database names into target (FABK2)."""
    shas = sorted(restore.document_shas(point))
    where = restore.documents_folder()
    present = [s for s in shas if (where / f"{s}.bin").exists()]
    with tempfile.TemporaryDirectory(dir=restore.folder()) as scratch:
        plain = Path(scratch) / "copy.zip"
        with zipfile.ZipFile(plain, "w") as z:
            z.write(point, DB_NAME, compress_type=zipfile.ZIP_DEFLATED)
            for s in present:
                z.write(where / f"{s}.bin", f"documents/{s}.bin", compress_type=zipfile.ZIP_STORED)  # sealed already: it would not shrink
            z.writestr("manifest.json", json.dumps({"made": datetime.now(UTC).isoformat(), "point": point.name, "documents": len(present),
                                                    "documents_missing": [s for s in shas if s not in present]}))
        with plain.open("rb") as src, target.open("wb") as dst:
            written = _seal_pieces(src, passphrase, dst)
    return {"bytes": written, "documents": len(present), "documents_missing": len(shas) - len(present)}


def _point_for_copy() -> dict[str, Any]:
    """The newest point when it is a daily, by-hand or off-box one under two hours old (the database as it is now),
    else a fresh point of the off-box kind, which keeps its own three: the copies never push out the owner's own."""
    now = datetime.now(UTC)
    for p in restore.list_points():
        if p["kind"] in ("daily", "manual", "offbox") and p["created_at"]:
            try:
                if now - datetime.fromisoformat(p["created_at"]) < timedelta(hours=REUSE_HOURS):
                    return p
            except ValueError:
                continue
        break  # only the newest point is fresh enough to stand for now
    return restore.make_point("offbox", "For the off-box copy")


def _writable(folder: Path) -> None:
    probe = folder / ".financialauthority-write-check"
    probe.write_bytes(b"ok")
    probe.unlink(missing_ok=True)


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
    """Seal a restore point (see _point_for_copy) and its documents into the folder, keep the newest few. The folder
    is tried first: a drive that is full or gone takes no restore point at all."""
    from . import crypto

    c = config(session)
    if not c["folder"] or not c["passphrase_enc"]:
        raise ValueError("Choose a folder and a passphrase first")
    folder = Path(c["folder"])
    partial: Path | None = None
    try:
        if not folder.is_dir():
            raise ValueError(f"{folder} is not a folder the server can see; mount the drive or the synced folder there")
        _writable(folder)
        passphrase = crypto.decrypt(c["passphrase_enc"])
        point = _point_for_copy()
        name = f"financialauthority-{datetime.now(UTC):%Y%m%d-%H%M%S}{SUFFIX}"
        partial = folder / f".{name}.partial"
        made = _write_copy(restore.folder() / point["name"], passphrase, partial)
        partial.replace(folder / name)
        partial = None
        for old in sorted(folder.glob(f"financialauthority-*{SUFFIX}"), reverse=True)[KEEP:]:
            old.unlink(missing_ok=True)
        c.update(last_at=datetime.now(UTC).isoformat(), last_file=name, last_error="")
        set_setting(session, SETTING, c)
        return {"file": name, "bytes": made["bytes"], "folder": str(folder), "point": point["name"], "documents": made["documents"], "documents_missing": made["documents_missing"]}
    except Exception as error:
        if partial is not None:
            partial.unlink(missing_ok=True)
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
    out = Path(args[2])
    try:
        opened = open_copy(Path(args[1]), passphrase, out)
    except Exception:  # noqa: BLE001 - the one message that helps
        print("That passphrase does not open this copy, or the file is damaged.")
        return 1
    print(json.dumps({**opened, "check": restore.check(out)["result"]}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
