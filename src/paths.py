"""Shared filesystem paths.

data/{raw,processed,static}/ and models/ are gitignored, so a fresh clone
does not have them on disk (gitignored .gitkeep files aren't committed
either). Any code that writes into these directories must call ensure_dirs()
first rather than assuming they already exist.
"""

from __future__ import annotations

from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
DATA_RAW = PROJECT_ROOT / "data" / "raw"
DATA_PROCESSED = PROJECT_ROOT / "data" / "processed"
DATA_STATIC = PROJECT_ROOT / "data" / "static"
MODELS_DIR = PROJECT_ROOT / "models"
WEB_DIR = PROJECT_ROOT / "web"
WEB_DATA_DIR = PROJECT_ROOT / "web" / "data"
DIST_DIR = PROJECT_ROOT / "dist"


def ensure_dirs() -> None:
    for path in (DATA_RAW, DATA_PROCESSED, DATA_STATIC, MODELS_DIR):
        path.mkdir(parents=True, exist_ok=True)
