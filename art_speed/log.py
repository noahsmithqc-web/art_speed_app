"""Rotating log file. Passwords are masked before anything is written."""

from __future__ import annotations

import logging
import re
import sys
from logging.handlers import RotatingFileHandler

from art_speed import config, paths

_LOGGER_NAME = "art_speed"
_FORMAT = "%(asctime)s  %(levelname)-7s  %(name)s  %(message)s"

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
    # The value runs to the next space or comma, so MySQL's closing bracket comes with it.
    value = match.group("val").rstrip(")].,;")
    if match.group("using") and value in ("YES", "NO"):
        return match.group(0)
    return (match.group("using") or "") + match.group("key") + "***"


def redact(text: str) -> str:
    return _SECRET.sub(_mask, text)


class RedactingFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return redact(super().format(record))


def setup_logging(level: int = logging.INFO) -> logging.Logger:
    """Configure the app logger once. Falls back to the console if the log folder is unusable."""
    logger = logging.getLogger(_LOGGER_NAME)
    if getattr(logger, "_speed_configured", False):
        return logger
    logger.setLevel(level)
    logger.propagate = False
    formatter = RedactingFormatter(_FORMAT)
    try:
        paths.ensure_app_home()
        handler: logging.Handler = RotatingFileHandler(
            paths.log_file(), maxBytes=config.LOG_MAX_BYTES,
            backupCount=config.LOG_BACKUPS, encoding="utf-8")
    except OSError as exc:
        handler = logging.StreamHandler(sys.stderr)
        handler.setFormatter(formatter)
        handler._speed_handler = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
        logger.warning("log file unavailable (%s); logging to console", exc)
    else:
        handler.setFormatter(formatter)
        handler._speed_handler = True  # type: ignore[attr-defined]
        logger.addHandler(handler)
    logger._speed_configured = True  # type: ignore[attr-defined]
    return logger


def app_handlers() -> list[logging.Handler]:
    """The handlers setup_logging() added (ignores any added by test tools)."""
    return [h for h in logging.getLogger(_LOGGER_NAME).handlers if getattr(h, "_speed_handler", False)]


def reset_logging() -> None:
    """Tests only: drop handlers so the next setup_logging() starts fresh."""
    logger = logging.getLogger(_LOGGER_NAME)
    for h in app_handlers():
        logger.removeHandler(h)
        h.close()
    logger._speed_configured = False  # type: ignore[attr-defined]


def get_logger(name: str) -> logging.Logger:
    """Module logger, e.g. get_logger(__name__)."""
    if not name.startswith(_LOGGER_NAME):
        name = f"{_LOGGER_NAME}.{name}"
    return logging.getLogger(name)
