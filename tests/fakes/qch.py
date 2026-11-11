"""A stand-in QC Hub. Adapted from A.R.T.'s tests/fakes/qch.py (FakeQchDb).

The QC Hub tables the site lookup reads, in an in-memory SQLite file, with the lookup's own SQL
run against them (%s turned into ?). A query that is wrong here is wrong in the code, not in the
fake. Values come back in the types mysql-connector gives (datetime, Decimal).
"""

from __future__ import annotations

import sqlite3
from datetime import datetime
from decimal import Decimal

from art_speed.qch import connection, credentials

TABLES = {
    "legacycounts": ("QCOrderNo", "SiteNumber", "StreetNameNorthSouth", "StreetNameEastWest",
                     "City", "State", "StartTime", "EndTime", "DateScheduledForCount",
                     "NBLaneConfig", "SBLaneConfig", "EBLaneConfig", "WBLaneConfig",
                     "ControlType", "StopControlled"),
    "orderlocationtime": ("OrderLocationTimeID", "SiteNumber", "OrderLocationID"),
    "orderlocation": ("OrderLocationID", "City", "StateCode", "Latitude", "Longitude"),
    "orderlocationstreet": ("OrderLocationStreetID", "OrderLocationID", "StreetDirectionCode",
                            "StreetName", "ClockwiseStreetOrder"),
}
INTEGERS = {"QCOrderNo", "SiteNumber", "OrderLocationTimeID", "OrderLocationID",
            "OrderLocationStreetID", "ClockwiseStreetOrder"}
DATETIMES = {"StartTime", "EndTime", "DateScheduledForCount"}
DECIMALS = {"Latitude", "Longitude"}


def fake_credentials(**kwargs) -> credentials.Credentials:
    values = {"host": "qch.test", "user": "reader", "password": "never-logged",
              "database": "qcdb", "port": 3306, "source": credentials.DEV_FILE}
    values.update(kwargs)
    return credentials.Credentials(**values)


class FakeQch(connection.Connection):
    """Drop-in for connection.Connection over an in-memory copy of the QC Hub tables."""

    def __init__(self, *sites: dict, fail_on: str | None = None,
                 fail_reason: str = connection.UNREACHABLE):
        super().__init__(raw=None, creds=fake_credentials())
        self.db = sqlite3.connect(":memory:", check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        for table, columns in TABLES.items():
            self.db.execute(f"CREATE TABLE {table} ({', '.join(columns)})")
        self.fail_on = fail_on              # a piece of SQL: any query containing it fails
        self.fail_reason = fail_reason
        self.asked: list = []
        self.closed = False
        seen: set = set()
        for site in sites:
            for table, rows in site.items():
                if table == "orderlocation":
                    rows = [r for r in rows if r["OrderLocationID"] not in seen]
                    seen.update(r["OrderLocationID"] for r in rows)
                self.add(table, rows)

    def add(self, table: str, rows: list[dict]) -> None:
        columns = TABLES[table]
        self.db.executemany(
            f"INSERT INTO {table} ({', '.join(columns)}) VALUES ({', '.join('?' * len(columns))})",
            [tuple(_stored(c, r.get(c)) for c in columns) for r in rows])

    def rows(self, sql: str, params: tuple = (), timeout: float | None = None) -> list:
        self.asked.append((" ".join(sql.split()), params, timeout))
        if self.fail_on and self.fail_on in sql:
            raise connection.QchUnavailable("Could not connect to QCH.", self.fail_reason)
        found = self.db.execute(sql.replace("%s", "?"), params).fetchall()
        return [{k: _typed(k, row[k]) for k in row.keys()} for row in found]

    def close(self) -> None:
        self.closed = True


def _stored(column: str, value):
    if value == "" or value is None:
        return None
    return int(value) if column in INTEGERS else value


def _typed(column: str, value):
    if value is None:
        return None
    if column in DATETIMES and isinstance(value, str):
        try:
            return datetime.strptime(value[:19], "%Y-%m-%d %H:%M:%S")
        except ValueError:
            return value
    if column in DECIMALS:
        return Decimal(str(value))
    return value


def tm_site(sitenumber: int, *, dates=("2026-02-24",), start: str = "06:00:00",
            end: str = "19:00:00", location: tuple | None = (542625, "38.8884457", "-76.9369868"),
            **legacycounts) -> dict:
    """A turn movement sitenumber: one legacycounts row per scheduled date, plus its location."""
    rows = []
    for day in dates:
        lc = {"QCOrderNo": int(str(sitenumber)[:6]), "SiteNumber": sitenumber,
              "StreetNameNorthSouth": "Benning Rd SE ", "StreetNameEastWest": "A St SE",
              "City": "Washington", "State": "DC",
              "StartTime": f"1900-01-01 {start}", "EndTime": f"1900-01-01 {end}",
              "DateScheduledForCount": f"{day} 00:00:00",
              "NBLaneConfig": "6200000", "SBLaneConfig": "2300000", "EBLaneConfig": "0000000",
              "WBLaneConfig": "7000000", "ControlType": "TWSC", "StopControlled": "EW"}
        lc.update(legacycounts)
        rows.append(lc)
    out = {"legacycounts": rows, "orderlocationtime": [], "orderlocation": [], "orderlocationstreet": []}
    if location is not None:
        loc_id, lat, lon = location
        out["orderlocationtime"].append({"OrderLocationTimeID": sitenumber, "SiteNumber": sitenumber,
                                         "OrderLocationID": loc_id})
        out["orderlocation"].append({"OrderLocationID": loc_id, "City": "Washington",
                                     "StateCode": "DC", "Latitude": lat, "Longitude": lon})
    return out


def tube_site(sitenumber: int, *, streets=("SR 85 N of Camp St",), location_id: int = 542700,
              codes=("MAIN",), lat: str | None = "33.4488", lon: str | None = "-84.4549") -> dict:
    """A tube/survey sitenumber: orderlocation tables only, no legacycounts row."""
    return {
        "legacycounts": [],
        "orderlocationtime": [{"OrderLocationTimeID": sitenumber, "SiteNumber": sitenumber,
                               "OrderLocationID": location_id}],
        "orderlocation": [{"OrderLocationID": location_id, "City": "Fayetteville", "StateCode": "GA",
                           "Latitude": lat, "Longitude": lon}],
        "orderlocationstreet": [{"OrderLocationStreetID": location_id * 10 + i,
                                 "OrderLocationID": location_id,
                                 "StreetDirectionCode": codes[i] if i < len(codes) else None,
                                 "StreetName": name, "ClockwiseStreetOrder": i}
                                for i, name in enumerate(streets)],
    }
