"""Parsers: documents (paystubs, statements) read into facts. Deterministic first; a model second, later."""

import re

_SPACES = re.compile(r"[\t\xa0\u1680\u2000-\u200a\u202f\u205f\u3000]")


def plain_spaces(text: str | None) -> str:
    """A tab or a non-breaking or typographic space as a space, one for one. An HTML mail writes "Net&nbsp;Pay" to keep a
    label on one line and the mail reader keeps the character; every label here is matched with a plain space."""
    return _SPACES.sub(" ", text or "")
