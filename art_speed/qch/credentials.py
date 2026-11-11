"""Where the QC Hub login comes from. Adapted from A.R.T.'s qch/credentials.py.

The speed tool has no Settings screen of its own in 0.1.0, so it reuses the login the user
already set up in A.R.T. - read-only:

* **Development** - the file named by ART_QCH_CREDENTIALS (`key=value` lines: host, user,
  password, database, optional port). Same file A.R.T.'s development setup uses.
* **Production** - host, port, user and database from A.R.T.'s settings.json, the password from
  Windows Credential Manager under A.R.T.'s entry ("A.R.T. QCH").

Nothing here writes to A.R.T.'s settings or to the Credential Manager: A.R.T.'s Settings screen
stays the one place a login is entered. No login at all is not an error for the speed tool - the
import goes on and every sitenumber is "not checked against QC Hub" (plan SP-Q5).

The password is never written to the log, never put in an error message, and never returned by
anything that formats a connection for display. `Credentials.safe` is the only text form.
"""

from __future__ import annotations

import json
import os
import sys
from dataclasses import dataclass, field
from pathlib import Path

from art_speed import config, fsguard
from art_speed.log import get_logger

log = get_logger(__name__)

DEFAULT_PORT = 3306
KEYRING_SERVICE = config.ART_KEYRING_SERVICE

DEV_FILE = "dev file"
CREDENTIAL_MANAGER = "Credential Manager"

REQUIRED = ("host", "user", "password", "database")


class CredentialsMissing(Exception):
    """No usable login. The message says what to do, never what was found."""


@dataclass
class Credentials:
    host: str
    user: str
    password: str = field(repr=False)     # never in a repr, a log line or an error
    database: str
    port: int = DEFAULT_PORT
    source: str = DEV_FILE

    @property
    def safe(self) -> str:
        """The connection as it may be shown or logged: everything except the password."""
        return f"{self.user}@{self.host}:{self.port}/{self.database}"

    def __str__(self) -> str:
        return self.safe

    def __repr__(self) -> str:                      # so a traceback can't leak it either
        return f"Credentials({self.safe}, from the {self.source})"


# ---------------------------------------------------------------- development file

def dev_file() -> Path | None:
    """The path in ART_QCH_CREDENTIALS, or None when the setting isn't set."""
    raw = os.environ.get(config.ENV_QCH_CREDENTIALS, "").strip().strip('"')
    return Path(os.path.expandvars(os.path.expanduser(raw))) if raw else None


def _parse(text: str) -> dict:
    """key=value lines. Blank lines and # comments ignored; keys are case-insensitive."""
    found: dict = {}
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        found[key.strip().lower()] = value.strip().strip('"').strip("'")
    return found


def from_dev_file(path: Path) -> Credentials:
    try:
        text = fsguard.guarded(path.read_text, encoding="utf-8-sig",
                               what="reading the credentials file")
    except FileNotFoundError as exc:
        raise CredentialsMissing(
            f"{config.ENV_QCH_CREDENTIALS} points at {path}, but there is no file there."
        ) from exc
    except (OSError, fsguard.FsTimeout) as exc:
        raise CredentialsMissing(f"The credentials file at {path} could not be read.") from exc

    values = _parse(text)
    absent = [key for key in REQUIRED if not values.get(key)]
    if absent:
        # Names only. The file's contents never reach the log or the screen.
        raise CredentialsMissing(
            f"The credentials file at {path} has no {', '.join(absent)}. "
            "It needs one key=value line for each of host, user, password, database.")
    port = values.get("port") or DEFAULT_PORT
    try:
        port = int(port)
    except (TypeError, ValueError) as exc:
        raise CredentialsMissing(f"The credentials file at {path} has a port that is not a "
                                 "number.") from exc
    return Credentials(host=values["host"], user=values["user"], password=values["password"],
                       database=values["database"], port=port, source=DEV_FILE)


# ---------------------------------------------------------------- A.R.T.'s saved connection

def art_settings_file() -> Path:
    """A.R.T.'s settings.json (%LOCALAPPDATA%\\ART, or ART_HOME in development). Read only."""
    override = os.environ.get(config.ENV_ART_HOME, "").strip()
    if override:
        home = Path(os.path.expandvars(os.path.expanduser(override)))
    elif sys.platform == "win32":
        base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
        home = Path(base) / config.ART_APP_DIR_NAME
    else:                                   # automated tests, Claude's sandbox
        base = os.environ.get("XDG_DATA_HOME") or str(Path.home() / ".local" / "share")
        home = Path(base) / config.ART_APP_DIR_NAME
    return home / "settings.json"


def art_connection() -> dict:
    """host / port / user / database as A.R.T. saved them, or {} when there is nothing usable."""
    path = art_settings_file()
    try:
        raw = json.loads(fsguard.guarded(path.read_text, encoding="utf-8-sig",
                                         what="reading A.R.T.'s settings"))
    except (OSError, ValueError, fsguard.FsTimeout):
        return {}
    qch = raw.get("qch") if isinstance(raw, dict) else None
    if not isinstance(qch, dict):
        return {}
    return {k: qch[k] for k in ("host", "user", "database", "port") if qch.get(k)}


def _keyring():
    """The keyring module, or None when it isn't installed."""
    try:
        import keyring
    except ImportError:                      # pragma: no cover - installed from requirements.txt
        log.warning("keyring is not installed; the Credential Manager cannot be read")
        return None
    return keyring


def stored_password(user: str) -> str | None:
    kr = _keyring()
    if kr is None:
        return None
    try:
        return kr.get_password(KEYRING_SERVICE, user)
    except Exception as exc:                 # keyring raises its own family of errors
        log.warning("could not read the Credential Manager (%s)", type(exc).__name__)
        return None


def from_credential_manager() -> Credentials:
    saved = art_connection()
    absent = [key for key in ("host", "user", "database") if not saved.get(key)]
    if absent:
        raise CredentialsMissing(
            "No QC Hub connection is set up yet. Add the host, user and database in A.R.T.'s "
            "Settings; the speed tool uses the same login.")
    password = stored_password(saved["user"])
    if not password:
        raise CredentialsMissing(
            f"No password is saved for {saved['user']}. Enter it again in A.R.T.'s Settings.")
    try:
        port = int(saved.get("port") or DEFAULT_PORT)
    except (TypeError, ValueError):
        port = DEFAULT_PORT
    return Credentials(host=str(saved["host"]).strip(), user=str(saved["user"]).strip(),
                       password=password, database=str(saved["database"]).strip(),
                       port=port, source=CREDENTIAL_MANAGER)


# ---------------------------------------------------------------- the one entry point

def load() -> Credentials:
    """The login to use. Raises CredentialsMissing with a message written for the user."""
    path = dev_file()
    if path is not None:
        found = from_dev_file(path)
        log.info("QC Hub credentials from the development file (%s)", found.safe)
        return found
    found = from_credential_manager()
    log.info("QC Hub credentials from the Credential Manager (%s)", found.safe)
    return found
