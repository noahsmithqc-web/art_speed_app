# Intersection Speed tool (`art_speed`)

Standalone tool for Quality Counts intersection speed studies: imports the Datalens speed CSVs
(`<sitenumber>_cam-<N>_speed.csv`), helps pick the source camera per approach, and fills the
delivered Excel report. Built as a Python package (`art_speed`: engine, storage, FastAPI router)
so it can be folded into A.R.T. later without moving any data (build plan 1.4, SP-8).

Specs live outside this repo, in `app_dev\claude\11 - intersection speed\`:
the build plan (`Intersection Speed - build plan 1.4.docx`), the locked mockup
(`ui mockup\speed_tab_mock_v15.html`) and one walkthrough per build step (`build\SP-n walkthrough.docx`).
The mockup is the ground truth for the screen; `tests/fixtures/speed_fixtures.json` is the same
thing as data (see `docs/CONTRACT.md`).

## First-time setup (development, Windows)

From Git Bash in this folder:

```bash
py -3.13 -m venv .venv
.venv/Scripts/python.exe -m pip install -r requirements-dev.txt
```

## Everyday use

| What | Command |
|---|---|
| Run the automated tests | `.venv/Scripts/python.exe -m pytest` |
| Tool version | `.venv/Scripts/python.exe -m art_speed version` |
| Read a folder of speed CSVs and report, writing nothing | `.venv/Scripts/python.exe -m art_speed scan <csv folder>` |
| Import a folder into a project | `.venv/Scripts/python.exe -m art_speed import <csv folder> --project <project folder> [--number 174083]` |
| Engine output for a folder of exports, as the mockup's JSON | `.venv/Scripts/python.exe -m art_speed dump out.json --csv <csv folder> --js` |
| The same for a project that already has speed data | `.venv/Scripts/python.exe -m art_speed dump out.json --project <project folder> --js` |
| What QC Hub knows about a sitenumber (stores nothing) | `.venv/Scripts/python.exe -m art_speed qch-site 17408304 17408305` |

`--project` takes an A.R.T. project folder as is; any other folder needs `--number`. If a file's
name and header give different sitenumbers the import stops and asks: re-run with `--use name`
or `--use header`. `--no-qch` skips the QC Hub lookup.

`dump` writes the contract JSON (and, with `--js`, the `.js` wrapper the mockup needs when opened
from disk). To try the mockup on another project: dump its exports, then open
`ui mockup\speed_tab_mock_v15_fixtures.html` and use "Load fixtures JSON…".

More commands are added as the build steps land (SP-4 server, SP-5 report).

## Layout

| Path | What |
|---|---|
| `art_speed/contract.py` | The JSON contract in code: every field the API returns (SP-0) |
| `docs/CONTRACT.md` | What each field means, and where the contract follows the plan rather than the mockup script |
| `tests/fixtures/speed_fixtures.json` | The contract fixture, extracted from mockup v15 |
| `tools/extract_fixtures.js` | Re-creates the fixture from the mockup (Node) |
| `tools/make_fixture_viewer.py` | Re-creates the fixture-driven copy of the mockup in the design folder |
| `art_speed/storage/db.py` | `speed_######.db`: schema, version stamp, upgrade on open (SP-1) |
| `art_speed/storage/store.py` | Every read and write of the database |
| `art_speed/importer/parse.py` | The strict Datalens speed CSV parser, raw and post-macro forms (SP-2) |
| `art_speed/importer/scan.py`, `run.py` | Folder scan, checks, delete-and-replace import |
| `art_speed/importer/nm_hint.py` | Reads `nm_######.db` read-only, once, for the grouping hint |
| `art_speed/engine.py` | The stats: pure functions over a filtered row set, no database (SP-3) |
| `art_speed/payload.py` | Builds the contract's drawer and tab payloads from plain data; the status, warning and suggestion rules |
| `art_speed/loader.py` | Database → engine → payloads |
| `art_speed/parity.py`, `docs/PARITY.md` | The delivered workbooks as parity targets and the documented deviations |
| `art_speed/qch/` | QC Hub connection, login (A.R.T.'s, read-only) and the per-sitenumber lookup |
| `art_speed/project.py` | Recognises an A.R.T. project folder (read-only) or takes a typed project number |
| `COPIED_FROM_ART.md` | Every file copied or adapted from `claude_app`, to be dropped at the merge |

## Rules this repo keeps

- Nothing here writes to `nm_######.db` or `qch_######.db`, and nothing here changes `claude_app`.
- "Turn Movement", never "Turning Movement". Speed groupings are "bins", never "bands".
- Real data (Datalens exports, delivered workbooks, databases) is never committed; tests that need
  the sample reports read them from the design folder and skip when it is not there.
