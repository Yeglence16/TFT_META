"""Tiny .env loader — no python-dotenv dependency for one env var.

RIOT_API_KEY is read from the environment per SPEC.md section 1; this only
falls back to parsing .env directly so scripts work without the caller having
exported it manually first.
"""

from __future__ import annotations

import os

from src.paths import PROJECT_ROOT

ENV_PATH = PROJECT_ROOT / ".env"


def load_dotenv_if_needed() -> None:
    if "RIOT_API_KEY" in os.environ or not ENV_PATH.exists():
        return
    for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        os.environ.setdefault(key.strip(), value.strip())
