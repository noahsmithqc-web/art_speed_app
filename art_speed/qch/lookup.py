"""What QCH knows about a project number.

Three things, and nothing else at this stage of the wizard:

* **name**  - `order.ProjectName`, where `order.OrderID` is the project number (Q3).
* **client** - `company.CompanyName`, via `order.CompanyID`.
* **state** - `legacycounts.State` for the order's sites. `legacycounts` holds one row per
  SiteNumber per scheduled count date, so the same site appears several times; the rows are
  deduplicated and read lowest SiteNumber first. No rows at all means the state stays blank.
  Rows that disagree mean the lowest SiteNumber wins and the whole list goes to the log - no
  pop-up (Q4). The QCH ingest uses the same rule.

The region is not looked up here: it is derived from the state through the shipped CSV.

Three outcomes, and the wizard branches on them:

    found              an order row exists
    not_found          connected, but no order row (Q5) - an order with no counts is still found
    connection_failed  couldn't connect, couldn't log in, or it ran long
"""

from __future__ import annotations

from dataclasses import dataclass, field

from art_speed.log import get_logger
from art_speed import manifest
from art_speed.qch import connection, regions

log = get_logger(__name__)

FOUND = manifest.FOUND
NOT_FOUND = manifest.NOT_FOUND
CONNECTION_FAILED = "connection_failed"

# `order` is a reserved word in MySQL, hence the backticks.
ORDER_SQL = """
    SELECT o.ProjectName AS project_name, c.CompanyName AS client
    FROM `order` o
    LEFT JOIN company c ON c.CompanyID = o.CompanyID
    WHERE o.OrderID = %s
    LIMIT 1
"""

STATE_SQL = """
    SELECT DISTINCT lc.SiteNumber AS site_number, lc.State AS state
    FROM legacycounts lc
    WHERE lc.QCOrderNo = %s AND lc.State IS NOT NULL AND lc.State <> ''
    ORDER BY lc.SiteNumber
"""


@dataclass
class Result:
    outcome: str
    project_name: str | None = None
    client: str | None = None
    state: str | None = None
    notes: list = field(default_factory=list)
    message: str = ""

    @property
    def nm_region(self) -> str | None:
        return regions.region_for(self.state)

    @property
    def ok(self) -> bool:
        return self.outcome == FOUND


def _text(value) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text or None


def _site_order(row: dict) -> tuple:
    """Sort key for a legacycounts row: lowest SiteNumber first, digits compared as a number."""
    site = "" if row.get("site_number") is None else str(row["site_number"]).strip()
    return (0, int(site), "") if site.isdigit() else (1, 0, site)


def state_for(qch: connection.Connection, project_number: str,
              notes: list | None = None) -> str | None:
    """The project's state, by the rule at the top of this file."""
    notes = notes if notes is not None else []
    rows = qch.rows(STATE_SQL, (project_number,))
    if not rows:
        log.info("no legacycounts rows for %s; state left blank", project_number)
        notes.append("QCH has no count sites for this project yet, so the state is blank.")
        return None

    # The SQL orders by SiteNumber, but the rule is ours, not the database's - so it is applied
    # here too. Numerically when the numbers are all digits: SiteNumbers come in 8 and 9 digit
    # widths, and text ordering puts 9-digit ones in the wrong place.
    rows = sorted(rows, key=_site_order)

    states = []
    for row in rows:
        value = _text(row.get("state"))
        if value and value not in states:
            states.append(value)

    if len(states) > 1:
        pairs = ", ".join(f"{row.get('site_number')}={_text(row.get('state'))}"
                          for row in rows if _text(row.get("state")))
        log.warning("project %s has sites in more than one state (%s); using %s from the "
                    "lowest SiteNumber", project_number, pairs, states[0])
        notes.append(f"QCH lists this project in more than one state ({', '.join(states)}). "
                     f"{states[0]} was used - change it on Summary if that is wrong.")

    try:
        return regions.clean_state(states[0])
    except regions.InvalidState:
        log.warning("project %s has a state QCH gave as %r, which is not two letters; "
                    "left blank", project_number, states[0])
        notes.append(f"QCH gave the state as {states[0]!r}, which is not a two-letter code. "
                     "It was left blank.")
        return None


def lookup(project_number: str, qch: connection.Connection | None = None) -> Result:
    """Look a project number up in QCH. Never raises: the outcome carries the answer."""
    notes: list = []
    try:
        if qch is not None:
            return _lookup_with(qch, project_number, notes)
        with connection.connected() as opened:
            return _lookup_with(opened, project_number, notes)
    except connection.QchUnavailable as exc:
        # One pop-up for every kind of failure; the real cause is already in the log (Q8).
        return Result(CONNECTION_FAILED, notes=notes, message=str(exc))


def _lookup_with(qch: connection.Connection, project_number: str, notes: list) -> Result:
    rows = qch.rows(ORDER_SQL, (project_number,))
    if not rows:
        log.info("project %s has no order row in QCH", project_number)
        return Result(NOT_FOUND, notes=notes,
                      message=f"Project {project_number} not found in QCH")

    row = rows[0]
    name, client = _text(row.get("project_name")), _text(row.get("client"))
    if client is None:
        notes.append("The order has no client on it in QCH.")
    state = state_for(qch, project_number, notes)
    log.info("QCH lookup for %s: name=%s client=%s state=%s",
             project_number, name, client, state)
    return Result(FOUND, project_name=name, client=client, state=state, notes=notes)
