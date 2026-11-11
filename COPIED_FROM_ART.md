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

Later steps add their own rows to this table (SP-1: QC Hub connection and site lookup, the
database open/create/upgrade pattern; SP-4: the router pattern; SP-6: lane diagram JS, drawer CSS,
build script).
