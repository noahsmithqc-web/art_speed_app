"""The near miss grouping hint (plan sections 1 and 5).

When the project folder is an A.R.T. project folder and nm_######.db is there, sitenumbers that
share a near miss location start as one speed location. The near miss database is opened
read-only (SQLite `mode=ro`), read once at import, and the answer is stamped into the speed db;
nothing is ever written to it and it is never opened again for that sitenumber.

Any trouble at all - no file, an older nm database, a locked file - means no hint: each
sitenumber starts as its own location. The hint never stops an import.
"""

from __future__ import annotations

import os
import sqlite3
from pathlib import Path

from art_speed.log import get_logger

log = get_logger(__name__)


def nm_db_path(project_folder: str | os.PathLike, project_number: str) -> Path:
    return Path(project_folder) / f"nm_{project_number}.db"


def read(project_folder: str | os.PathLike, project_number: str) -> dict[int, str]:
    """{sitenumber: near miss location code}, e.g. {17408301: '01,02', 17408302: '01,02'}.

    A near miss sitecode joined to a QC Hub site by hand answers to that QC Hub sitenumber;
    otherwise to project number + sitecode.
    """
    path = nm_db_path(project_folder, project_number)
    if not path.is_file():
        return {}
    try:
        conn = sqlite3.connect(f"{path.resolve().as_uri()}?mode=ro", uri=True, timeout=2.0)
    except sqlite3.Error as exc:
        log.warning("near miss grouping hint not read (%s)", exc)
        return {}
    try:
        rows = conn.execute(
            "SELECT s.nm_sitenumber, s.qch_sitenumber, l.nm_location_code "
            "FROM nm_sitecode s JOIN nm_location l ON l.nm_location_id = s.nm_location_id").fetchall()
    except sqlite3.Error as exc:
        log.warning("near miss grouping hint not read from %s (%s)", path.name, exc)
        return {}
    finally:
        conn.close()
    hint: dict[int, str] = {}
    for nm_sitenumber, qch_sitenumber, code in rows:
        number = qch_sitenumber if qch_sitenumber is not None else nm_sitenumber
        if isinstance(number, int) and code:
            hint[number] = str(code)
    return hint
