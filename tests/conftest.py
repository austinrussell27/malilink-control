import os
import sys
import tempfile
from pathlib import Path

# One environment for every in-process test (settings are read once at import time).
os.environ.setdefault("MALILINK_DATA_DIR", tempfile.mkdtemp())
os.environ.setdefault("MALILINK_DEMO", "1")
os.environ.setdefault("MALILINK_DRY_RUN", "1")
sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
