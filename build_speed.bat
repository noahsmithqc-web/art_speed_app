@echo off
rem Build the speed tool for the team. Run from the speed_app folder (double-click is fine).
rem Output: dist\SpeedTool\SpeedTool.exe   (double-click to start the tool)
rem         dist\SpeedTool-<version>.zip   - unzip anywhere, double-click SpeedTool.exe
rem build\ is scratch; build\ and dist\ are git-ignored.
cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo No .venv here - create it first:  py -3.13 -m venv .venv
    pause
    exit /b 1
)
set "PY=.venv\Scripts\python.exe"

echo Installing the build tools...
%PY% -m pip install -q -r requirements-build.txt
if errorlevel 1 goto failed

echo Building...
%PY% -m PyInstaller --noconfirm --clean --distpath dist --workpath build packaging\Speed.spec
if errorlevel 1 goto failed

echo.
echo Self-check:
dist\SpeedTool\SpeedTool.exe selfcheck
if errorlevel 1 goto failed

rem The release notes ship beside SpeedTool.exe (a plain copy; nothing in the tool reads it).
copy /Y CHANGELOG.md dist\SpeedTool\CHANGELOG.md >nul
if errorlevel 1 goto failed

for /f "delims=" %%v in ('dist\SpeedTool\SpeedTool.exe version --bare') do set "VER=%%v"
set "ZIP=dist\SpeedTool-%VER%.zip"
echo.
echo Zipping %ZIP% ...
if exist "%ZIP%" del "%ZIP%"
powershell -NoProfile -Command "Compress-Archive -Path 'dist\SpeedTool' -DestinationPath '%ZIP%'"
if errorlevel 1 goto failed

echo.
echo Built:  %CD%\dist\SpeedTool\SpeedTool.exe
echo Zip:    %CD%\%ZIP%
echo.
pause
exit /b 0

:failed
echo.
echo The build failed - the error is above.
pause
exit /b 1
