"""Command line: `.venv/Scripts/python.exe -m art_speed <command>`."""

from __future__ import annotations

import argparse
import sys

from art_speed import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="art_speed", description="Intersection speed tool")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("version", help="print the tool version")
    for name, text in (("import", "import every <sitenumber>_cam-<N>_speed.csv in a folder"),
                       ("scan", "read a folder of speed CSVs and report, writing nothing")):
        i = sub.add_parser(name, help=text)
        i.add_argument("csv_folder", help="folder holding the Datalens speed CSVs")
        i.add_argument("--project", required=name == "import", metavar="FOLDER",
                       help="project folder (an A.R.T. project folder, or any folder with --number)")
        i.add_argument("--number", help="six-digit project number, when the folder is not an A.R.T. project")
        i.add_argument("--use", choices=("name", "header"),
                       help="which sitenumber to use for every file whose name and header disagree")
        i.add_argument("--no-qch", action="store_true", help="skip the QC Hub lookup")
    q = sub.add_parser("qch-site", help="show what QC Hub knows about one or more sitenumbers")
    q.add_argument("sitenumbers", nargs="+", type=int)
    return parser


def _print_report(report) -> None:
    print(f"{report.status}: {report.message}")
    for line in report.fatal:
        print(f"  FATAL  {line}")
    for d in report.disagreements:
        print(f"  CHOOSE {d['file']}: file name says {d['file_name_sitenumber']}, header says "
              f"{d['header_sitecode']!r}  (re-run with --use name or --use header)")
    for f in report.refused_files:
        print(f"  REFUSED  {f['error']}")
    for s in report.sites:
        print(f"  {s.sitenumber}  cams {','.join(map(str, s.cams))}  {s.events:,} events"
              f"{'  (replaced)' if s.overwritten else ''}  QC Hub: {s.qch_lookup or 'not checked'}"
              f"{'  ' + s.name if s.name else ''}")
        for w in s.warnings:
            print(f"      warning   {w}")
        for a in s.advisories:
            print(f"      advisory  {a}")
    if report.unmatched:
        print(f"  skipped (not speed exports): {', '.join(report.unmatched)}")


def cmd_import(args, dry_run: bool) -> int:
    """SP-2: folder import. `scan` is the same with nothing written."""
    import tempfile

    from art_speed import project
    from art_speed.importer import run, scan
    try:
        if args.project:
            proj = project.open_folder(args.project, args.number)
        else:                                   # scan without a project: any number will do
            proj = project.Project(folder=__import__("pathlib").Path(tempfile.gettempdir()), number="000000")
    except project.ProjectError as exc:
        print(exc)
        return 1
    choices = None
    if args.use:
        which = scan.USE_NAME if args.use == "name" else scan.USE_HEADER
        try:
            found = scan.scan_folder(args.csv_folder)
        except scan.FolderError as exc:
            print(exc)
            return 1
        choices = {d["file"]: which for d in found.disagreements}
    report = run.import_folder(proj, args.csv_folder, choices=choices, use_qch=not args.no_qch,
                               dry_run=dry_run)
    _print_report(report)
    return 0 if report.status in (run.IMPORTED, run.SCANNED) else 1


def cmd_qch_site(args) -> int:
    """SP-1: the QC Hub lookup the import runs, on its own - nothing is stored."""
    from art_speed.qch import site
    for number, found in site.lookup_sites(args.sitenumbers).items():
        print(f"{number}: {found.describe()}")
        if found.found:
            print(f"    name         {found.name!r}   city/state {found.city!r} {found.state!r}")
            print(f"    lane configs {found.lane_configs}   control {found.control_type!r} {found.stop_controlled!r}")
            print(f"    lat/long     {found.latitude}, {found.longitude}")
            print(f"    count window {found.count_date} {found.start_time} for {found.duration_hr} h")
        for note in found.notes:
            print(f"    note: {note}")
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "version":
        print(f"speed tool {__version__}")
        return 0
    if args.command in ("import", "scan"):
        return cmd_import(args, dry_run=args.command == "scan")
    if args.command == "qch-site":
        return cmd_qch_site(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
