# The speed contract (SP-0)

`tests/fixtures/speed_fixtures.json` is the locked mockup (v15) as data. The engine dump (SP-3)
and the API (SP-4) return exactly these fields; `art_speed/contract.py` is the same list in code
and `contract.check_drawer()` / `check_tab()` / `check_fixtures()` hold any payload against it.

```
{ "contract_version": 1,
  "project":   { number, name, state, client, db, is_art_project },
  "tab":       { summary, generate, rows[] },          GET /speed/locations
  "locations": { "<id>": drawer },                     GET /speed/locations/{id}
  "viewer":    { fast_rows, open_location } }          mockup viewer only, never from the API
```

## Tab

| Field | Meaning |
|---|---|
| `summary` | sitenumbers / locations / cams in the db, `last_import`, `qch_snapshot`, `last_report` (`YYYY-MM-DD HH:MM`, or null) |
| `generate.enabled` | at least one location is Ready |
| `generate.green` | every location is Ready (the near miss green rule) |
| `rows[].code` | sitecode; grouped `01+02`; a split carries its slice in `slices[].label` |
| `rows[].slices[]` | `sitenumber`, optional `date`, optional `from_hour`–`to_hour` (to is exclusive), `label` |
| `rows[].window` | count window: `start_date`, `start_time` (`06:00 AM`), `duration_hours`, `source` (QC Hub / file header / edited / slice) |
| `rows[].corners` / `posted` / `source_cams` | `{set, of}` – the "3/5" columns |
| `rows[].status` | `state` ready / needs_input, `label`, `missing[]` (what stops it being Ready) |
| `rows[].warnings[]` | warning sentences (the tag's hover text) |

## Drawer

| Block | Contents |
|---|---|
| `location` | id, code, name (`name_source` qch / manual, `manual_name`, `qch_name`), `sitenumbers[]` (sitecode, name, `qch_lookup`, the file `header`), `slices[]`, `is_sliced`, `is_grouped`, lat/long, `geometry_source`, `nm_grouping_used`, `corners_linked`, `window`, `count_days[]`, totals (`cams`, `events`, `masked_hours`) |
| `posted_same` | per axis: is "Same both ways" ticked |
| `directions.NB…WB` | `exists` (lane config has a lane), `include_in_report`, `posted`, `lane_config` + `lane_config_qch`, `source_cam` (cam key), `suggested` `{cam, why}`, `adjust_by`, `mask` `{low, high, is_default}`, `report` (below), `off_leg_events` (rows in a direction that is not in the report) |
| `directions.X.report` | null unless the direction is in the report and has a source cam. `events` (rows incl. zeros), `zero`, `pct_zero`, `masked` (removed by the two speed-mask handles), `pct_masked` (of non-zero), `n`, `avg`, `p85`, `p95`, `over_posted`, `pct_over_posted`, `masked_hours` |
| `cams[]` | `key` (`sitenumber:cam`), `label`, `corner`, `file`, `csv_form` (raw / post-macro), `events` (whole file), `events_in_location` (inside this location's slices, time-masked hours out), `dead`, `masked_hours[]` (`YYYY-MM-DD HH`), `mask_acknowledged`, `hours_with_data`, `over_half_masked`, and per direction the raw QC stats: `n`, `zero`, `n_nonzero`, `pct_zero`, `avg`, `p85`, `role` (Primary / Secondary / "") |
| `histograms[cam][dir]` | raw speeds of that cam × direction inside the location (zeros apart): `bins` (1-mph → count), `occupied` (the outlier dots), `min_speed`, `max_speed`, `bins10[]` (`from`, `to`, `n`, `above_upper_mask`), `events`, `zero`, `masked_hours` |
| `hourly` | `dates[]`, `peak_hours` (7, 8, 16, 17), `scale_max` (heatmap top; zero is always the floor), `thin_below` 30, `blank_below` 5, and per included direction `source_cam`, `masked_hours`, `shared_with[]`, `days[]` = `date`, day `n` / `p85` / `p95`, and 24 `cells` (null = no data that hour; else `rows`, `zero`, `n`, `p85`, `p95`, `masked`, `peak`) |
| `bins[cam][dir][date-hour][speed]` | hourly 1-mph bin counts, whole file, zeros included (plan 6a) – what a dynamic report would embed |
| `note` | the report note |
| `status` | as on the tab |
| `warnings[]` | `{code, text}`; `over_half_masked` also has `cam` and `can_acknowledge` |
| `paper_trail[]` | newest first: `when`, `what`, `who`, `scope` (location / cam; cam entries carry `sitenumber`). The page prints `what` then `when   [by who]` |

## Definitions (plan section 6, confirmed SP-Q12)

Order: slice window → time masks (masked date-hours of the cam) → zeros out → lower mask
(`low` 5 means "below 5 is masked") → upper mask → `adjust_by` → stats.
Percentiles are Excel's PERCENTILE.INC. `% over posted` is strictly greater than posted, on the
masked, adjusted speeds. `% zero` is raw, before any speed mask. Hourly cells use the same rules
on one date-hour; a masked hour keeps its struck-through value but is out of every stat, the scale
and the row peak. `peak` = the highest whole-mph hour of the day among unmasked hours with 5 or
more speeds (ties all flagged). `scale_max` = the highest unmasked hourly 85th with 30 or more
speeds. Whole-mph rounding is half-up (what the mockup's `Math.round` does), never banker's.

## Where the contract differs from the mockup script

The mockup's look is the contract. Three rules in its script are older than the answers in the
build plan, and the plan wins; the extractor (`tools/extract_fixtures.js`) applies them:

| Rule | Mockup v15 script | Contract (plan) |
|---|---|---|
| Dead cam | more than 95 % zero speeds over the included directions (its tag says "> 50 %") | 100 % zero speeds over the whole file (SP-Q9) |
| Source-cam suggestion | skips a cam with more than 50 % zeros in that direction | considers every live cam with at least one non-zero speed in the direction: primary corner, else secondary, else lowest % zero; % zero breaks ties (plan 6) |
| Low n | warns under 30 speeds | no low-n warning or tag (SP-Q9, SP-Q14) |

Two warning codes exist in the contract that the mockup data never raises: `header_vs_qch`
(file header disagrees with QC Hub) and `not_in_qch` (site entered by hand).
