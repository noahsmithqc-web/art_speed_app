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

# Where A.R.T. keeps its settings. The speed tool only ever READS settings.json there, to reuse
# the QC Hub connection the user already set up in A.R.T. (host, port, user, database; the
# password is in Windows Credential Manager under A.R.T.'s entry). It writes nothing there.
ART_APP_DIR_NAME = "ART"
ENV_ART_HOME = "ART_HOME"                     # A.R.T.'s development override of that folder
ENV_QCH_CREDENTIALS = "ART_QCH_CREDENTIALS"   # A.R.T.'s development credentials file
ART_KEYRING_SERVICE = "A.R.T. QCH"            # A.R.T.'s entry in Windows Credential Manager
