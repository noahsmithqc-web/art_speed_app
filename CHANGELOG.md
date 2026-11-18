# Changelog - Intersection Speed tool

## 0.1.1 (2026-11-18)

- Internal notes: a box under the locations table on the Speed tab for notes the team keeps
  about a project (the same box as A.R.T.'s). Bold, italic, underline, text colours, highlights
  and tick boxes; Edit / Save / Cancel; a tick can be made without Edit. Never printed on a
  report. Stored in `######_notes.json` in the project folder.

## 0.1.0 (2026-11-12)

First packaged build.

- Home with New project (QC Hub fills name, client, state), recent projects, Open other project.
- Settings: default project folder and the tool's own QC Hub connection.
- Add speed data: pick the Datalens speed CSVs to import; re-import replaces a sitenumber.
- Speed tab and drawer: posted speeds, lane configs, count window, camera corners, source cam per
  approach with suggestions, adjust-by, speed mask, hourly time mask, group and split locations.
- Generate report: one workbook per location from the report template, calculated and saved by
  Excel, header written as values.
