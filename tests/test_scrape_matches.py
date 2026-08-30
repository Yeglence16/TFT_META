import logging
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import src.scrape_matches as scrape_matches
from src.state_store import StateStore


def test_next_shard_path_with_no_existing_shards(tmp_path, monkeypatch):
    monkeypatch.setattr(scrape_matches, "DATA_RAW", tmp_path)
    assert scrape_matches._next_shard_path() == tmp_path / "matches_0001.jsonl.gz"


def test_next_shard_path_increments_past_existing_shards(tmp_path, monkeypatch):
    monkeypatch.setattr(scrape_matches, "DATA_RAW", tmp_path)
    (tmp_path / "matches_0001.jsonl.gz").write_bytes(b"")
    (tmp_path / "matches_0002.jsonl.gz").write_bytes(b"")

    # Regression: Path.stem only strips one suffix, so
    # "matches_0002.jsonl.gz".stem is "matches_0002.jsonl", not "matches_0002" —
    # a naive split("_")[1] on that crashes with ValueError instead of
    # returning matches_0003.jsonl.gz.
    assert scrape_matches._next_shard_path() == tmp_path / "matches_0003.jsonl.gz"


class _FakeMatchIdClient:
    """Two puuids whose 10-id match histories overlap on 5 ids (0-14 total
    unique across both) — enough to distinguish a correct dedupe count from
    one that double-counts intra-batch duplicates."""

    def __init__(self, id_lists: list[list[str]]) -> None:
        self._id_lists = iter(id_lists)

    def regional_url(self, region: str, path: str) -> str:
        return path

    def get(self, url: str, params: dict | None = None):
        return next(self._id_lists)


def test_collect_match_ids_reports_true_dedupe_not_intra_batch_duplicates(tmp_path, caplog):
    state = StateStore(tmp_path / "state.db")
    client = _FakeMatchIdClient(
        [
            [f"EUW1_{i}" for i in range(10)],       # puuid 1: ids 0-9
            [f"EUW1_{i}" for i in range(5, 15)],     # puuid 2: ids 5-14, 5 overlap with puuid 1
        ]
    )
    puuid_rows = [{"puuid": "p1", "platform": "euw1"}, {"puuid": "p2", "platform": "euw1"}]

    with caplog.at_level(logging.INFO):
        scrape_matches.collect_match_ids(client, state, puuid_rows)

    # Regression: is_done() only excludes ids already marked 'done', not ids
    # already 'pending' from earlier in the same batch — a naive per-call
    # counter double-counts the 5 overlapping ids as "new" a second time.
    # True unique count (0-14) is 15, out of 20 total seen.
    assert state.pending_keys("match_ids") == {f"EUW1_{i}" for i in range(15)}

    done_line = next(r.message for r in caplog.records if "unique new" in r.message)
    assert "20 ids seen, 15 unique new" in done_line

    state.close()
