"""Entry point for the Cloudera AI Application: serves app.py on the port the platform assigns.

Cloudera AI Applications run a script file (not a shell command) and route traffic to
127.0.0.1:$CDSW_APP_PORT. The script is executed chunk by chunk in an IPython kernel, so
`__file__` is undefined; the working directory is the project root. Dependencies are installed into the
runtime's user site when requirements.txt changes (see ensure_deps.py).
"""

import os
import subprocess
import sys
from pathlib import Path

import ensure_deps

ROOT = Path.cwd()
print(f"root={ROOT} python={sys.version.split()[0]} CDSW_APP_PORT={os.environ.get('CDSW_APP_PORT')}")

ensure_deps.ensure(ROOT)

subprocess.run(
    [
        sys.executable, "-m", "streamlit", "run", str(ROOT / "app.py"),
        "--server.port", os.environ.get("CDSW_APP_PORT", "8090"),
        "--server.address", "127.0.0.1",
        "--server.headless", "true",
        "--server.enableCORS", "false",
        "--server.enableXsrfProtection", "false",
        "--browser.gatherUsageStats", "false",
    ],
    cwd=ROOT,
    check=True,
)
