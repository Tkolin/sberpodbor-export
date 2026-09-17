"""Character-level helpers for XML output.

The allowed-character ranges are built from code points rather than written as string
escapes on purpose: a literal control character in source is invisible, easy to mangle in
transit, and produces a syntax error that points at the wrong place.
"""

from __future__ import annotations

import re
from datetime import datetime
from typing import Any
from xml.sax.saxutils import escape, quoteattr

# XML 1.0 §2.2 Char: tab, LF, CR, then the printable planes. Everything else — the
# remaining C0 controls, lone surrogates, FFFE/FFFF — cannot be represented at all.
_ALLOWED_RANGES = (
    (0x09, 0x09),
    (0x0A, 0x0A),
    (0x0D, 0x0D),
    (0x20, 0xD7FF),
    (0xE000, 0xFFFD),
    (0x10000, 0x10FFFF),
)

_ILLEGAL = re.compile(
    "[^" + "".join(f"{chr(lo)}-{chr(hi)}" for lo, hi in _ALLOWED_RANGES) + "]"
)


def clean(value: Any) -> str:
    """Drop characters XML cannot carry. Never raises."""
    if value is None:
        return ""
    return _ILLEGAL.sub("", str(value))


def text(value: Any) -> str:
    """Escaped element content."""
    return escape(clean(value))


def attr(name: str, value: Any) -> str:
    """Renders ` name="value"`, or nothing at all when the value is absent."""
    if value is None or value == "":
        return ""
    if isinstance(value, bool):
        value = "true" if value else "false"
    if isinstance(value, datetime):
        value = value.isoformat()
    return f" {name}={quoteattr(clean(value))}"
