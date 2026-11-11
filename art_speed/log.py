"""Logging, with passwords masked. Adapted from A.R.T.'s log.py (see COPIED_FROM_ART.md).

The masking is A.R.T.'s, unchanged. The rotating log file is not here yet: until the shell
exists (SP-6) the tool has no app-state folder of its own, so records go wherever the caller's
logging is configured (the console under the CLI, pytest's capture under tests).
"""

from __future__ import annotations

import logging
import re

_LOGGER_NAME = "art_speed"

# password=..., pwd: ..., "password": "..."  (case-insensitive)
_SECRET = re.compile(
    r"""(?ix)
    (?P<using>using\s+)?
    (?P<key>["']?(?:password|passwd|pwd)["']?\s*[:=]\s*)
    (?P<val>"[^"]*"|'[^']*'|[^\s,;}]+)
    """
)


def _mask(match: "re.Match") -> str:
    """Replace a password with ***, with one deliberate exception.

    MySQL's own refusal reads "(using password: YES)", where YES or NO says whether a password
    was sent at all - not what it was. Masking it hides the one thing that tells a missing
    password from a wrong one. The carve-out is as narrow as it can be: the words must be
    preceded by "using", and must be exactly YES or NO in capitals, which is how MySQL writes
    them. A user whose password happens to be "yes" is still masked.
    """
    value = match.group("val").rstrip(")].,;")
    if match.group("using") and value in ("YES", "NO"):
        return match.group(0)
    return (match.group("using") or "") + match.group("key") + "***"


def redact(text: str) -> str:
    return _SECRET.sub(_mask, text)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def get_logger(name: str) -> logging.Logger:
    """Module logger, e.g. get_logger(__name__)."""
    if not name.startswith(_LOGGER_NAME):
        name = f"{_LOGGER_NAME}.{name}"
    return logging.getLogger(name)
