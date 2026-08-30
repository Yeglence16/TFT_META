"""Ladder scrape: collect PUUIDs to seed match collection (SPEC.md section 5).

Set 18 is ~5 days old at the time this was written: challenger is empty and
grandmaster/master are nearly empty because ranked resets every set. This is
expected, not an API/key problem. So instead of only hitting
challenger/grandmaster/master, this also pages through configured
tier/division brackets (config.yaml `ladder.paginated_tiers` /
`ladder.divisions`, e.g. DIAMOND/EMERALD) via:

    /tft/league/v1/entries/{tier}/{division}?page=N

High tiers are still tried first and included opportunistically — once the
ladder repopulates as the set matures, they'll start returning entries and
get included automatically, no code change needed. An empty high tier is
skipped silently.

No tier/division/page-count is hardcoded — all of it comes from config.yaml
`ladder`, and none of this is set-specific (works for whatever tiers Riot
currently exposes under this key's shard).

Resumable via StateStore (SPEC.md section 3): namespace "ladder_pages" tracks
which (platform, tier, division, page) combos are already fetched, so a
restart after a 403 or a crash doesn't re-page from the start. The full
partial puuid set collected so far is also persisted after every fatal error.

Output: data/processed/puuids.json — a deduped list of
{"puuid", "platform", "tier", "division"}. The platform is kept because
match-id collection (section 6) needs to know which regional host
(europe/americas/asia) a puuid's account routes through; tier/division are
kept purely for reporting (a per-tier/division puuid count, logged at the
end of a run and reconstructible any time by re-grouping this file).
"""

from __future__ import annotations

import json
import logging
import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.env import load_dotenv_if_needed
from src.paths import DATA_PROCESSED, PROJECT_ROOT, ensure_dirs
from src.riot_client import RiotApiKeyExpiredError, RiotClient
from src.state_store import StateStore

logger = logging.getLogger(__name__)

CONFIG_PATH = PROJECT_ROOT / "config.yaml"
STATE_DB_PATH = DATA_PROCESSED / "scrape_state.db"
PUUIDS_OUTPUT_PATH = DATA_PROCESSED / "puuids.json"

HIGH_TIER_ENDPOINTS = {
    "challenger": "/tft/league/v1/challenger",
    "grandmaster": "/tft/league/v1/grandmaster",
    "master": "/tft/league/v1/master",
}


def _entry_puuid(client: RiotClient, platform: str, entry: dict) -> str | None:
    puuid = entry.get("puuid")
    if puuid:
        return puuid
    summoner_id = entry.get("summonerId")
    if not summoner_id:
        return None
    summoner = client.get(client.platform_url(platform, f"/tft/summoner/v1/summoners/{summoner_id}"))
    if summoner is None:
        return None
    return summoner.get("puuid")


def _scrape_high_tier(
    client: RiotClient, platform: str, tier: str, puuid_info: dict[str, dict]
) -> None:
    ladder = client.get(client.platform_url(platform, HIGH_TIER_ENDPOINTS[tier]))
    entries = (ladder or {}).get("entries", [])
    if not entries:
        logger.info("[%s] %s: 0 entries, skipping", platform, tier)
        return

    new = 0
    for entry in entries:
        puuid = _entry_puuid(client, platform, entry)
        if puuid and puuid not in puuid_info:
            puuid_info[puuid] = {"platform": platform, "tier": tier, "division": None}
            new += 1
    logger.info("[%s] %s: %d entries, %d new puuids", platform, tier, len(entries), new)


def _scrape_paginated_tier(
    client: RiotClient,
    state: StateStore,
    platform: str,
    tier: str,
    division: str,
    max_pages: int,
    puuid_info: dict[str, dict],
) -> None:
    for page in range(1, max_pages + 1):
        page_key = f"{platform}:{tier}:{division}:{page}"
        if state.is_done("ladder_pages", page_key):
            continue

        entries = (
            client.get(
                client.platform_url(platform, f"/tft/league/v1/entries/{tier}/{division}"),
                params={"page": page},
            )
            or []
        )

        if not entries:
            state.mark_done("ladder_pages", page_key)
            logger.info("[%s] %s %s page %d: empty, stopping", platform, tier, division, page)
            break

        new = 0
        for entry in entries:
            puuid = _entry_puuid(client, platform, entry)
            if puuid and puuid not in puuid_info:
                puuid_info[puuid] = {"platform": platform, "tier": tier, "division": division}
                new += 1

        state.mark_done("ladder_pages", page_key)
        logger.info(
            "[%s] %s %s page %d: %d entries, %d new puuids",
            platform, tier, division, page, len(entries), new,
        )


def _write_puuids(puuid_info: dict[str, dict]) -> None:
    rows = [
        {"puuid": puuid, **info}
        for puuid, info in sorted(puuid_info.items())
    ]
    PUUIDS_OUTPUT_PATH.write_text(json.dumps(rows, indent=2), encoding="utf-8")


def _load_existing_puuids() -> dict[str, dict]:
    """Loads a previous puuids.json. Tolerates the older {"puuid", "platform"}
    schema (no tier/division) from before this reporting was added — those
    rows are labeled tier "unknown" rather than crashing the summary sort."""
    if not PUUIDS_OUTPUT_PATH.exists():
        return {}
    rows = json.loads(PUUIDS_OUTPUT_PATH.read_text(encoding="utf-8"))
    return {
        row["puuid"]: {
            "platform": row["platform"],
            "tier": row.get("tier", "unknown"),
            "division": row.get("division"),
        }
        for row in rows
    }


def log_tier_summary(puuid_info: dict[str, dict]) -> None:
    counts: dict[str, int] = {}
    for info in puuid_info.values():
        key = info["tier"] if info["division"] is None else f"{info['tier']} {info['division']}"
        counts[key] = counts.get(key, 0) + 1

    logger.info("PUUID counts by tier/division (%d total):", len(puuid_info))
    for key, count in sorted(counts.items()):
        logger.info("  %-20s %d", key, count)


def run(config: dict) -> dict[str, dict]:
    ensure_dirs()
    ladder_cfg = config["ladder"]
    high_tiers = ladder_cfg.get("high_tiers", [])
    paginated_tiers = ladder_cfg.get("paginated_tiers", [])
    divisions = ladder_cfg.get("divisions", [])
    max_pages = ladder_cfg.get("max_pages_per_division", 50)

    api_key = os.environ.get("RIOT_API_KEY")
    if not api_key:
        raise SystemExit("RIOT_API_KEY not set (checked environment and .env)")

    puuid_info = _load_existing_puuids()
    if puuid_info:
        logger.info("Resuming: %d puuids already known from %s", len(puuid_info), PUUIDS_OUTPUT_PATH)

    client = RiotClient(api_key)
    state = StateStore(STATE_DB_PATH)
    try:
        for platform in config["regions"]:
            for tier in high_tiers:
                if tier not in HIGH_TIER_ENDPOINTS:
                    logger.warning("Unknown high tier '%s' in config.ladder.high_tiers, skipping", tier)
                    continue
                _scrape_high_tier(client, platform, tier, puuid_info)

            for tier in paginated_tiers:
                for division in divisions:
                    _scrape_paginated_tier(client, state, platform, tier, division, max_pages, puuid_info)
    except RiotApiKeyExpiredError:
        logger.error(
            "API key expired mid-scrape. Saving %d puuids collected so far to %s — "
            "refresh RIOT_API_KEY and rerun to resume.",
            len(puuid_info), PUUIDS_OUTPUT_PATH,
        )
        _write_puuids(puuid_info)
        raise
    finally:
        client.close()
        state.close()

    _write_puuids(puuid_info)
    logger.info("Total unique puuids: %d -> %s", len(puuid_info), PUUIDS_OUTPUT_PATH)
    log_tier_summary(puuid_info)
    return puuid_info


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    load_dotenv_if_needed()
    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    run(config)


if __name__ == "__main__":
    main()
