# Parity with the delivered speed workbooks (SP-3)

Targets: the six workbooks in `sample reports\final report` (174083's five locations and
17650202), fifteen delivered directions. `tests/test_parity.py` proves three statements.

**A. Fed the delivered records, the engine gives the delivered numbers.** For every direction
sheet, the engine's 85th percentile and % over posted of the sheet's own speed column equal the
values Excel last computed (`H5`, `J2`), and the hourly 85th and 95th equal `summary_stats` for
all 24 hours. 15 of 15 directions, exactly.

**B. Fed the raw Datalens exports and the delivered decisions, the tool gives the delivered
numbers.** The exports go through the real import into a speed db; each direction gets the source
cam the delivered sheet was built from, no adjustment, and the manual floor. Row count, 85th,
% over posted and every hourly 85th / 95th equal the workbook. 15 of 15, exactly.

The delivered decisions were recovered by matching every delivered record (time + track id) to
the exports. Every record matched one cam with no speed offset:

| Sitenumber | Dir | Source cam | Manual floor | Delivered n |
|---|---|---|---|---|
| 17408301 | NB / SB | 2 / 3 | none / none | 1,764 / 1,590 |
| 17408302 | NB / SB | 3 / 3 | none / none | 937 / 783 |
| 17408304 | NB / SB | 2 / 2 | **below 4** / none | 4,783 / 4,322 |
| 17408305 | NB / SB / EB | 3 / 4 / 4 | none / none / none | 6,846 / 6,938 / 8 |
| 17408306 | NB / SB | 1 / 1 | **below 4** / **below 4** | 3,304 / 4,182 |
| 17650202 | NB / SB / EB / WB | 1 / 2 / 2 / 1 | none ×4 | 1,575 / 1,410 / 61 / 72 |

"none" = zeros removed, every non-zero speed kept. The manual floor was applied to three
directions only, which is the inconsistency the plan refers to.

**C. With the tool's default floor (below 5 mph) the 85th percentiles do not move; % over posted
moves slightly.** These are the documented manual deviations:

| Sitenumber | Dir | Delivered 85th | Tool 85th | Delivered % over | Tool % over (floor 5) | Shift (points) | n delivered → tool |
|---|---|---|---|---|---|---|---|
| 17408301 | NB | 26 | 26 | 20.12 % | 20.18 % | +0.06 | 1,764 → 1,759 |
| 17408301 | SB | 26 | 26 | 22.77 % | 22.80 % | +0.03 | 1,590 → 1,588 |
| 17408302 | NB | 28 | 28 | 34.26 % | 34.29 % | +0.04 | 937 → 936 |
| 17408302 | SB | 29 | 29 | 41.63 % | 41.90 % | +0.27 | 783 → 778 |
| 17408304 | NB | 27 | 27 | 25.28 % | 25.35 % | +0.07 | 4,783 → 4,770 |
| 17408304 | SB | 26 | 26 | 17.42 % | 17.43 % | +0.01 | 4,322 → 4,320 |
| 17408305 | NB | 29 | 29 | 46.00 % | 46.02 % | +0.02 | 6,846 → 6,843 |
| 17408305 | SB | 27 | 27 | 21.89 % | 22.17 % | +0.27 | 6,938 → 6,852 |
| 17408305 | EB | 13.95 | 13.95 | 0.00 % | 0.00 % | 0 | 8 → 8 |
| 17408306 | NB | 15 | 15 | 0.15 % | 0.15 % | +0.003 | 3,304 → 3,242 |
| 17408306 | SB | 24 | 24 | 6.29 % | 6.30 % | +0.01 | 4,182 → 4,177 |
| 17650202 | NB | 14 | 14 | 0.19 % | 0.19 % | +0.001 | 1,575 → 1,563 |
| 17650202 | SB | 11 | 11 | 0.07 % | 0.07 % | +0.001 | 1,410 → 1,391 |
| 17650202 | EB | 16 | 16 | 0.00 % | 0.00 % | 0 | 61 → 61 |
| 17650202 | WB | 15 | 15 | 0.00 % | 0.00 % | 0 | 72 → 70 |

Largest shift: 0.27 of a percentage point (17408302 SB, 17408305 SB). The shift is always upward:
dropping very slow speeds makes the share over posted slightly larger. The test pins every
"tool" value in this table (`parity.DEFAULT_FLOOR`) and fails if any 85th percentile moves.

Build plan 1.4 section 6 expected "17408304 NB matches exactly and three other directions shift
by 0.1–0.3 points". Measured: all fifteen 85th percentiles match exactly; 17408304 NB's % over
posted moves by 0.07 because its manual floor was "below 4", not "below 5"; two directions move
by 0.27 and the rest by 0.07 or less.

## The mockup's numbers

`tests/test_engine_fixtures.py` separately proves that the engine rebuilds every field of the six
drawers the locked mockup computed (speed_fixtures.json), and that 174083's raw exports, taken
through the real import and the database, give the same numbers as the mockup's baked-in data.

## The report writer (SP-5)

`tests/test_report.py` generates all six locations from the raw exports with the delivered
decisions and checks, for every direction sheet, that the records written (date, time, direction,
class, speed, track id, helper hour) are exactly the delivered sheet's records, and that the header
fields (sitecode, location, start date, start time, duration, posted speeds) are the delivered
ones. Where LibreOffice is installed, it also recalculates two generated workbooks with the
template's own formulas and checks the Summary prints the delivered 85th percentiles and % over
posted - the same check Excel performs when it opens the file.
