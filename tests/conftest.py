"""Test setup: redirect DRAGONTAG_* paths to a temp dir before importing the app."""
import os
import tempfile
from pathlib import Path

_tmp = Path(tempfile.mkdtemp(prefix="dragontag-tests-"))
os.environ.setdefault("DRAGONTAG_CONFIG_PATH", str(_tmp / "config"))
os.environ.setdefault("DRAGONTAG_LIBRARY_PATH", str(_tmp / "library"))
os.environ.setdefault("DRAGONTAG_DROP_PATH", str(_tmp / "drop"))
for p in ("config", "library", "drop"):
    (_tmp / p).mkdir(parents=True, exist_ok=True)
