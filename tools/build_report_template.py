"""SP-5: make the shipped report template from the delivered template in the design folder.

    python tools/build_report_template.py "<...>/sample reports/template - report/speed deliverable template w charts 2.xlsx"

Writes art_speed/reference/report_templates/speed_report_template.xlsx.

The shipped file is the delivered template, part for part, with one thing taken out: the
Locations helper sheet's sample rows (ten sites of another project). Their cells are emptied and
the texts only they used are blanked in the shared-strings table, so no other project's site
list travels inside a client's workbook. Every other part - Summary, the direction sheets,
summary_stats, the five charts, the logo, the styles - is copied byte for byte. The script
prints what it changed and refuses to run on a template it does not recognise.
"""
from __future__ import annotations

import re
import sys
import zipfile
from pathlib import Path

OUT = Path(__file__).resolve().parent.parent / "art_speed" / "reference" / "report_templates" / "speed_report_template.xlsx"
LOCATIONS = "xl/worksheets/sheet6.xml"
STRINGS = "xl/sharedStrings.xml"
EXPECTED_SHEETS = ["Summary", "NB", "SB", "EB", "WB", "Locations", "summary_stats"]


def empty_locations_rows(xml: str) -> tuple[str, int]:
    """Rows 2 and below of the Locations sheet: keep each cell's style, drop its value and formula."""
    head, rest = xml.split("<sheetData>", 1)
    data, tail = rest.split("</sheetData>", 1)
    rows = re.findall(r"<row [^>]*>.*?</row>|<row [^>]*/>", data, flags=re.S)
    out, emptied = [], 0
    for row in rows:
        number = int(re.search(r'r="(\d+)"', row).group(1))
        if number == 1:
            out.append(row)
            continue
        def strip(m):
            nonlocal emptied
            attrs = re.sub(r'\s+t="[^"]*"', "", m.group(1))
            if m.group(2) is not None:
                emptied += 1
            return f"<c{attrs}/>"
        out.append(re.sub(r"<c((?:\s+[\w:]+=\"[^\"]*\")*)\s*(?:/>|>(.*?)</c>)", strip, row, flags=re.S))
    return head + "<sheetData>" + "".join(out) + "</sheetData>" + tail, emptied


def referenced_strings(parts: dict) -> set:
    used = set()
    for name, xml in parts.items():
        if name.startswith("xl/worksheets/sheet"):
            used.update(int(i) for i in re.findall(r'<c [^>]*t="s"[^>]*><v>(\d+)</v>', xml))
    return used


def main(argv: list) -> int:
    if len(argv) != 2:
        print(__doc__)
        return 2
    source = Path(argv[1])
    with zipfile.ZipFile(source) as z:
        infos = z.infolist()
        raw = {i.filename: z.read(i.filename) for i in infos}
    workbook = raw["xl/workbook.xml"].decode("utf-8")
    sheets = re.findall(r'<sheet name="([^"]+)"', workbook)
    if sheets != EXPECTED_SHEETS:
        print(f"not the speed deliverable template: sheets are {sheets}")
        return 1
    text = {n: raw[n].decode("utf-8") for n in raw if n.startswith("xl/worksheets/sheet") or n == STRINGS}
    before = referenced_strings(text)
    text[LOCATIONS], emptied = empty_locations_rows(text[LOCATIONS])
    orphans = sorted(before - referenced_strings(text))
    items = re.findall(r"<si>.*?</si>|<si/>", text[STRINGS], flags=re.S)
    for i in orphans:
        items[i] = "<si><t/></si>"
    head = text[STRINGS][:text[STRINGS].index("<si")]
    text[STRINGS] = head + "".join(items) + "</sst>"
    OUT.parent.mkdir(parents=True, exist_ok=True)
    with zipfile.ZipFile(OUT, "w", zipfile.ZIP_DEFLATED) as z:
        for info in infos:                       # same parts, same order
            data = text[info.filename].encode("utf-8") if info.filename in (LOCATIONS, STRINGS) else raw[info.filename]
            z.writestr(zipfile.ZipInfo(info.filename, date_time=(2026, 1, 1, 0, 0, 0)), data, zipfile.ZIP_DEFLATED)
    print(f"wrote {OUT}")
    print(f"  Locations: {emptied} sample cells emptied; {len(orphans)} texts only they used were blanked")
    print(f"  every other part copied unchanged ({len(infos) - 2} of {len(infos)})")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
