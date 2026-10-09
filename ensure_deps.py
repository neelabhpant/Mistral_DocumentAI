"""Install requirements.txt into the runtime's user site once per (requirements, Python version).

Cloudera AI Applications and Jobs start fresh containers; ~/.local persists with the project, so this only
installs on the first start after requirements.txt changes.
"""

import hashlib
import subprocess
import sys
from pathlib import Path


def ensure(root: Path = Path.cwd()) -> None:
    req = root / "requirements.txt"
    digest = hashlib.sha256(req.read_bytes()).hexdigest()[:12]
    marker = Path.home() / ".local" / f".deps-py{sys.version_info.major}{sys.version_info.minor}-{digest}"
    if marker.exists():
        return
    subprocess.run([sys.executable, "-m", "pip", "install", "--user", "-q", "-r", str(req)], check=True)
    marker.parent.mkdir(parents=True, exist_ok=True)
    marker.touch()
