"""Match ID collection (SPEC.md section 6) and match download + filter
(SPEC.md section 7).

Phase 1 — collect_match_ids(): for every puuid from scrape_ladder.py's
output, pull its match id history and dedupe. Every lobby has 8 players, so
the same match id arrives up to 8 times; the dedupe ratio is logged. Stops
early once config.yaml's target_match_count is reached (dev sample: 3000;
full run: 25000) — remaining puuids are left unprocessed for a later run to
pick up, not discarded.

Phase 2 — download_matches(): fetch full match JSON for every pending match
id and store it, untouched, in gzipped jsonl shards under data/raw/
(matches_0001.jsonl.gz, ~5000 matches per shard) — SPEC.md section 7. Each
match id's own platform prefix (e.g. "EUW1_...") determines its regional
host directly; no bookkeeping from phase 1 is needed for this.

Phase 3 — filter_matches(): a *separate* step over the already-written raw
shards (SPEC.md section 7 — "ham veri korunsun", filtering never touches
data/raw/). Originally this was meant to filter by tft_set_number,
game_version-derived patch, and queue_id. That changed:

    game_version is USELESS right now. Set 18 is TFT's first Unreal-engine
    set, and every live match sampled (see src/inspect_live.py, ~30 matches
    across two regions/tiers) returns the exact same placeholder string,
    "TFT Unreal Version ?.?.?.?" — no digits, nothing to parse. There is
    also currently only one patch's worth of data obtainable at all (the set
    is 5 days old), so a patch-prefix filter has no discriminating power
    right now anyway. So: filter on tft_set_number + queue_id only (both
    confirmed against live data — see config.yaml). config.yaml no longer
    has a patch_prefix field, and src/patch_filter.py was deleted.

Each kept match's game_datetime is carried into the filtered output
untouched (it's already part of `info`), and the min/max across all kept
matches is logged at the end — this becomes the "collected between X and Y"
line in the README once a real scrape has run (SPEC.md section 15).

Resumable via StateStore (SPEC.md section 3):
  - namespace "puuids_matchid_collected": puuids whose match ids are already fetched
  - namespace "match_ids": pending = collected but not yet downloaded,
    done = downloaded (or confirmed 404) — this is the resumable download queue
"""

from __future__ import annotations

import gzip
import json
import logging
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.env import load_dotenv_if_needed
from src.paths import DATA_PROCESSED, DATA_RAW, PROJECT_ROOT, ensure_dirs
from src.riot_client import PLATFORM_TO_REGIONAL, RiotApiKeyExpiredError, RiotClient
from src.state_store import StateStore

logger = logging.getLogger(__name__)

CONFIG_PATH = PROJECT_ROOT / "config.yaml"
STATE_DB_PATH = DATA_PROCESSED / "scrape_state.db"
PUUIDS_INPUT_PATH = DATA_PROCESSED / "puuids.json"
FILTERED_OUTPUT_PATH = DATA_PROCESSED / "matches_filtered.jsonl.gz"

SHARD_SIZE = 5000
MATCH_IDS_PER_PUUID = 20


def _regional_for_platform(platform: str) -> str:
    regional = PLATFORM_TO_REGIONAL.get(platform)
    if regional is None:
        raise SystemExit(f"No regional host mapping for platform '{platform}' — add it to PLATFORM_TO_REGIONAL")
    return regional


def _regional_for_match_id(match_id: str) -> str:
    platform_prefix = match_id.split("_", 1)[0].lower()
    regional = PLATFORM_TO_REGIONAL.get(platform_prefix)
    if regional is None:
        raise ValueError(f"Unknown platform prefix in match id '{match_id}'")
    return regional


def _load_puuids() -> list[dict]:
    if not PUUIDS_INPUT_PATH.exists():
        raise SystemExit(f"{PUUIDS_INPUT_PATH} not found — run scrape_ladder.py first")
    return json.loads(PUUIDS_INPUT_PATH.read_text(encoding="utf-8"))


def _known_match_id_count(state: StateStore) -> int:
    return len(state.pending_keys("match_ids")) + len(state.done_keys("match_ids"))


def collect_match_ids(
    client: RiotClient,
    state: StateStore,
    puuid_rows: list[dict],
    target_match_count: int | None = None,
) -> None:
    """SPEC.md section 6, phase 1.

    If target_match_count is set, stops pulling more puuids' match histories
    once the total known unique match id count (pending + already downloaded)
    reaches it — a dev-sample run doesn't need to walk the whole puuid list.
    Puuids not yet processed stay unmarked, so a later full run just resumes
    and keeps going through them.
    """
    pending_rows = [row for row in puuid_rows if not state.is_done("puuids_matchid_collected", row["puuid"])]
    logger.info(
        "Match id collection: %d/%d puuids already done, %d remaining",
        len(puuid_rows) - len(pending_rows), len(puuid_rows), len(pending_rows),
    )

    count_before = _known_match_id_count(state)
    total_seen = 0
    processed = 0
    for row in pending_rows:
        if target_match_count is not None and _known_match_id_count(state) >= target_match_count:
            logger.info(
                "target_match_count (%d) reached after %d puuids — stopping match id collection early",
                target_match_count, processed,
            )
            break

        regional = _regional_for_platform(row["platform"])
        match_ids = (
            client.get(
                client.regional_url(regional, f"/tft/match/v1/matches/by-puuid/{row['puuid']}/ids"),
                params={"count": MATCH_IDS_PER_PUUID},
            )
            or []
        )
        total_seen += len(match_ids)
        for match_id in match_ids:
            # add_pending is an INSERT OR IGNORE, so this is safe to call even
            # for ids already pending or done from a prior puuid this run —
            # new_ids is computed from the state count delta below instead of
            # a per-call counter, because is_done() alone can't tell "already
            # pending from this same run" apart from "genuinely new" and would
            # double-count intra-batch duplicates as new.
            state.add_pending("match_ids", match_id)
        state.mark_done("puuids_matchid_collected", row["puuid"])
        processed += 1

    new_ids = _known_match_id_count(state) - count_before
    dedupe_pct = 100 * (1 - new_ids / total_seen) if total_seen else 0.0
    logger.info(
        "Match id collection done: %d puuids processed, %d ids seen, %d unique new, dedupe ratio %.1f%%",
        processed, total_seen, new_ids, dedupe_pct,
    )


def _next_shard_path() -> Path:
    existing = sorted(DATA_RAW.glob("matches_*.jsonl.gz"))
    next_num = 1
    if existing:
        # Path.stem only strips one suffix, so "matches_0001.jsonl.gz".stem is
        # "matches_0001.jsonl" — strip the full ".jsonl.gz" from the name instead.
        num_str = existing[-1].name.removeprefix("matches_").removesuffix(".jsonl.gz")
        next_num = int(num_str) + 1
    return DATA_RAW / f"matches_{next_num:04d}.jsonl.gz"


def _flush_shard(state: StateStore, buffer: list[tuple[str, dict]]) -> None:
    path = _next_shard_path()
    with gzip.open(path, "wt", encoding="utf-8") as f:
        for _match_id, raw in buffer:
            f.write(json.dumps(raw) + "\n")
    for match_id, _raw in buffer:
        state.mark_done("match_ids", match_id)
    logger.info("Wrote shard %s (%d matches)", path, len(buffer))


def download_matches(client: RiotClient, state: StateStore) -> None:
    """SPEC.md section 7, phase 2 (raw download only — no filtering here)."""
    pending = sorted(state.pending_keys("match_ids"))
    logger.info("Match download: %d pending", len(pending))
    if not pending:
        return

    buffer: list[tuple[str, dict]] = []
    skipped_404 = 0
    for match_id in pending:
        regional = _regional_for_match_id(match_id)
        match = client.get(client.regional_url(regional, f"/tft/match/v1/matches/{match_id}"))
        if match is None:
            skipped_404 += 1
            state.mark_done("match_ids", match_id)
            continue
        buffer.append((match_id, match))
        if len(buffer) >= SHARD_SIZE:
            _flush_shard(state, buffer)
            buffer = []

    if buffer:
        _flush_shard(state, buffer)

    logger.info("Match download done: %d skipped as 404", skipped_404)


def filter_matches(target_set_number: int, target_queue_id: int) -> None:
    """SPEC.md section 7, phase 3 — a separate pass over data/raw/, never
    modifies it. Filters on tft_set_number + queue_id only (see module
    docstring for why game_version can't be used)."""
    ensure_dirs()
    shard_paths = sorted(DATA_RAW.glob("matches_*.jsonl.gz"))
    if not shard_paths:
        logger.warning("No raw shards found in %s — nothing to filter", DATA_RAW)
        return

    total = 0
    wrong_set = 0
    wrong_queue = 0
    kept = 0
    game_datetimes: list[int] = []

    with gzip.open(FILTERED_OUTPUT_PATH, "wt", encoding="utf-8") as out:
        for shard_path in shard_paths:
            with gzip.open(shard_path, "rt", encoding="utf-8") as f:
                for line in f:
                    total += 1
                    match = json.loads(line)
                    info = match["info"]

                    if info.get("tft_set_number") != target_set_number:
                        wrong_set += 1
                        continue
                    if info.get("queue_id") != target_queue_id:
                        wrong_queue += 1
                        continue

                    kept += 1
                    game_datetime = info.get("game_datetime")
                    if game_datetime is not None:
                        game_datetimes.append(game_datetime)
                    out.write(json.dumps(match) + "\n")

    logger.info(
        "Filtering: %d raw matches -> %d kept (%d wrong tft_set_number, %d wrong queue_id) -> %s",
        total, kept, wrong_set, wrong_queue, FILTERED_OUTPUT_PATH,
    )

    if game_datetimes:
        min_dt = datetime.fromtimestamp(min(game_datetimes) / 1000, tz=timezone.utc)
        max_dt = datetime.fromtimestamp(max(game_datetimes) / 1000, tz=timezone.utc)
        logger.info("Kept matches span %s to %s (UTC)", min_dt.isoformat(), max_dt.isoformat())
    else:
        logger.warning("No matches passed the filter — no date range to report")


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv_if_needed()
    api_key = os.environ.get("RIOT_API_KEY")
    if not api_key:
        raise SystemExit("RIOT_API_KEY not set (checked environment and .env)")

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    ensure_dirs()
    puuid_rows = _load_puuids()

    client = RiotClient(api_key)
    state = StateStore(STATE_DB_PATH)
    try:
        collect_match_ids(client, state, puuid_rows, target_match_count=config.get("target_match_count"))
        download_matches(client, state)
    except RiotApiKeyExpiredError:
        logger.error("API key expired mid-scrape. Progress is saved in %s — rerun after refreshing RIOT_API_KEY.", STATE_DB_PATH)
        raise
    finally:
        client.close()
        state.close()

    filter_matches(config["set_number"], config["queue_id"])


if __name__ == "__main__":
    main()
