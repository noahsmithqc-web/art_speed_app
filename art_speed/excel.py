"""Have Excel calculate and save a generated workbook, so it is ready to send (SP-5 follow-up).

The report is a fill of the delivered template: its numbers and charts are formulas, and the file
the tool writes holds the template's stored blanks until Excel has calculated it. Rather than ask
the user to open and save each workbook, the tool does that step itself: one hidden Excel is
started, opens every workbook of the run, calculates, saves and closes it.

Between calculating and saving it also turns the header into plain values (Noah, 2026-11-12:
"we want header info as values, not formulas"): every formula in Summary C2:H13 and in A1:J5 of
the four direction sheets is replaced by what it calculated to. Only Excel knows those results,
which is why this happens here and not in the writer. A cell showing an error is left as its
formula. Everything outside those ranges - the Summary row with the client's threshold, the
hourly table, summary_stats, the charts - stays live.

    done, message = excel.finalize([path, ...])

Excel is driven through Windows PowerShell (which every Windows machine has) and Excel's own
automation interface, so nothing extra is installed. It never shows a window or a prompt.

When it can't be done - not Windows, Excel not installed, Excel did not answer in time - nothing
is lost: the workbooks are still valid, they are still marked to calculate when opened, and the
answer says they need opening and saving in Excel before they are sent.
"""

from __future__ import annotations

import base64
import os
import subprocess
import sys
from pathlib import Path

from art_speed.log import get_logger

log = get_logger(__name__)

ENV_FILES = "SPEED_FINALIZE_FILES"
START_TIMEOUT_S = 90            # starting Excel and the first workbook
PER_FILE_TIMEOUT_S = 90         # each further workbook (a weeklong sheet takes a few seconds)
NOT_DONE = "Open each workbook in Excel and save it once before sending it."

SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$files = $env:SPEED_FINALIZE_FILES -split "`n" | Where-Object { $_ }
$excel = $null
$headers = @{ 'Summary' = 'C2:H13'; 'NB' = 'A1:J5'; 'SB' = 'A1:J5'; 'EB' = 'A1:J5'; 'WB' = 'A1:J5' }
function Set-HeaderValues($wb) {
    foreach ($name in $headers.Keys) {
        try {
            $ws = $wb.Worksheets.Item($name)
            foreach ($c in $ws.Range($headers[$name]).Cells) {
                if (-not $c.HasFormula) { continue }
                $v = $c.Value2
                if ($v -is [int]) { continue }          # an error value (#N/A ...): leave the formula
                # Every write goes in as text through .Formula: PowerShell fixes the type of a
                # property setter at its first use, so writing a text and then a number through
                # .Value2 fails ("Unable to cast Double to String").
                if ($null -eq $v -or ($v -is [string] -and $v -eq '')) { $c.Formula = '' }
                elseif ($v -is [string]) {
                    $c.Formula = $v
                    # Excel reads a text that looks like a number, a date or a formula as one: keep it text.
                    if ($c.HasFormula -or $c.Value2 -isnot [string]) { $c.Formula = "'" + $v }
                }
                elseif ($v -is [bool]) { $c.Formula = $(if ($v) { 'TRUE' } else { 'FALSE' }) }
                else { $c.Formula = ([double]$v).ToString('R', [Globalization.CultureInfo]::InvariantCulture) }
            }
        } catch {
            Write-Output ("NOTE " + $name + ": " + ($_.Exception.Message -replace "\s+", " "))
        }
    }
}
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AskToUpdateLinks = $false
    $excel.EnableEvents = $false
    foreach ($f in $files) {
        $wb = $excel.Workbooks.Open($f, 0, $false)
        $excel.CalculateFull()
        # Manual calculation while the header is written: otherwise every cell written makes Excel
        # recalculate the whole-column formulas, and a workbook takes most of a minute.
        $excel.Calculation = -4135
        Set-HeaderValues $wb
        $excel.Calculation = -4105
        $wb.Save()
        $wb.Close($false)
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($wb)
        Write-Output "OK $f"
    }
} finally {
    if ($excel) {
        $excel.Quit()
        [void][Runtime.InteropServices.Marshal]::ReleaseComObject($excel)
    }
    [GC]::Collect()
    [GC]::WaitForPendingFinalizers()
}
"""


def command() -> list:
    """powershell with the script passed encoded: no script file, no execution-policy prompt."""
    encoded = base64.b64encode(SCRIPT.encode("utf-16-le")).decode("ascii")
    return ["powershell.exe", "-NoProfile", "-NonInteractive", "-WindowStyle", "Hidden",
            "-EncodedCommand", encoded]


def finalize(paths: list, runner=None, is_windows: bool | None = None) -> tuple[bool, str]:
    """Open, calculate and save each workbook in Excel. Returns (done, a sentence for the user).

    `runner` stands in for subprocess.run in tests. Never raises.
    """
    paths = [str(Path(p).resolve()) for p in paths]
    if not paths:
        return True, ""
    if not (sys.platform == "win32" if is_windows is None else is_windows):
        return False, "Excel was not run (this is not a Windows computer). " + NOT_DONE
    run = runner or subprocess.run
    env = dict(os.environ, **{ENV_FILES: "\n".join(paths)})
    limit = START_TIMEOUT_S + PER_FILE_TIMEOUT_S * (len(paths) - 1)
    kwargs = {"capture_output": True, "text": True, "timeout": limit, "env": env}
    if runner is None and hasattr(subprocess, "CREATE_NO_WINDOW"):
        kwargs["creationflags"] = subprocess.CREATE_NO_WINDOW
    try:
        result = run(command(), **kwargs)
    except subprocess.TimeoutExpired:
        log.error("Excel did not finish %d workbook(s) within %d s", len(paths), limit)
        return False, f"Excel did not finish within {limit} seconds. " + NOT_DONE
    except OSError as exc:
        log.error("Excel could not be started: %s", exc)
        return False, "Excel could not be started. " + NOT_DONE
    saved = {line[3:].strip() for line in (result.stdout or "").splitlines() if line.startswith("OK ")}
    notes = sorted({line[5:].strip() for line in (result.stdout or "").splitlines() if line.startswith("NOTE ")})
    missing = [p for p in paths if p not in saved]
    if result.returncode != 0 or missing:
        detail = (result.stderr or "").strip().splitlines()
        log.error("Excel finished %d of %d workbook(s): %s", len(saved), len(paths),
                  detail[0] if detail else f"exit code {result.returncode}")
        if not saved:
            why = f" Excel said: {detail[0][:200]}" if detail else ""
            return False, "Excel could not calculate the workbooks (is Excel installed?)." + why + " " + NOT_DONE
        names = ", ".join(Path(p).name for p in missing)
        return False, f"Excel calculated {len(saved)} of {len(paths)} workbooks; not {names}. " + NOT_DONE
    if notes:
        log.error("header not written as values: %s", "; ".join(notes))
        return False, ("Calculated and saved by Excel, but the header could not be written as values "
                       f"({'; '.join(notes)[:300]}). The header cells are still formulas.")
    return True, "Calculated and saved by Excel: ready to send."
