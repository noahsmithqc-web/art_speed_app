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

More commands are added as the build steps land (SP-2 import, SP-3 engine dump, SP-4 server,
SP-5 report); this table is kept current.

## Layout

| Path | What |
|---|---|
| `art_speed/contract.py` | The JSON contract in code: every field the API returns (SP-0) |
| `docs/CONTRACT.md` | What each field means, and where the contract follows the plan rather than the mockup script |
| `tests/fixtures/speed_fixtures.json` | The contract fixture, extracted from mockup v15 |
| `tools/extract_fixtures.js` | Re-creates the fixture from the mockup (Node) |
| `tools/make_fixture_viewer.py` | Re-creates the fixture-driven copy of the mockup in the design folder |
| `COPIED_FROM_ART.md` | Every file copied or adapted from `claude_app`, to be dropped at the merge |

## Rules this repo keeps

- Nothing here writes to `nm_######.db` or `qch_######.db`, and nothing here changes `claude_app`.
- "Turn Movement", never "Turning Movement". Speed groupings are "bins", never "bands".
- Real data (Datalens exports, delivered workbooks, databases) is never committed; tests that need
  the sample reports read them from the design folder and skip when it is not there.
