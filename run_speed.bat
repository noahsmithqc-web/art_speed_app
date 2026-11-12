@echo off
rem Start the speed tool from source. Close this window to stop it.
cd /d "%~dp0"
if exist ".venv\Scripts\python.exe" (
    set "PY=.venv\Scripts\python.exe"
) else (
    set "PY=py -3.13"
)
%PY% -m art_speed serve %*
if errorlevel 1 pause
