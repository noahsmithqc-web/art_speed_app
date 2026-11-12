"""The script PyInstaller freezes into SpeedTool.exe.

Everything lives in art_speed.__main__.packaged_main; this file only exists because PyInstaller
wants a script, not `-m art_speed`.
"""
import sys

from art_speed.__main__ import packaged_main

if __name__ == "__main__":
    sys.exit(packaged_main())
