# PyInstaller spec for the speed tool - the one-folder build the team gets. Adapted from A.R.T.'s
# packaging/ART.spec (see COPIED_FROM_ART.md).
#
#   build_speed.bat   (from the speed_app folder)  ->  dist\\SpeedTool\\SpeedTool.exe
#                                                      dist\\SpeedTool-<version>.zip
#
# One program, with a console: the black window is the app (closing it stops the tool) and the
# screen is a browser tab. Double-clicked it starts the tool; `SpeedTool.exe selfcheck` checks the
# build, and every command of `python -m art_speed` works the same way.
#
# Every entry below exists because something is loaded by name at run time, where PyInstaller's
# import scan cannot see it. If `SpeedTool.exe selfcheck` reports a MISSING line, fix it here.

from pathlib import Path

from PyInstaller.utils.hooks import collect_data_files, collect_submodules, copy_metadata

ROOT = Path(SPECPATH).parent                     # speed_app\
APP = ROOT / "art_speed"

# paths.resource_path() reads <bundle>\art_speed\<...>, so the files keep that layout.
datas = [
    (str(APP / "web"), "art_speed/web"),                 # the page: html, scripts, css, logo
    (str(APP / "reference"), "art_speed/reference"),     # the report template, state_nm_region.csv
]

hiddenimports = []

# uvicorn picks its loop, protocols and log formatter by name.
hiddenimports += collect_submodules("uvicorn")

# keyring finds its backends through package metadata, so the metadata has to ship too.
# On Windows it is keyring.backends.Windows over pywin32-ctypes.
hiddenimports += collect_submodules("keyring.backends")
try:
    hiddenimports += collect_submodules("win32ctypes")
except Exception:                                # not installed off Windows
    pass
datas += copy_metadata("keyring")

# mysql-connector loads its auth plugins by name and carries its error-message locales as data.
hiddenimports += collect_submodules("mysql.connector")
datas += collect_data_files("mysql.connector")

# Its C extension loads auth plugins as DLLs from <folder of _mysql_connector>\mysql\vendor\plugin;
# PyInstaller copies the extension but not that folder. Only the plugins a password login can
# use: the Kerberos, LDAP-SASL, WebAuthn and OCI ones need DLLs nobody has, and QCH uses none.
UNUSED_PLUGINS = ("kerberos", "ldap", "webauthn", "fido", "oci")


def _unused(name) -> bool:
    return any(word in str(name).lower() for word in UNUSED_PLUGINS)


import mysql as _mysql
_plugins = Path(_mysql.__file__).parent / "vendor" / "plugin"
if _plugins.is_dir():
    datas += [(str(f), "mysql/vendor/plugin") for f in _plugins.iterdir()
              if f.is_file() and not _unused(f.name)]

# The folder and file pickers import tkinter inside a function.
hiddenimports += ["tkinter", "tkinter.filedialog"]

# openpyxl and Pillow are not shipped: the report writer fills the template at file level, and
# only the development parity check (art_speed/parity.py) reads workbooks with openpyxl.

a = Analysis(
    [str(ROOT / "packaging" / "speed_entry.py")],
    pathex=[str(ROOT)],
    datas=datas,
    hiddenimports=hiddenimports,
    excludes=["pytest", "httpx", "_pytest", "openpyxl", "PIL"],
    noarchive=False,
)
a.binaries = [b for b in a.binaries if not (_unused(b[0]) and "plugin" in str(b[0]).lower())]
a.datas = [d for d in a.datas if not (_unused(d[0]) and "plugin" in str(d[0]).lower())]

pyz = PYZ(a.pure)

exe = EXE(
    pyz,
    a.scripts,
    [],
    exclude_binaries=True,               # one-folder: the DLLs sit beside SpeedTool.exe
    name="SpeedTool",
    console=True,                        # the black window is the app; closing it stops the tool
    upx=False,
)

coll = COLLECT(
    exe,
    a.binaries,
    a.datas,
    name="SpeedTool",
    upx=False,
)
