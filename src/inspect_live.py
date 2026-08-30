"""One-off diagnostic: fetch a handful of real matches and print the raw
values of tft_set_number, game_version and queue_id.

This exists because config.yaml's set_number and queue_id must not be guessed
(see SPEC.md section 7) — run this script and fill config.yaml in from what
it actually prints.

Not part of the scrape pipeline (scrape_ladder.py / scrape_matches.py,
SPEC.md sections 5-7) — this is a manual, throwaway inspection tool: ladder ->
a few PUUIDs -> one match per PUUID -> print the fields, nothing is saved.

Usage:
    python src/inspect_live.py
"""

from __future__ import annotations

import os
import sys
from pathlib import Path

import yaml

sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from src.env import load_dotenv_if_needed
from src.paths import PROJECT_ROOT
from src.riot_client import PLATFORM_TO_REGIONAL, RiotApiKeyExpiredError, RiotClient

CONFIG_PATH = PROJECT_ROOT / "config.yaml"

SAMPLE_SIZE = 5

# Tried in order per platform — some tiers (esp. challenger) can be sparse or
# empty depending on the key/region, so fall back down the ladder.
LEAGUE_TIERS = ("challenger", "grandmaster", "master")


def main() -> None:
    # print() below can hit non-ASCII text (e.g. "— nothing to show"); stdout
    # defaults to the OS locale encoding (cp1254 here), which produces
    # invalid UTF-8 once output is redirected or piped (same root cause as
    # tests/test_encoding.py).
    sys.stdout.reconfigure(encoding="utf-8")
    load_dotenv_if_needed()
    api_key = os.environ.get("RIOT_API_KEY")
    if not api_key:
        raise SystemExit("RIOT_API_KEY not set (checked environment and .env)")

    config = yaml.safe_load(CONFIG_PATH.read_text(encoding="utf-8"))
    platform = config["regions"][0]
    regional = PLATFORM_TO_REGIONAL.get(platform)
    if regional is None:
        raise SystemExit(
            f"No regional host mapping for platform '{platform}' — add it to "
            "PLATFORM_TO_REGIONAL in src/riot_client.py."
        )

    client = RiotClient(api_key)
    seen = 0
    try:
        entries: list = []
        for tier in LEAGUE_TIERS:
            ladder = client.get(client.platform_url(platform, f"/tft/league/v1/{tier}"))
            tier_entries = (ladder or {}).get("entries", [])
            print(f"[{platform}] {tier}: {len(tier_entries)} entries")
            if tier_entries:
                entries = tier_entries[:SAMPLE_SIZE]
                break

        if not entries:
            raise SystemExit(
                f"No entries in challenger/grandmaster/master on '{platform}' — "
                "try a different region in config.yaml."
            )

        for entry in entries:
            puuid = entry.get("puuid")
            if not puuid:
                summoner_id = entry["summonerId"]
                summoner = client.get(
                    client.platform_url(platform, f"/tft/summoner/v1/summoners/{summoner_id}")
                )
                if summoner is None:
                    continue
                puuid = summoner["puuid"]

            match_ids = client.get(
                client.regional_url(regional, f"/tft/match/v1/matches/by-puuid/{puuid}/ids"),
                params={"count": 1},
            )
            if not match_ids:
                continue

            match = client.get(client.regional_url(regional, f"/tft/match/v1/matches/{match_ids[0]}"))
            if match is None:
                continue

            info = match["info"]
            print(f"match_id={match_ids[0]}")
            print(f"  tft_set_number = {info.get('tft_set_number')!r}")
            print(f"  game_version   = {info.get('game_version')!r}")
            print(f"  queue_id       = {info.get('queue_id')!r}")
            seen += 1
    except RiotApiKeyExpiredError as exc:
        raise SystemExit(f"API key expired mid-inspection: {exc}")
    finally:
        client.close()

    if seen == 0:
        print("No matches retrieved — nothing to show.")


if __name__ == "__main__":
    main()
