"""Tool-wide constants. Adapted from A.R.T.'s config.py (see COPIED_FROM_ART.md)."""

from art_speed import __version__

APP_NAME = "Speed tool"
APP_TITLE = "Intersection Speed tool"
VERSION = __version__

# Time limits (seconds). Same values as A.R.T.
FS_TIMEOUT_S = 10.0          # any single folder check / write (network drives can hang)
QCH_CONNECT_TIMEOUT_S = 5
QCH_CONNECT_ATTEMPTS = 2     # a connect that gets no answer is tried once more
QCH_QUERY_TIMEOUT_S = 15
QCH_PULL_TIMEOUT_S = 30      # each site lookup query

# Longest full path the tool will write (Windows' classic limit is 260).
MAX_PATH_CHARS = 240

# Folder name under %LOCALAPPDATA% that holds the tool's own settings, recent projects list and
# logs. Its own folder: nothing is shared with A.R.T.'s (%LOCALAPPDATA%\ART).
APP_DIR_NAME = "SpeedTool"

# Local web server. 127.0.0.1 only: never reachable from other machines. A.R.T. uses 8765.
HOST = "127.0.0.1"
PORT = 8766

OPEN_PROJECT_CHECK_S = 1.0   # is the open project's folder still there? Runs on every page load.
RECENT_LIMIT = 10            # cards on the Home screen

# Log file rotation: 5 files of 1 MB each.
LOG_MAX_BYTES = 1_000_000
LOG_BACKUPS = 5

# Development-only overrides (never set on teammates' machines).
ENV_HOME = "SPEED_HOME"                         # app-state folder instead of %LOCALAPPDATA%\SpeedTool
ENV_QCH_CREDENTIALS = "SPEED_QCH_CREDENTIALS"   # a development credentials file (key=value lines)

# A.R.T.'s data types, as its project file (######.json) names them. The speed tool writes that
# file only when it creates a new project (the copied wizard), and never stamps a data type.
DATASET_NEAR_MISS = "near miss"
DATASET_TURN_MOVEMENT = "turn movement"
DATASET_TYPES = (DATASET_NEAR_MISS, DATASET_TURN_MOVEMENT)
