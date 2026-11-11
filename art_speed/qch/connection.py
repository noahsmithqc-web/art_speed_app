"""Talking to QCH, with time limits.

QCH lives behind the VPN, so the interesting failure is not an error - it is a wait. A laptop
off the network will sit on a TCP connect for a minute or more before anything comes back, and
the import would look frozen. So: 5 seconds to connect, 15 seconds for any one query, and past
either of those the caller gets a failure it can show. A connect that gets no answer is tried
once more first (Q80), so the worst case before the pop-up is about 12 seconds.

Every failure that reaches the screen is the same pop-up (Q8). The real cause - refused login,
unknown host, a query that ran long - goes to the log. Passwords never do: the log's redacting
formatter would catch a stray one, but nothing here puts one in a message to begin with.
"""

from __future__ import annotations

from contextlib import contextmanager
from typing import Any

from art_speed import config, fsguard
from art_speed.log import get_logger
from art_speed.qch import credentials

log = get_logger(__name__)


# Why a connection failed. The user never sees these - every failure shows the same sentence
# (Q8) - but the app needs to tell "the login is wrong" from "the server is not reachable":
# one means don't save the password, the other means save it and say it wasn't checked.
REFUSED = "refused"            # the server answered and rejected the login or the database
UNREACHABLE = "unreachable"    # no answer: DNS, VPN, firewall, timeout
NO_CREDENTIALS = "no_credentials"
NO_DRIVER = "no_driver"
QUERY_FAILED = "query_failed"

# MySQL errno values that mean "the server answered, and said no".
_REFUSAL_ERRNOS = frozenset({
    1044,   # access denied for user to database
    1045,   # access denied for user (the wrong-password case)
    1049,   # unknown database
    1698,   # access denied (auth plugin)
})


class QchUnavailable(Exception):
    """Could not connect, could not log in, or a query did not finish in time.

    `reason` is for the app, not the user: the message is the same either way.
    """

    def __init__(self, message: str, reason: str = UNREACHABLE):
        self.reason = reason
        super().__init__(message)


def _reason_for(exc: Exception) -> str:
    """Did the server answer and refuse, or did we never reach it?"""
    errno = getattr(exc, "errno", None)
    return REFUSED if errno in _REFUSAL_ERRNOS else UNREACHABLE


def driver():
    """The `mysql.connector` module itself - the object whose .connect() we call.

    Same driver as the existing pull scripts. None when it isn't installed. Note this returns
    the submodule, not the `mysql` package: `import mysql.connector` binds `mysql`, and it is
    `mysql.connector` that carries connect().
    """
    try:
        import mysql.connector
    except ImportError:                 # pragma: no cover - installed from requirements.txt
        log.error("mysql-connector-python is not installed")
        return None
    return mysql.connector


class Connection:
    """One open connection. `rows` is the only way anything asks QCH a question."""

    def __init__(self, raw: Any, creds: credentials.Credentials):
        self._raw = raw
        self.creds = creds

    def rows(self, sql: str, params: tuple = (), timeout: float | None = None) -> list:
        """Run one SELECT and return a list of dicts. Raises QchUnavailable if it runs long."""
        limit = config.QCH_QUERY_TIMEOUT_S if timeout is None else timeout

        def run() -> list:
            cursor = self._raw.cursor(dictionary=True)
            try:
                cursor.execute(sql, params)
                return list(cursor.fetchall())
            finally:
                cursor.close()

        try:
            # The thread can't be cancelled, so a query that overruns is left to finish on its
            # own while the caller is told. The connection is closed on the way out regardless.
            return fsguard.guarded(run, timeout=limit, what="a QCH query")
        except fsguard.FsTimeout as exc:
            log.error("QCH query did not finish within %g s on %s", limit, self.creds.safe)
            raise QchUnavailable(
                f"QCH did not answer within {limit:g} seconds.", UNREACHABLE) from exc
        except Exception as exc:
            log.error("QCH query failed on %s: %s: %s", self.creds.safe, type(exc).__name__, exc)
            raise QchUnavailable("The QCH query failed.", QUERY_FAILED) from exc

    def close(self) -> None:
        try:
            self._raw.close()
        except Exception as exc:                      # a dead socket is not worth failing over
            log.debug("closing the QCH connection raised %s", type(exc).__name__)


def open_connection(creds: credentials.Credentials | None = None) -> Connection:
    """Connect to QCH. Raises QchUnavailable, with the real reason in the log."""
    if creds is None:
        try:
            creds = credentials.load()
        except credentials.CredentialsMissing as exc:
            # No credentials at all is treated as a connection failure, with a hint (plan §4).
            log.error("no QCH credentials: %s", exc)
            raise QchUnavailable(str(exc), NO_CREDENTIALS) from exc

    connector = driver()
    if connector is None:
        raise QchUnavailable("This copy of the app cannot reach QCH: its database driver is "
                             "missing. Reinstall the speed tool.", NO_DRIVER)

    def connect() -> Any:
        return connector.connect(
            host=creds.host, port=creds.port, user=creds.user,
            password=creds.password, database=creds.database,
            connection_timeout=config.QCH_CONNECT_TIMEOUT_S)

    # Q80: QCH (AWS) or the VPN now and then drops a connect attempt - no answer at all, where a
    # normal connect takes under a second - and the next attempt goes straight through. So an
    # attempt that gets no answer is tried once more before the pop-up. A refusal (the server
    # answered: wrong password, unknown database) is never retried.
    attempts = max(1, config.QCH_CONNECT_ATTEMPTS)
    for attempt in range(1, attempts + 1):
        last = attempt == attempts
        try:
            raw = fsguard.guarded(connect, timeout=config.QCH_CONNECT_TIMEOUT_S + 1,
                                  what="connecting to QCH")
            break
        except fsguard.FsTimeout as exc:
            if not last:
                log.warning("QCH did not answer within %g s (%s); trying again (attempt %d "
                            "of %d)", config.QCH_CONNECT_TIMEOUT_S, creds.safe, attempt + 1,
                            attempts)
                continue
            log.error("QCH did not answer within %g s (%s)",
                      config.QCH_CONNECT_TIMEOUT_S, creds.safe)
            raise QchUnavailable(
                f"QCH did not answer within {config.QCH_CONNECT_TIMEOUT_S:g} seconds. "
                "Check that you are on the VPN or the office network.", UNREACHABLE) from exc
        except Exception as exc:
            reason = _reason_for(exc)
            if reason == UNREACHABLE and not last:
                log.warning("could not connect to QCH (%s): %s: %s; trying again (attempt %d "
                            "of %d)", creds.safe, type(exc).__name__, exc, attempt + 1,
                            attempts)
                continue
            # Never the exception's own text on screen: a driver error can echo the connection
            # string back. The log gets the type and the message; the user gets one sentence.
            log.error("could not connect to QCH (%s): %s: %s",
                      creds.safe, type(exc).__name__, exc)
            raise QchUnavailable("Could not connect to QCH.", reason) from exc

    log.info("connected to QCH (%s, from the %s)", creds.safe, creds.source)
    return Connection(raw, creds)


@contextmanager
def connected(creds: credentials.Credentials | None = None):
    """`with connected() as qch:` - always closed, even when a query fails."""
    conn = open_connection(creds)
    try:
        yield conn
    finally:
        conn.close()


def check(creds: credentials.Credentials | None = None) -> tuple:
    """(ok, message, reason) for qch-test, and for checking credentials before saving them."""
    try:
        with connected(creds) as qch:
            qch.rows("SELECT 1 AS ok")
    except QchUnavailable as exc:
        return False, str(exc), exc.reason
    return True, "connected", None
