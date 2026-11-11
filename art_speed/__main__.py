"""Command line: `.venv/Scripts/python.exe -m art_speed <command>`."""

from __future__ import annotations

import argparse
import sys

from art_speed import __version__


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="art_speed", description="Intersection speed tool")
    sub = parser.add_subparsers(dest="command")
    sub.add_parser("version", help="print the tool version")
    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    if args.command == "version":
        print(f"speed tool {__version__}")
        return 0
    parser.print_help()
    return 2


if __name__ == "__main__":
    sys.exit(main())
