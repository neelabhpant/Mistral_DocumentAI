"""Entry point for the Cloudera AI Application: serves the web app (app.py) with uvicorn on the port the platform
assigns.

Cloudera AI Applications run a script file (not a shell command) and route traffic to
127.0.0.1:$CDSW_APP_PORT. The script is executed chunk by chunk in an IPython kernel, so
`__file__` is undefined; the working directory is the project root. Dependencies are installed into the
runtime's user site when requirements.txt changes (see ensure_deps.py).

One worker, because the background jobs, the caches and the Impala lock live in that process. No access log, because
the UI polls job status.
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
        sys.executable, "-m", "uvicorn", "app:app",
        "--host", "127.0.0.1",
        "--port", os.environ.get("CDSW_APP_PORT", "8090"),
        "--no-access-log",
    ],
    cwd=ROOT,
    check=True,
)
