"""Where the QCH login comes from.

Two sources, and the app never mixes them:

* **Development** - a file outside any folder Claude can see, named by the SPEED_QCH_CREDENTIALS
  setting. `key=value`, one per line: host, user, password, database (port optional, 3306 by
  default). Used only while that setting exists.
* **Production** - the non-secret half (host, port, user, database) lives in settings.json where
  anyone can read it, and the password alone lives in Windows Credential Manager. Entered once in
  Settings (C7).

The password is never written to the log, never put in an error message, and never returned by
anything that formats a connection for display. `Credentials.safe` is the only text form.
"""

from __future__ import annotations

import os
from dataclasses import dataclass, field
from pathlib import Path

from art_speed import config, fsguard
from art_speed.appstate import settings
from art_speed.log import get_logger

log = get_logger(__name__)

DEFAULT_PORT = 3306
KEYRING_SERVICE = "Speed tool QCH"        # what shows in Windows Credential Manager

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
    """The path in SPEED_QCH_CREDENTIALS, or None when the setting isn't set."""
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
                               what=f"reading the credentials file")
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


# ---------------------------------------------------------------- Credential Manager

def _keyring():
    """The keyring module, or None when it isn't installed (so C4 can be tested without it)."""
    try:
        import keyring
    except ImportError:                      # pragma: no cover - present in the packaged app
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


def forget_password(user: str) -> None:
    """Remove a stored password. Used when a saved one turns out to be wrong."""
    kr = _keyring()
    if kr is None:
        return
    try:
        kr.delete_password(KEYRING_SERVICE, user)
        log.info("QCH password removed from the Credential Manager for %s", user)
    except Exception as exc:                 # no entry to remove is not a failure
        log.debug("no QCH password to remove for %s (%s)", user, type(exc).__name__)


def store_password(user: str, password: str) -> None:
    """Save the password for this user. Called from Settings (C7)."""
    kr = _keyring()
    if kr is None:
        raise CredentialsMissing("This copy of the app cannot reach Windows Credential Manager.")
    kr.set_password(KEYRING_SERVICE, user, password)
    log.info("QCH password stored in the Credential Manager for %s", user)


def from_credential_manager() -> Credentials:
    saved = settings.qch_connection()
    absent = [key for key in ("host", "user", "database") if not saved.get(key)]
    if absent:
        raise CredentialsMissing(
            "No QCH connection is set up yet. Add the host, user and database in Settings.")
    password = stored_password(saved["user"])
    if not password:
        raise CredentialsMissing(
            f"No password is saved for {saved['user']}. Enter it again in Settings.")
    return Credentials(host=saved["host"], user=saved["user"], password=password,
                       database=saved["database"],
                       port=int(saved.get("port") or DEFAULT_PORT),
                       source=CREDENTIAL_MANAGER)


# ---------------------------------------------------------------- the one entry point

def load() -> Credentials:
    """The login to use. Raises CredentialsMissing with a message written for the user."""
    path = dev_file()
    if path is not None:
        found = from_dev_file(path)
        log.info("QCH credentials from the development file (%s)", found.safe)
        return found
    found = from_credential_manager()
    log.info("QCH credentials from the Credential Manager (%s)", found.safe)
    return found


# ---------------------------------------------------------------- saving, checked

# What save_connection did. The screens and the command line both branch on these.
CONNECTED = "connected"            # saved, and it works
REJECTED = "rejected"              # QCH answered and said no - nothing was saved
SAVED_UNCHECKED = "saved_unchecked"  # saved, but QCH could not be reached to try it


@dataclass
class Saved:
    outcome: str
    message: str

    @property
    def ok(self) -> bool:
        return self.outcome == CONNECTED


def save_connection(host: str, user: str, database: str, password: str,
                    port: int | None = None) -> Saved:
    """Save a QCH login, having checked it first.

    This is the whole job behind Settings' Save button and the set-password command, so the
    rule lives in one place rather than in each front end. The order matters: the login is
    tried *before* anything is stored, so a password QCH rejects never reaches the Credential
    Manager. A password that could not be checked - no VPN, server down - is stored anyway,
    because someone has to be able to set up their credentials while off the network.
    """
    from art_speed.qch import connection          # here, not at the top: connection imports us

    if not (str(host).strip() and str(user).strip() and str(database).strip()):
        return Saved(REJECTED, "The QCH host, user and database are all needed.")
    if not password:
        return Saved(REJECTED, "Enter the password.")

    candidate = Credentials(host=str(host).strip(), user=str(user).strip(),
                            password=password, database=str(database).strip(),
                            port=int(port or DEFAULT_PORT), source=CREDENTIAL_MANAGER)

    ok, message, reason = connection.check(candidate)
    if not ok and reason == connection.REFUSED:
        log.warning("QCH refused the credentials for %s; nothing was saved", candidate.safe)
        return Saved(REJECTED,
                     "QCH refused that login. Check the user name and password and try again "
                     "- nothing was saved.")

    settings.set_qch_connection(candidate.host, candidate.user, candidate.database,
                                candidate.port)
    store_password(candidate.user, password)

    if ok:
        log.info("QCH connection saved and checked (%s)", candidate.safe)
        return Saved(CONNECTED, "Saved. The connection to QCH works.")
    log.warning("QCH connection saved but not checked (%s): %s", candidate.safe, message)
    return Saved(SAVED_UNCHECKED,
                 f"Saved, but QCH could not be reached to check it. {message} "
                 "It will be used next time you are on the network.")
