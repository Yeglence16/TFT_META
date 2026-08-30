"""Resumable progress tracking (SPEC.md section 3).

A killed process, an expired dev key, or a reboot must not mean starting a
20-30k match scrape over from scratch. This is a thin SQLite wrapper with one
table, partitioned by an arbitrary `namespace` string so scrape_ladder.py and
scrape_matches.py don't each need their own schema:

    state(namespace, key, status)   status is 'pending' or 'done'

Used for all three things SPEC.md section 3 calls out:
  - "işlenmiş PUUID'ler"     -> namespace "puuids_matchid_collected", mark_done
  - "indirilmiş maç ID'leri" -> namespace "match_ids", mark_done
  - "bekleyen kuyruk"        -> namespace "match_ids", pending_keys()
  - (also used for ladder page progress: namespace "ladder_pages")

Every write commits immediately — there is no batching — so the file on disk
always reflects real progress, even if the process dies mid-run.
"""

from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any


class StateStore:
    def __init__(self, db_path: Path) -> None:
        db_path.parent.mkdir(parents=True, exist_ok=True)
        self._conn = sqlite3.connect(db_path)
        self._conn.execute(
            """
            CREATE TABLE IF NOT EXISTS state (
                namespace TEXT NOT NULL,
                key TEXT NOT NULL,
                status TEXT NOT NULL CHECK (status IN ('pending', 'done')),
                PRIMARY KEY (namespace, key)
            )
            """
        )
        self._conn.commit()

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> "StateStore":
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()

    def add_pending(self, namespace: str, key: str) -> None:
        """No-op if `key` is already pending or already done."""
        self._conn.execute(
            "INSERT OR IGNORE INTO state (namespace, key, status) VALUES (?, ?, 'pending')",
            (namespace, key),
        )
        self._conn.commit()

    def mark_done(self, namespace: str, key: str) -> None:
        self._conn.execute(
            """
            INSERT INTO state (namespace, key, status) VALUES (?, ?, 'done')
            ON CONFLICT (namespace, key) DO UPDATE SET status = 'done'
            """,
            (namespace, key),
        )
        self._conn.commit()

    def is_done(self, namespace: str, key: str) -> bool:
        row = self._conn.execute(
            "SELECT 1 FROM state WHERE namespace = ? AND key = ? AND status = 'done'",
            (namespace, key),
        ).fetchone()
        return row is not None

    def pending_keys(self, namespace: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT key FROM state WHERE namespace = ? AND status = 'pending'",
            (namespace,),
        ).fetchall()
        return {row[0] for row in rows}

    def done_keys(self, namespace: str) -> set[str]:
        rows = self._conn.execute(
            "SELECT key FROM state WHERE namespace = ? AND status = 'done'",
            (namespace,),
        ).fetchall()
        return {row[0] for row in rows}

    def counts(self, namespace: str) -> dict[str, int]:
        rows = self._conn.execute(
            "SELECT status, COUNT(*) FROM state WHERE namespace = ? GROUP BY status",
            (namespace,),
        ).fetchall()
        return dict(rows)
