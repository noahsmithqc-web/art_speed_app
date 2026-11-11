"""Command line: `.venv/Scripts/python.exe -m art_speed <command>`."""

from __future__ import annotations

import argparse
import sys

from art_speed import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="art_speed", description="Intersection speed tool")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("version", help="print the tool version")
    q = sub.add_parser("qch-site", help="show what QC Hub knows about one or more sitenumbers")
    q.add_argument("sitenumbers", nargs="+", type=int)
    return parser


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
    if args.command == "qch-site":
        return cmd_qch_site(args)
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
