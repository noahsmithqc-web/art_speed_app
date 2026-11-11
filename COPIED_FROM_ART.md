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
| `art_speed/qch/credentials.py` | `art_app/qch/credentials.py` | Adapted | Reads A.R.T.'s `settings.json` and Credential Manager entry directly, read-only; everything that saves a login (store / forget password, `save_connection`) removed |
| `art_speed/qch/site.py` | `art_app/turn_movement/lookup.py`, `art_app/turn_movement/model.py` | Adapted | Same three SQL statements and value shaping (`as_date`, `as_time`, `as_float`, street name rule, first lat/long rule); returns a `Site` with the fields the speed db keeps instead of a turn movement `RosterRow`; adds the count window |
| `art_speed/log.py` | `art_app/log.py` | Adapted | Password masking unchanged; the rotating log file setup left out until the shell (SP-6) |
| `art_speed/config.py` | `art_app/config.py` | Adapted | The time limits and path limit only, plus the names of A.R.T.'s settings the tool reads |
| `art_speed/storage/db.py` | `art_app/near_miss/db.py` | Adapted | The open / create / check / upgrade / transaction pattern; the schema is the speed tool's own |
| `tests/fakes/qch.py` | `tests/fakes/qch.py` (`FakeQchDb`, `tm_site`, `tube_site`) | Adapted | Only the four tables the site lookup reads |
| `art_speed/api.py`, `art_speed/edits.py` | `art_app/reporting/routes.py`, `art_app/reporting/edits.py` | Pattern only | No code copied. Same split: thin routes over an edits module, every save in one transaction with its paper-trail line, the drawer read again and returned. Different on purpose: REST verbs and HTTP status codes for refusals (see the top of `api.py`) |

Later steps add their own rows to this table (SP-6: lane diagram JS, drawer CSS, build script).
