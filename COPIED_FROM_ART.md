# Copied from A.R.T.

The speed tool is standalone in packaging only (build plan 1.4). Pieces it shares with A.R.T. were
**copied** from `claude_app` on purpose, not imported, so that this repo runs on its own. When the
tool is folded into A.R.T. (SP-8) every file below is deleted here and the A.R.T. original is used.

Nothing in `claude_app` was changed to make these copies. Source commit: `claude_app` `edcc5f2`
(1.2.12, 2026-11-11).

"Copied" = the same code with only the import lines and names changed (`art_app` → `art_speed`).
"Adapted" = started from the A.R.T. file but trimmed or reshaped; what changed is stated.

| File here | From `claude_app` | How | What changed |
|---|---|---|---|
| `.gitignore` | `.gitignore` | Adapted | `*.db.creating` added; template exception points at `art_speed/reference/report_templates` |
| `.gitattributes` | `.gitattributes` | Adapted | `*.xlsx binary` added |
| `pytest.ini` | `pytest.ini` | Copied | — |
| `requirements.txt`, `requirements-dev.txt` | same names | Adapted | Same pins; `xlrd` dropped (no .xls here) |
| `art_speed/fsguard.py` | `art_app/fsguard.py` | Copied | Probe file prefix `.speed_write_test_` |
| `art_speed/qch/connection.py` | `art_app/qch/connection.py` | Copied | Two words in messages ("import" for "wizard", "the speed tool" for "A.R.T.") |
| `art_speed/qch/credentials.py` | `art_app/qch/credentials.py` | Copied (SP-6) | The speed tool keeps its own login: Credential Manager service `Speed tool QCH`, dev file variable `SPEED_QCH_CREDENTIALS`. It no longer reads A.R.T.'s |
| `art_speed/qch/site.py` | `art_app/turn_movement/lookup.py`, `art_app/turn_movement/model.py` | Adapted | Same three SQL statements and value shaping (`as_date`, `as_time`, `as_float`, street name rule, first lat/long rule); returns a `Site` with the fields the speed db keeps instead of a turn movement `RosterRow`; adds the count window |
| `art_speed/log.py` | `art_app/log.py` | Copied (SP-6) | Log file is `speed.log` |
| `art_speed/config.py` | `art_app/config.py` | Adapted | The time limits, path limit, host, recent-list and log limits; app folder `SpeedTool`, port 8766, `SPEED_HOME` / `SPEED_QCH_CREDENTIALS` |
| `art_speed/storage/db.py` | `art_app/near_miss/db.py` | Adapted | The open / create / check / upgrade / transaction pattern; the schema is the speed tool's own |
| `tests/fakes/qch.py` | `tests/fakes/qch.py` (`FakeQchDb`, `tm_site`, `tube_site`) | Adapted | Only the four tables the site lookup reads |
| `art_speed/api.py`, `art_speed/edits.py` | `art_app/reporting/routes.py`, `art_app/reporting/edits.py` | Pattern only | No code copied. Same split: thin routes over an edits module, every save in one transaction with its paper-trail line, the drawer read again and returned. Different on purpose: REST verbs and HTTP status codes for refusals (see the top of `api.py`) |
| `art_speed/report.py` (`reports_folder`) | `art_app/reporting/generate.py` | Pattern only | The timestamped `reports_yyyy_mm_dd__hhmm` run folder and the report log. No code copied; the workbook writer is the speed tool's own |

Not from A.R.T.: `art_speed/reference/report_templates/speed_report_template.xlsx` is the delivered
speed template from the design folder (`sample reports\template - report`), with its Locations
sample rows emptied by `tools/build_report_template.py`.

## SP-6: the shell

Source commit for these: `claude_app` as it stood on 2026-11-12 (read only; nothing there was changed).

| File here | From `claude_app` | How | What changed |
|---|---|---|---|
| `art_speed/jsonfile.py` | `art_app/jsonfile.py` | Copied | — |
| `art_speed/paths.py` | `art_app/paths.py` | Copied | App folder `%LOCALAPPDATA%\SpeedTool`, variable `SPEED_HOME`, log file `speed.log` |
| `art_speed/appstate/recent.py`, `settings.py` | `art_app/appstate/` | Copied | — |
| `art_speed/dialogs.py` | `art_app/dialogs.py` | Copied | — |
| `art_speed/session.py` | `art_app/session.py` | Adapted | Same one-open-project rule; opens with `project.open_folder`, which also takes a folder that is not an A.R.T. project plus a typed number |
| `art_speed/main.py` | `art_app/main.py` | Adapted | Only the routes the shell needs (app, settings, QCH credentials, folder picker, recent projects, open / close / reveal); answers in A.R.T.'s `{ok, error:{kind, message}}` shape. No heartbeat: the tool stops when its window is closed |
| `art_speed/web/shell.js` | `art_app/web/app.js`, `home.js`, `settings.js` | Adapted | The engine calls, modal, toast, rail, Home cards, folder picker and the whole Settings / About pop-up are A.R.T.'s code; the wizard, Summary and the two data tabs are left out; Home opens a folder instead of creating a project; the page state object is `APP` (the mockup's script already uses `S`) |
| `art_speed/create_project/wizard.py` | `art_app/create_project/wizard.py` | Copied | The photos-folder name (`bike_ped_paths`) is written in rather than imported from `reporting/photos.py` |
| `art_speed/manifest.py` | `art_app/project/manifest.py` | Copied | Lives at the package root (`art_speed/project.py` already has that name) |
| `art_speed/qch/lookup.py`, `qch/regions.py`, `reference/state_nm_region.csv` | same paths under `art_app` | Copied | — |
| `art_speed/web/wizard.js` | `art_app/web/wizard.js` | Copied | `S` → `APP`; lands on the speed tab; "filled in later from Summary" dropped (no Summary here) |
| `art_speed/web/shell.css` | `art_app/web/styles.css` | Generated | The whole sheet, every rule scoped under `.as` by `tools/build_web_css.py` so it cannot touch the speed tab |
| `art_speed/web/assets/qc_logo_white.png` | `art_app/web/assets/qc_logo_white.png` | Copied | — |
| `run_speed.bat` | `run_art.bat` | Adapted | Starts `art_speed serve` |
| `tests/conftest.py` (`speed_home`) | `tests/conftest.py` | Adapted | The same isolation: a throw-away app folder and a stubbed Credential Manager for every test |

From the locked mockup, not from A.R.T.: `art_speed/web/speed.css` (its `<style>` block, scoped
under `.sv` by the same tool) and the drawing code of `art_speed/web/speed.js` (the lane diagram,
matrix, histograms and tables are the mockup's own functions; see the file's header).

Later steps add their own rows (SP-7: build script).
