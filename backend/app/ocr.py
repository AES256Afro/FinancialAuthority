"""Reading a photo or a scan, free and on this server (M49): Tesseract reads the words, and poppler's pdftoppm draws
a scanned PDF's pages as pictures for it first. Both ship in the image; nothing leaves the server. A photo is
untrusted data like any file, so each read runs as its own process with a time limit, a memory limit and a size
limit, and a file that cannot be read in time simply has no text. Without the programs (a development machine),
photos and scans keep no text, as before."""

from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
import time
from datetime import date
from pathlib import Path
from typing import Any

TESSERACT = "tesseract"
PDFTOPPM = "pdftoppm"
MAX_IMAGE_BYTES = 15 * 1024 * 1024
IMAGE_SECONDS = 25.0  # one photo
SCAN_SECONDS = 60.0  # every page of one scanned PDF together
MAX_SCAN_PAGES = 8
MEMORY_BYTES = 1024 * 1024 * 1024
MAX_TEXT = 20_000


def available() -> bool:
    return shutil.which(TESSERACT) is not None


def scans_available() -> bool:
    return available() and shutil.which(PDFTOPPM) is not None


def _limits() -> None:  # pragma: no cover - runs in the child, on Linux
    import resource

    resource.setrlimit(resource.RLIMIT_AS, (MEMORY_BYTES, MEMORY_BYTES))
    resource.setrlimit(resource.RLIMIT_CPU, (int(SCAN_SECONDS) + 5, int(SCAN_SECONDS) + 5))


def _run(args: list[str], *, data: bytes | None, seconds: float) -> subprocess.CompletedProcess[bytes] | None:
    try:
        return subprocess.run(args, input=data, capture_output=True, timeout=max(1.0, seconds), check=False,  # noqa: S603 - fixed programs, no shell
                              preexec_fn=_limits if os.name == "posix" else None, env={"PATH": os.environ.get("PATH", ""), "OMP_THREAD_LIMIT": "1"})
    except (OSError, subprocess.SubprocessError):
        return None


def image_text(raw: bytes, seconds: float = IMAGE_SECONDS) -> str:
    """The words in a photo, or "" when it cannot be read (no reader, too big, too slow, not a picture)."""
    if not available() or not raw or len(raw) > MAX_IMAGE_BYTES:
        return ""
    done = _run([TESSERACT, "stdin", "stdout", "-l", "eng", "--psm", "3"], data=raw, seconds=seconds)
    if done is None or done.returncode != 0:
        return ""
    return done.stdout.decode("utf-8", errors="replace").strip()[:MAX_TEXT]


def scan_pages(raw: bytes, max_pages: int = MAX_SCAN_PAGES, seconds: float = SCAN_SECONDS) -> list[str]:
    """A scanned PDF (pages that are pictures) read page by page, under one time budget for the whole file."""
    if not scans_available() or not raw.startswith(b"%PDF"):
        return []
    started = time.monotonic()
    with tempfile.TemporaryDirectory(prefix="fa-scan-") as work:
        source = Path(work) / "in.pdf"
        source.write_bytes(raw)
        drawn = _run([PDFTOPPM, "-r", "200", "-gray", "-png", "-f", "1", "-l", str(max_pages), str(source), str(Path(work) / "page")], data=None, seconds=seconds / 2)
        if drawn is None or drawn.returncode != 0:
            return []
        pages = []
        for picture in sorted(Path(work).glob("page-*.png"), key=lambda p: int(re.sub(r"\D", "", p.stem) or 0)):
            left = seconds - (time.monotonic() - started)
            if left <= 1:
                break
            pages.append(image_text(picture.read_bytes(), seconds=min(IMAGE_SECONDS, left)))
        return pages


TOTAL = re.compile(r"(?im)^[^\S\n]*(?:grand[^\S\n]+)?total(?:[^\S\n]+(?:due|paid|amount))?[^\S\n]*[:\-]?[^\S\n]*(?:usd)?[^\S\n]*\$?[^\S\n]*([0-9]{1,3}(?:,[0-9]{3})*\.[0-9]{2}|[0-9]+\.[0-9]{2})\b")
DATE_US = re.compile(r"\b(0?[1-9]|1[0-2])[/-](0?[1-9]|[12][0-9]|3[01])[/-]((?:20)?[0-9]{2})\b")
DATE_ISO = re.compile(r"\b(20[0-9]{2})-(0[1-9]|1[0-2])-(0[1-9]|[12][0-9]|3[01])\b")


def receipt_reading(text: str, today: date) -> dict[str, Any]:
    """What a receipt's words seem to say: its total (the last "total" line, after the subtotal and tax) and the day
    (the first date on it, not after today). Offered to the owner to keep or not; never applied on its own."""
    out: dict[str, Any] = {}
    totals = TOTAL.findall(text or "")
    if totals:
        try:
            out["total"] = round(float(totals[-1].replace(",", "")), 2)
        except ValueError:
            pass
    for m in DATE_ISO.finditer(text or ""):
        try:
            d = date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            continue
        if d <= today:
            out["bought_on"] = d.isoformat()
            break
    if "bought_on" not in out:
        for m in DATE_US.finditer(text or ""):
            year = int(m.group(3))
            year = year + 2000 if year < 100 else year
            try:
                d = date(year, int(m.group(1)), int(m.group(2)))
            except ValueError:
                continue
            if d <= today:
                out["bought_on"] = d.isoformat()
                break
    return out
