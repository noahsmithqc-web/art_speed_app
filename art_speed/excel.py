"""Have Excel calculate and save a generated workbook, so it is ready to send (SP-5 follow-up).

The report is a fill of the delivered template: its numbers and charts are formulas, and the file
the tool writes holds the template's stored blanks until Excel has calculated it. Rather than ask
the user to open and save each workbook, the tool does that step itself: one hidden Excel is
started, opens every workbook of the run, calculates, saves and closes it.

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
PER_FILE_TIMEOUT_S = 45         # each further workbook (a weeklong sheet takes a few seconds)
NOT_DONE = "Open each workbook in Excel and save it once before sending it."

SCRIPT = r"""
$ErrorActionPreference = 'Stop'
$files = $env:SPEED_FINALIZE_FILES -split "`n" | Where-Object { $_ }
$excel = $null
try {
    $excel = New-Object -ComObject Excel.Application
    $excel.Visible = $false
    $excel.DisplayAlerts = $false
    $excel.AskToUpdateLinks = $false
    $excel.EnableEvents = $false
    foreach ($f in $files) {
        $wb = $excel.Workbooks.Open($f, 0, $false)
        $excel.CalculateFull()
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
    missing = [p for p in paths if p not in saved]
    if result.returncode != 0 or missing:
        detail = (result.stderr or "").strip().splitlines()
        log.error("Excel finished %d of %d workbook(s): %s", len(saved), len(paths),
                  detail[0] if detail else f"exit code {result.returncode}")
        if not saved:
            return False, "Excel could not calculate the workbooks (is Excel installed?). " + NOT_DONE
        names = ", ".join(Path(p).name for p in missing)
        return False, f"Excel calculated {len(saved)} of {len(paths)} workbooks; not {names}. " + NOT_DONE
    return True, "Calculated and saved by Excel: ready to send."
