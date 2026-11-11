"""Small builders the storage, engine and API tests share."""

from __future__ import annotations

from art_speed.qch import site as qch_site
from art_speed.storage import db, store


def open_db(tmp_path, number: str = "174083"):
    conn, _ = db.open_or_create(tmp_path, number)
    return conn


def event_rows(direction: str, speeds, *, date: str = "2026-02-24", hour: int = 6, start_line: int = 7):
    """(line_no, date, time, direction, movement, class, speed, track_id, extra) rows, one a second."""
    return [(start_line + i, date, f"{hour:02d}:{i // 60 % 60:02d}:{i % 60:02d}", direction, "Thru",
             "cl2_passenger_car", int(s), str(i + 1), None) for i, s in enumerate(speeds)]


def add_site(conn, sitenumber: int, cams: dict, *, site: qch_site.Site | None = None,
             header: dict | None = None, nm_location_code: str | None = None,
             make_location: bool = True) -> int | None:
    """Store a sitenumber with its cams ({cam: [event rows]}) and, by default, its own location.
    Returns the location id."""
    with db.transaction(conn):
        rows_total = sum(len(r) for r in cams.values())
        import_id = store.insert_import(conn, "test", sitenumbers=1, files=len(cams), events=rows_total)
        hdr = {"header_sitecode": str(sitenumber), "header_start_date": "2026-02-24",
               "header_start_time": "06:00:00", "header_duration_hr": 13.0}
        hdr.update(header or {})
        store.insert_sitenumber(conn, import_id, sitenumber, nm_location_code=nm_location_code, **hdr)
        store.save_qch_site(conn, site or qch_site.Site(sitenumber, None))
        for cam, rows in cams.items():
            store.insert_cam(conn, sitenumber, cam, file_name=f"{sitenumber}_cam-{cam}_speed.csv",
                             file_hash="0" * 64, csv_form="raw", events=len(rows),
                             zero_events=sum(1 for r in rows if r[6] == 0))
            store.insert_events(conn, sitenumber, cam, rows)
        return store.create_location(conn, [{"sitenumber": sitenumber}]) if make_location else None


def benning() -> qch_site.Site:
    """A QC Hub turn movement site: NB and SB lanes, no EB, one WB lane."""
    return qch_site.Site(
        17408304, qch_site.LEGACYCOUNTS, street_ns="Benning Rd SE", street_ew="A St SE",
        city="Washington", state="DC",
        lane_configs={"NB": "6200000", "SB": "2300000", "EB": "0000000", "WB": "7000000"},
        control_type="TWSC", stop_controlled="EW", latitude=38.8884457, longitude=-76.9369868,
        count_date="2026-02-24", start_time="06:00:00", duration_hr=13.0)


# ------------------------------------------------------------------ Datalens speed CSVs on disk

def csv_text(sitenumber, rows, *, start_date="2026-02-24", start_time="06:00 AM", duration="13.00",
             header_sitecode=None) -> str:
    """A raw-form export. rows: (date, 'hh:mm:ss AM', 'NB Thru', class, speed, track id)."""
    lines = [f"Sitecode:,{header_sitecode if header_sitecode is not None else sitenumber}",
             f"Start date:,{start_date}", f"Start time:,{start_time}", f"Duration (hrs):,{duration}", "",
             "Date,Time,Direction,Class,Speed (In MPH),Track ID"]
    lines += [",".join(str(v) for v in r) for r in rows]
    return "\n".join(lines) + "\n"


def post_macro_text(sitenumber, cam, rows, *, start_date="10/6/2026", start_time="12:00:00 AM",
                    duration="0") -> str:
    """The same file after the QA macro and an Excel save: padded to seven columns, US dates,
    times without a leading zero, the cam number in column G."""
    lines = [f"Sitecode:,{sitenumber},,,,,", f"Start date:,{start_date},,,,,",
             f"Start time:,{start_time},,,,,", f"Duration (hrs):,{duration},,,,,", ",,,,,,",
             "Date,Time,Direction,Class,Speed (In MPH),Track ID,"]
    lines += [",".join(str(v) for v in r) + f",{cam}" for r in rows]
    return "\r\n".join(lines) + "\r\n"


def speed_rows(direction, speeds, *, date="2026-02-24", hour=6, first_track=1):
    """Raw-form rows, one a second from hh:00:00."""
    h12, ampm = (hour % 12 or 12), ("AM" if hour < 12 else "PM")
    return [(date, f"{h12:02d}:{i // 60:02d}:{i % 60:02d} {ampm}", f"{direction} Thru",
             "cl2_passenger_car", s, first_track + i) for i, s in enumerate(speeds)]


def write_csv(folder, sitenumber, cam, rows, **header):
    path = folder / f"{sitenumber}_cam-{cam}_speed.csv"
    path.write_text(csv_text(sitenumber, rows, **header), encoding="utf-8", newline="")
    return path
