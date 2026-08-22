"""stats.nba.com client -- OPTIONAL enrichment, never a dependency.

This endpoint is bot-gated, not IP-blocked. With the full browser header set below a
request succeeds; measured behaviour is that it then stonewalls the same IP, hanging
with a 0-byte response for minutes. It is therefore used for at most a handful of
single-shot calls per night, never in a loop and never in the request path.

What it buys us: official OFF_RATING / DEF_RATING / PACE / TS_PCT, versus ESPN where
we derive defensive rating ourselves from game logs. Strictly an upgrade when it works,
and completely absent from the critical path when it does not.
"""

from __future__ import annotations

import logging
from typing import Any

import httpx

from app.providers.http import ProviderError, get_json

log = logging.getLogger(__name__)

BASE = "https://stats.nba.com/stats"

# The full set nba_api sends. Dropping any of these reliably produces the 0-byte hang.
HEADERS = {
    "User-Agent": (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/127.0.0.0 Safari/537.36"
    ),
    "Accept": "application/json, text/plain, */*",
    "Accept-Language": "en-US,en;q=0.9",
    "x-nba-stats-origin": "stats",
    "x-nba-stats-token": "true",
    "Connection": "keep-alive",
    "Referer": "https://stats.nba.com/",
    "Origin": "https://www.nba.com",
    "Sec-Fetch-Site": "same-site",
    "Sec-Fetch-Mode": "cors",
    "Sec-Fetch-Dest": "empty",
}

# Long read timeout: a throttled request hangs rather than refusing.
TIMEOUT = httpx.Timeout(35.0, connect=10.0)

MEASURE_TYPES = ("Advanced", "Four Factors", "Opponent")


def season_string(season: int) -> str:
    """ESPN-style end year to NBA-style span: 2026 -> '2025-26'."""
    return f"{season - 1}-{str(season)[2:]}"


def league_dash_team_stats(
    season: int, measure_type: str = "Advanced", season_type: str = "Regular Season"
) -> list[dict[str, Any]]:
    """One team-level table. Raises ProviderError when throttled -- callers must tolerate it."""
    if measure_type not in MEASURE_TYPES:
        raise ValueError(f"unsupported measure type {measure_type!r}")

    params = {
        "Conference": "", "DateFrom": "", "DateTo": "", "Division": "", "GameScope": "",
        "GameSegment": "", "Height": "", "ISTRound": "", "LastNGames": 0, "LeagueID": "00",
        "Location": "", "MeasureType": measure_type, "Month": 0, "OpponentTeamID": 0,
        "Outcome": "", "PORound": 0, "PaceAdjust": "N", "PerMode": "PerGame", "Period": 0,
        "PlayerExperience": "", "PlayerPosition": "", "PlusMinus": "N", "Rank": "N",
        "Season": season_string(season), "SeasonSegment": "", "SeasonType": season_type,
        "ShotClockRange": "", "StarterBench": "", "TeamID": 0, "TwoWay": 0,
        "VsConference": "", "VsDivision": "",
    }

    # Only one retry: repeated hammering is exactly what triggers the throttle.
    data = get_json(
        f"{BASE}/leaguedashteamstats",
        params=params,
        headers=HEADERS,
        retries=1,
        backoff=4.0,
        timeout=TIMEOUT,
    )

    result_sets = data.get("resultSets") or []
    if not result_sets:
        raise ProviderError("stats.nba.com returned no resultSets")

    headers = result_sets[0]["headers"]
    rows = result_sets[0]["rowSet"]
    return [dict(zip(headers, row, strict=True)) for row in rows]
