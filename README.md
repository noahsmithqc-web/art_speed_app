# Intersection Speed tool (`art_speed`)

Standalone tool for Quality Counts intersection speed studies: imports the Datalens speed CSVs
(`<sitenumber>_cam-<N>_speed.csv`, or since 0.1.2 `<sitenumber>_cam-<N>_speed_<gate version>.csv`), helps pick the source camera per approach, and fills the
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

## Start the tool

Double-click `run_speed.bat` (or `.venv/Scripts/python.exe -m art_speed serve`). A black window
opens - that window is the app - and a browser tab opens on `http://127.0.0.1:8766/`. Close the
black window to stop the tool.

- **Home** has "New project" (A.R.T.'s wizard: type the project number, QC Hub fills name,
  client and state, and a subfolder named with the number is created in the default project
  folder - an A.R.T. project folder, so A.R.T. opens it too), the recent projects, and "Open
  other project…". One project is open at a time. An A.R.T. project folder opens as it is; any
  other folder asks for the six-digit project number once and remembers it with the recent card.
- **Settings** (on Home) holds the default project folder and the tool's own QC Hub connection
  (host, port, database, username, password - the password goes to Windows Credential Manager
  under "Speed tool QCH", checked against QC Hub before it is stored). The tool never reads
  A.R.T.'s login.
- **Speed** is the tab of the locked mockup: Add speed data (a file picker: select the speed
  CSVs to import, several at once, from one folder), the locations table, the drawer
  (Setup, Overview, Decisions, Detail, Paper trail), group / split, Generate report.

In a grouped location (several sitenumbers in one report) the same cam number on every
sitenumber is one camera: one row in Setup, the Overview matrix and Decisions, with its
statistics and report rows over the whole count (`docs/CONTRACT.md`, "Cam groups").

The tool keeps its own files (settings, recent list, `speed.log`) in `%LOCALAPPDATA%\SpeedTool`;
set `SPEED_HOME` to put them somewhere else.

## Build it for the team

Double-click `build_speed.bat` (needs the `.venv`). It packages the tool with PyInstaller, runs
the packaged tool's self-check, and leaves:

- `dist\SpeedTool\SpeedTool.exe` - the tool and everything it needs, no Python required;
- `dist\SpeedTool-<version>.zip` - what a teammate gets: unzip anywhere, double-click
  `SpeedTool.exe`. A black window opens (the app) and a browser tab opens on it; closing the
  black window stops the tool. Excel must be installed for reports to come out calculated.

`SpeedTool.exe selfcheck` lists every bundled file and module; a MISSING line means
`packaging/Speed.spec` needs an entry. The version comes from `art_speed/__init__.py`.

## Everyday use

| What | Command |
|---|---|
| Start the tool (app + browser tab) | `.venv/Scripts/python.exe -m art_speed serve [--project <project folder>] [--number 174083] [--no-browser]` |
| Run the automated tests | `.venv/Scripts/python.exe -m pytest` |
| Tool version | `.venv/Scripts/python.exe -m art_speed version` |
| Read a folder of speed CSVs and report, writing nothing | `.venv/Scripts/python.exe -m art_speed scan <csv folder>` |
| Import a folder into a project | `.venv/Scripts/python.exe -m art_speed import <csv folder> --project <project folder> [--number 174083]` |
| Engine output for a folder of exports, as the mockup's JSON | `.venv/Scripts/python.exe -m art_speed dump out.json --csv <csv folder> --js` |
| The same for a project that already has speed data | `.venv/Scripts/python.exe -m art_speed dump out.json --project <project folder> --js` |
| Generate the report workbooks of every Ready location | `.venv/Scripts/python.exe -m art_speed report --project <project folder> [--number 174083] [--ids 3 4]` |
| Run the bare API of one project (development) | `.venv/Scripts/python.exe -m art_speed api --project <project folder> [--number 174083]` |
| ... with the mockup showing the project's live data, read-only | add `--mockup "../claude/11 - intersection speed/ui mockup"`, then open `http://127.0.0.1:8766/mockup/` |
| What QC Hub knows about a sitenumber (stores nothing) | `.venv/Scripts/python.exe -m art_speed qch-site 17408304 17408305` |

`--project` takes an A.R.T. project folder as is; any other folder needs `--number`. If a file's
name and header give different sitenumbers the import stops and asks: re-run with `--use name`
or `--use header`. `--no-qch` skips the QC Hub lookup.

`dump` writes the contract JSON (and, with `--js`, the `.js` wrapper the mockup needs when opened
from disk). To try the mockup on another project: dump its exports, then open
`ui mockup\speed_tab_mock_v15_fixtures.html` and use "Load fixtures JSON…".

The API's routes are listed at the top of `art_speed/api.py`.

`report` writes one workbook per location into a new `reports_yyyy_mm_dd__hhmm` folder in the
project folder. The workbook is the delivered template with the header fields and the source
cam's rows filled in. Its summary, hourly table and charts are Excel formulas, so after writing
the workbooks the tool has Excel (hidden) open, calculate and save each one: they are ready to
send. If Excel can't be run, the answer says so and the workbooks need opening and saving once.

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
| `art_speed/edits.py` | Every change a user makes: validates, writes, adds the paper-trail line (SP-4) |
| `art_speed/api.py` | The FastAPI router (`/speed/...`); mounted by the shell now and by A.R.T. at the merge |
| `art_speed/server.py` | A bare app around the router for development and tests (`art_speed api`) |
| `art_speed/main.py` | The app the tool runs (SP-6): the shell's `/api/...` routes, the `/speed` router, the page |
| `art_speed/session.py` | The one open project |
| `art_speed/notes.py`, `web/notes.js` | Internal notes: the box under the locations table, stored in `######_notes.json` in the project folder; never read by the report writer |
| `art_speed/create_project/wizard.py`, `manifest.py`, `qch/lookup.py`, `qch/regions.py` | New project: A.R.T.'s wizard, project file and project lookup, copied |
| `art_speed/appstate/`, `paths.py`, `jsonfile.py`, `dialogs.py` | Settings, the recent list, where the tool keeps its files, the native folder picker |
| `art_speed/web/` | The page: `index.html`, `shell.js` (rail, Home, Settings, pop-ups), `speed.js` (the speed tab and drawer), `boot.js` |
| `art_speed/web/shell.css`, `speed.css` | A.R.T.'s stylesheet and the mockup's, each under its own scope; written by `tools/build_web_css.py` |
| `run_speed.bat` | Starts the tool from source |
| `build_speed.bat`, `packaging/`, `requirements-build.txt` | The packaged build: `dist\SpeedTool\` and its zip |
| `CHANGELOG.md` | Release notes; copied beside `SpeedTool.exe` |
| `art_speed/report.py` | The report writer: fills the delivered template, header fields and source-cam rows only (SP-5) |
| `art_speed/excel.py` | Has Excel calculate each generated workbook, turn the header (Summary C2:H13, direction sheets A1:J5) into values, and save it |
| `art_speed/reference/report_templates/speed_report_template.xlsx` | The shipped template (made by `tools/build_report_template.py` from the design folder's) |
| `art_speed/qch/` | QC Hub connection, the tool's own login (Settings) and the per-sitenumber lookup |
| `art_speed/project.py` | Recognises an A.R.T. project folder (read-only) or takes a typed project number |
| `COPIED_FROM_ART.md` | Every file copied or adapted from `claude_app`, to be dropped at the merge |

## Rules this repo keeps

- Nothing here writes to `nm_######.db` or `qch_######.db`, and nothing here changes `claude_app`.
- "Turn Movement", never "Turning Movement". Speed groupings are "bins", never "bands".
- Real data (Datalens exports, delivered workbooks, databases) is never committed; tests that need
  the sample reports read them from the design folder and skip when it is not there.
