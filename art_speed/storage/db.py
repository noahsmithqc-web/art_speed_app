"""speed_######.db - the speed tool's own database (plan section 4, SP-1).

One file per project, beside nm_######.db and qch_######.db when the folder is an A.R.T. project
folder, and in any folder otherwise. Created by the first import. Nothing in it points at the
other two databases and nothing here ever opens them.

The open / create / version pattern is A.R.T.'s near_miss/db.py (see COPIED_FROM_ART.md):
built under a temporary name and renamed, so the real name only ever holds a complete database;
`PRAGMA user_version` is the version stamp; a file from an older version is upgraded on open by
the functions in _MIGRATIONS; a file from a newer version is refused.

Tables (speed db version 1):

    speed_meta          key/value: project number, created by which tool version
    speed_import_log    one row per folder import
    speed_sitenumber    one row per imported sitenumber: file-header metadata, nm grouping hint
    speed_qch_site      the QC Hub snapshot for the sitenumber, or the manual entry
    speed_lane_config   lane configs edited in the drawer (speed db only); absent = QC Hub's
    speed_cam           one row per camera file: corner, CSV form, counts, mask acknowledgement
    speed_cam_mask      time masks: one row per masked date-hour of a cam
    speed_event         the raw rows, every column of the export
    speed_location      one row per report location: name, note, count window, posted-speed links
    speed_slice         the location's slices: sitenumber + optional date + optional hour window
    speed_direction     the decisions per location x direction
    speed_log           the paper trail
    speed_report_log    one row per generated workbook
"""

from __future__ import annotations

import os
import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterator

from art_speed import config, fsguard
from art_speed.log import get_logger

log = get_logger(__name__)

DB_VERSION = 1          # PRAGMA user_version. Raise it only with a migration beside it (_MIGRATIONS).
BUSY_TIMEOUT_S = 5.0    # how long a statement waits if the file is locked by another process

DIRECTIONS = ("NB", "SB", "EB", "WB")
CORNERS = ("NW", "NE", "SW", "SE")
CSV_FORMS = ("raw", "post-macro")
LOOKUPS = ("legacycounts", "orderlocation", "not_found", "manual")   # NULL = never checked
NAME_QCH, NAME_MANUAL = "qch", "manual"
NAME_SOURCES = (NAME_QCH, NAME_MANUAL)
WINDOW_QCH, WINDOW_HEADER, WINDOW_EDITED, WINDOW_SLICE = "QC Hub", "file header", "edited", "slice"
WINDOW_ROWS = "rows"        # no usable duration in QC Hub or the header: first row's hour to the last row's
WINDOW_SOURCES = (WINDOW_QCH, WINDOW_HEADER, WINDOW_EDITED, WINDOW_SLICE, WINDOW_ROWS)
SITENUMBER_FROM = ("file name", "header")
LOG_SCOPES = ("location", "site", "cam", "project")
MASK_LOW_DEFAULT = 5    # "below 5 mph is masked"
MANUAL_LANE_CONFIG = "2000000"      # a site with no QC Hub lane configs: four approaches, one thru lane

TABLES = ("speed_meta", "speed_import_log", "speed_sitenumber", "speed_qch_site", "speed_lane_config",
          "speed_cam", "speed_cam_mask", "speed_event", "speed_location", "speed_slice",
          "speed_direction", "speed_log", "speed_report_log")
_TABLE_SINCE: dict = {}             # table -> the db version that added it (for _check)


def _in(values: tuple) -> str:
    return "(" + ", ".join(f"'{v}'" for v in values) + ")"


_UTC_OK = ("{col} GLOB "
           "'[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]T[0-9][0-9]:[0-9][0-9]:[0-9][0-9]Z'")
_DATE_OK = "date({col}) IS {col}"                    # 'YYYY-MM-DD' and a real calendar date
_TIME_OK = "time({col}) IS {col} AND {col} < '24'"   # 'HH:MM:SS'
_LANES_OK = "{col} NOT GLOB '*[^0-7]*' AND length({col}) <= 7"
_SITENUMBER_OK = ("typeof({col}) = 'integer' AND {col} BETWEEN 10000000 AND 999999999")

_SCHEMA = (
    """
CREATE TABLE speed_meta (
    key    TEXT PRIMARY KEY,
    value  TEXT NOT NULL
)""",
    f"""
CREATE TABLE speed_import_log (
    import_id     INTEGER PRIMARY KEY,
    imported_utc  TEXT    NOT NULL CHECK ({_UTC_OK.format(col="imported_utc")}),
    imported_by   TEXT,                          -- Windows user name, when it can be read
    folder        TEXT    NOT NULL,              -- the folder that was picked
    app_version   TEXT    NOT NULL,              -- tool version that read the files
    sitenumbers   INTEGER NOT NULL CHECK (sitenumbers >= 0),
    files         INTEGER NOT NULL CHECK (files >= 0),
    events        INTEGER NOT NULL CHECK (events >= 0),
    overwritten   INTEGER NOT NULL DEFAULT 0 CHECK (overwritten >= 0)   -- sitenumbers replaced
)""",
    f"""
CREATE TABLE speed_sitenumber (
    sitenumber          INTEGER PRIMARY KEY CHECK ({_SITENUMBER_OK.format(col="sitenumber")}),
    import_id           INTEGER NOT NULL REFERENCES speed_import_log (import_id),
    header_sitecode     TEXT,                    -- the files' own 'Sitecode:' line
    header_start_date   TEXT CHECK (header_start_date IS NULL OR {_DATE_OK.format(col="header_start_date")}),
    header_start_time   TEXT CHECK (header_start_time IS NULL OR {_TIME_OK.format(col="header_start_time")}),
    header_duration_hr  REAL,                    -- NULL or 0 on a post-macro file
    sitenumber_from     TEXT NOT NULL DEFAULT 'file name'       -- SP-Q11: which one the user chose
        CHECK (sitenumber_from IN {_in(SITENUMBER_FROM)}),      -- when name and header disagreed
    nm_location_code    TEXT                     -- grouping hint read once from nm_######.db; NULL = none
)""",
    f"""
CREATE TABLE speed_qch_site (
    sitenumber       INTEGER PRIMARY KEY
        REFERENCES speed_sitenumber (sitenumber) ON DELETE CASCADE,
    lookup           TEXT CHECK (lookup IS NULL OR lookup IN {_in(LOOKUPS)}),   -- NULL = not checked
    street_ns        TEXT,
    street_ew        TEXT,
    location_name    TEXT,                       -- the one name of a tube/survey or manual site
    city             TEXT,
    state            TEXT,
    nb_lane_config   TEXT NOT NULL DEFAULT '' CHECK ({_LANES_OK.format(col="nb_lane_config")}),
    sb_lane_config   TEXT NOT NULL DEFAULT '' CHECK ({_LANES_OK.format(col="sb_lane_config")}),
    eb_lane_config   TEXT NOT NULL DEFAULT '' CHECK ({_LANES_OK.format(col="eb_lane_config")}),
    wb_lane_config   TEXT NOT NULL DEFAULT '' CHECK ({_LANES_OK.format(col="wb_lane_config")}),
    control_type     TEXT,
    stop_controlled  TEXT,
    latitude         REAL,
    longitude        REAL,
    count_date       TEXT CHECK (count_date IS NULL OR {_DATE_OK.format(col="count_date")}),
    start_time       TEXT CHECK (start_time IS NULL OR {_TIME_OK.format(col="start_time")}),
    duration_hr      REAL CHECK (duration_hr IS NULL OR duration_hr > 0),
    checked_utc      TEXT CHECK (checked_utc IS NULL OR {_UTC_OK.format(col="checked_utc")}),
    notes            TEXT NOT NULL DEFAULT '[]'  -- JSON list of sentences from the lookup
)""",
    f"""
CREATE TABLE speed_lane_config (                 -- SP-Q7: edits made in the speed drawer
    sitenumber   INTEGER NOT NULL REFERENCES speed_sitenumber (sitenumber) ON DELETE CASCADE,
    direction    TEXT    NOT NULL CHECK (direction IN {_in(DIRECTIONS)}),
    lane_config  TEXT    NOT NULL CHECK ({_LANES_OK.format(col="lane_config")}),
    edited_utc   TEXT    NOT NULL CHECK ({_UTC_OK.format(col="edited_utc")}),
    PRIMARY KEY (sitenumber, direction)
)""",
    f"""
CREATE TABLE speed_cam (
    sitenumber       INTEGER NOT NULL REFERENCES speed_sitenumber (sitenumber) ON DELETE CASCADE,
    cam              INTEGER NOT NULL CHECK (cam >= 1),
    file_name        TEXT    NOT NULL,
    file_hash        TEXT    NOT NULL,           -- SHA-256 of the file
    csv_form         TEXT    NOT NULL CHECK (csv_form IN {_in(CSV_FORMS)}),
    events           INTEGER NOT NULL CHECK (events >= 0),
    zero_events      INTEGER NOT NULL CHECK (zero_events >= 0),
    duplicate_lines  INTEGER NOT NULL DEFAULT 0 CHECK (duplicate_lines >= 0),   -- kept, counted
    corner           TEXT CHECK (corner IS NULL OR corner IN {_in(CORNERS)}),
    mask_ack_utc     TEXT CHECK (mask_ack_utc IS NULL OR {_UTC_OK.format(col="mask_ack_utc")}),
    PRIMARY KEY (sitenumber, cam)
)""",
    f"""
CREATE TABLE speed_cam_mask (                    -- 7b: a masked date-hour belongs to the camera
    sitenumber  INTEGER NOT NULL,
    cam         INTEGER NOT NULL,
    date        TEXT    NOT NULL CHECK ({_DATE_OK.format(col="date")}),
    hour        INTEGER NOT NULL CHECK (hour BETWEEN 0 AND 23),
    PRIMARY KEY (sitenumber, cam, date, hour),
    FOREIGN KEY (sitenumber, cam) REFERENCES speed_cam (sitenumber, cam) ON DELETE CASCADE
)""",
    f"""
CREATE TABLE speed_event (                       -- SP-Q4: raw rows, the finest grain, all columns
    event_id    INTEGER PRIMARY KEY,
    sitenumber  INTEGER NOT NULL,
    cam         INTEGER NOT NULL,
    line_no     INTEGER NOT NULL,                -- line in the file, 1-based
    date        TEXT    NOT NULL CHECK ({_DATE_OK.format(col="date")}),
    time        TEXT    NOT NULL CHECK ({_TIME_OK.format(col="time")}),
    direction   TEXT    NOT NULL CHECK (direction IN {_in(DIRECTIONS)}),
    movement    TEXT    NOT NULL,                -- always 'Thru' (the parser refuses anything else)
    class       TEXT,
    speed_mph   INTEGER NOT NULL CHECK (speed_mph >= 0),
    track_id    TEXT,
    extra       TEXT,                            -- JSON of any further columns the export carries
    FOREIGN KEY (sitenumber, cam) REFERENCES speed_cam (sitenumber, cam) ON DELETE CASCADE
)""",
    "CREATE INDEX speed_event_by_cam ON speed_event (sitenumber, cam, direction, date, time)",
    f"""
CREATE TABLE speed_location (
    location_id         INTEGER PRIMARY KEY AUTOINCREMENT,   -- never reused: a deleted location's id stays dead
    name_source         TEXT    NOT NULL DEFAULT '{NAME_QCH}' CHECK (name_source IN {_in(NAME_SOURCES)}),
    manual_name         TEXT,
    note                TEXT    NOT NULL DEFAULT '',
    nm_grouping_used    INTEGER NOT NULL DEFAULT 0 CHECK (nm_grouping_used IN (0, 1)),
    corners_linked      INTEGER NOT NULL DEFAULT 1 CHECK (corners_linked IN (0, 1)),
    window_start_date   TEXT CHECK (window_start_date IS NULL OR {_DATE_OK.format(col="window_start_date")}),
    window_start_time   TEXT CHECK (window_start_time IS NULL OR {_TIME_OK.format(col="window_start_time")}),
    window_duration_hr  REAL CHECK (window_duration_hr IS NULL OR window_duration_hr > 0),
    window_source       TEXT    NOT NULL DEFAULT '{WINDOW_HEADER}' CHECK (window_source IN {_in(WINDOW_SOURCES)}),
    posted_same_ns      INTEGER NOT NULL DEFAULT 1 CHECK (posted_same_ns IN (0, 1)),   -- SP-Q6
    posted_same_ew      INTEGER NOT NULL DEFAULT 1 CHECK (posted_same_ew IN (0, 1)),
    created_utc         TEXT    NOT NULL CHECK ({_UTC_OK.format(col="created_utc")}),
    updated_utc         TEXT    NOT NULL CHECK ({_UTC_OK.format(col="updated_utc")})
)""",
    """
CREATE TABLE speed_slice (                       -- SP-Q10 / Q21: sitenumber + optional date + optional hours
    slice_id     INTEGER PRIMARY KEY,
    location_id  INTEGER NOT NULL REFERENCES speed_location (location_id) ON DELETE CASCADE,
    position     INTEGER NOT NULL CHECK (position >= 1),
    sitenumber   INTEGER NOT NULL REFERENCES speed_sitenumber (sitenumber) ON DELETE CASCADE,
    date         TEXT CHECK (date IS NULL OR date(date) IS date),
    from_hour    INTEGER,
    to_hour      INTEGER,                        -- exclusive: 6 to 10 is 06:00-10:00
    CHECK ((from_hour IS NULL) = (to_hour IS NULL)),
    CHECK (from_hour IS NULL OR (from_hour >= 0 AND from_hour < to_hour AND to_hour <= 24)),
    UNIQUE (location_id, position)
)""",
    f"""
CREATE TABLE speed_direction (                   -- the decisions, per location x direction
    location_id        INTEGER NOT NULL REFERENCES speed_location (location_id) ON DELETE CASCADE,
    direction          TEXT    NOT NULL CHECK (direction IN {_in(DIRECTIONS)}),
    include_in_report  INTEGER NOT NULL CHECK (include_in_report IN (0, 1)),
    posted_mph         REAL    CHECK (posted_mph IS NULL OR posted_mph > 0),
    source_sitenumber  INTEGER,
    source_cam         INTEGER,
    adjust_by          INTEGER NOT NULL DEFAULT 0,
    mask_low           INTEGER DEFAULT {MASK_LOW_DEFAULT} CHECK (mask_low IS NULL OR mask_low >= 1),   -- NULL = off
    mask_high          INTEGER CHECK (mask_high IS NULL OR mask_high >= 1),
    PRIMARY KEY (location_id, direction),
    CHECK ((source_sitenumber IS NULL) = (source_cam IS NULL)),
    CHECK (mask_low IS NULL OR mask_high IS NULL OR mask_low < mask_high),
    FOREIGN KEY (source_sitenumber, source_cam) REFERENCES speed_cam (sitenumber, cam) ON DELETE SET NULL
)""",
    f"""
CREATE TABLE speed_log (                         -- the paper trail (SP-Q15)
    log_id       INTEGER PRIMARY KEY,
    logged_utc   TEXT    NOT NULL CHECK ({_UTC_OK.format(col="logged_utc")}),
    who          TEXT    NOT NULL,
    scope        TEXT    NOT NULL CHECK (scope IN {_in(LOG_SCOPES)}),
    location_id  INTEGER,                        -- scope 'location'
    sitenumber   INTEGER,                        -- scope 'site' and 'cam'
    cam          INTEGER,                        -- scope 'cam'
    what         TEXT    NOT NULL
)""",
    "CREATE INDEX speed_log_by_location ON speed_log (location_id)",
    "CREATE INDEX speed_log_by_site ON speed_log (sitenumber)",
    f"""
CREATE TABLE speed_report_log (
    report_id      INTEGER PRIMARY KEY,
    generated_utc  TEXT    NOT NULL CHECK ({_UTC_OK.format(col="generated_utc")}),
    who            TEXT    NOT NULL,
    folder         TEXT    NOT NULL,             -- reports_yyyy_mm_dd__hhmm
    file_name      TEXT    NOT NULL,
    location_id    INTEGER NOT NULL,
    app_version    TEXT    NOT NULL
)""",
)

# version -> the function that takes a database from that version to the next. Empty at
# version 1; the hook is here from the start so nobody re-imports after an update (SP-Q1).
_MIGRATIONS: dict = {}


# ------------------------------------------------------------------ errors

class SpeedDbError(Exception):
    """The speed database can't be used. The message is written for the user."""


class NoSpeedData(SpeedDbError):
    """The project has no speed_######.db yet: nothing has been imported."""

    def __init__(self, path: Path):
        self.path = path
        super().__init__(f"No speed data has been imported yet ({path.name} does not exist).")


class DatabaseExists(SpeedDbError):
    def __init__(self, path: Path):
        self.path = path
        super().__init__(f"{path.name} already exists.")


# ------------------------------------------------------------------ names and time

def now_utc() -> str:
    """The tool's timestamp format: ISO 8601 UTC with a Z, as A.R.T. writes it."""
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def is_valid_project_number(value) -> bool:
    return isinstance(value, str) and len(value) == 6 and value.isascii() and value.isdigit()


def file_name(project_number: str) -> str:
    return f"speed_{project_number}.db"


def file_in(project_folder: str | os.PathLike, project_number: str) -> Path:
    if not is_valid_project_number(project_number):
        raise SpeedDbError(f"Project number {project_number!r} is not six digits.")
    return Path(project_folder) / file_name(project_number)


def _creating(path: Path) -> Path:
    """The temporary name the database is built under."""
    return path.with_name(path.name + ".creating")


def exists(project_folder: str | os.PathLike, project_number: str) -> bool:
    path = file_in(project_folder, project_number)
    try:
        return fsguard.exists(path)
    except fsguard.FsTimeout as exc:
        raise SpeedDbError(f"{path.parent} did not respond. If it is a network drive, check that "
                           "you are connected.") from exc


# ------------------------------------------------------------------ connections

def _connect(path: Path) -> sqlite3.Connection:
    # isolation_level=None: nothing starts a transaction behind our back; transaction() does
    # it explicitly. check_same_thread=False: the connection is opened on fsguard's worker
    # thread and then used on the caller's.
    conn = sqlite3.connect(str(path), timeout=BUSY_TIMEOUT_S, isolation_level=None,
                           check_same_thread=False)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")     # off by default, and per connection
    return conn


def user_version(conn: sqlite3.Connection) -> int:
    return conn.execute("PRAGMA user_version").fetchone()[0]


def _check(conn: sqlite3.Connection, path: Path) -> None:
    """Is this a speed database this tool can use? Raises SpeedDbError. Upgrades an older one."""
    try:
        version = user_version(conn)
        found = {row[0] for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'")}
    except sqlite3.DatabaseError as exc:        # "file is not a database"
        raise SpeedDbError(f"{path.name} is not a database the speed tool can read ({exc}).") from exc
    if version > DB_VERSION:
        raise SpeedDbError(f"{path.name} was written by a newer version of the speed tool "
                           f"(database version {version}; this version reads up to {DB_VERSION}). "
                           "Update the speed tool to open it.")
    if version < 1:
        raise SpeedDbError(f"{path.name} is not a speed tool database.")
    missing = [t for t in TABLES if t not in found and version >= _TABLE_SINCE.get(t, 1)]
    if missing:                                 # a table a later version adds is migrated in
        raise SpeedDbError(f"{path.name} is damaged: missing {', '.join(missing)}.")
    if version < DB_VERSION:
        _migrate(conn, version, path)


def _migrate(conn: sqlite3.Connection, version: int, path: Path) -> None:
    """Upgrade on open: run each step from `version` up to DB_VERSION, all or nothing."""
    with transaction(conn):
        while version < DB_VERSION:
            step = _MIGRATIONS.get(version)
            if step is None:                    # pragma: no cover - a version bump without its step
                raise SpeedDbError(f"{path.name} is database version {version} and this tool has "
                                   f"no upgrade from it.")
            step(conn)
            version += 1
        conn.execute(f"PRAGMA user_version = {DB_VERSION}")
    log.info("upgraded %s to database version %d", path, DB_VERSION)


def _remove(path: Path) -> None:
    try:
        path.unlink()
    except FileNotFoundError:
        pass


def create(project_folder: str | os.PathLike, project_number: str) -> Path:
    """Create speed_######.db: every table, the meta rows, user_version. Never replaces a file."""
    path = file_in(project_folder, project_number)
    tmp = _creating(path)
    if len(str(tmp)) > config.MAX_PATH_CHARS:
        raise SpeedDbError(f"The full path is {len(str(tmp))} characters; the limit is "
                           f"{config.MAX_PATH_CHARS}. Choose a folder with a shorter path.")

    def build() -> None:
        if os.path.exists(path):
            raise DatabaseExists(path)
        _remove(tmp)
        try:
            conn = sqlite3.connect(str(tmp), isolation_level=None)
            try:
                conn.execute("PRAGMA foreign_keys = ON")
                conn.execute("BEGIN")
                for statement in _SCHEMA:
                    conn.execute(statement)
                conn.executemany("INSERT INTO speed_meta (key, value) VALUES (?, ?)", [
                    ("project_number", project_number), ("created_utc", now_utc()),
                    ("created_app_version", config.VERSION)])
                conn.execute(f"PRAGMA user_version = {DB_VERSION}")
                conn.execute("COMMIT")
            finally:
                conn.close()
            os.rename(tmp, path)
        except BaseException:
            _remove(tmp)
            raise

    try:
        fsguard.guarded(build, what=f"creating {path}")
    except DatabaseExists:
        raise
    except fsguard.FsTimeout as exc:
        raise SpeedDbError(f"{path.parent} did not respond while creating {path.name}. If it is "
                           "a network drive, check that you are connected.") from exc
    except (OSError, sqlite3.Error) as exc:
        log.error("creating %s failed: %s", path, exc)
        raise SpeedDbError(f"{path.name} could not be created ({exc}). Nothing was left "
                           "behind.") from exc
    log.info("created %s (database version %d)", path, DB_VERSION)
    return path


def open_db(project_folder: str | os.PathLike, project_number: str) -> sqlite3.Connection:
    """Open an existing speed_######.db and check it. The caller closes the connection.

    Raises NoSpeedData when there is no file (never creates one), SpeedDbError otherwise.
    """
    path = file_in(project_folder, project_number)

    def connect() -> sqlite3.Connection:
        if not os.path.exists(path):
            raise NoSpeedData(path)
        conn = _connect(path)
        try:
            _check(conn, path)
        except BaseException:
            conn.close()
            raise
        return conn

    try:
        return fsguard.guarded(connect, what=f"opening {path}")
    except fsguard.FsTimeout as exc:
        raise SpeedDbError(f"{path.parent} did not respond while opening {path.name}. If it is a "
                           "network drive, check that you are connected.") from exc
    except sqlite3.Error as exc:
        raise SpeedDbError(f"{path.name} could not be opened ({exc}).") from exc


def open_or_create(project_folder: str | os.PathLike,
                   project_number: str) -> tuple[sqlite3.Connection, bool]:
    """What an import calls: the database, and whether this call created it."""
    try:
        return open_db(project_folder, project_number), False
    except NoSpeedData:
        create(project_folder, project_number)
        return open_db(project_folder, project_number), True


@contextmanager
def transaction(conn: sqlite3.Connection) -> Iterator[sqlite3.Connection]:
    """Everything inside lands together or not at all.

    BEGIN IMMEDIATE takes the write lock up front, so a second writer is refused at the start
    rather than half way through.
    """
    conn.execute("BEGIN IMMEDIATE")
    try:
        yield conn
    except BaseException:
        conn.execute("ROLLBACK")
        raise
    conn.execute("COMMIT")
