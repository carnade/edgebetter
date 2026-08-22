"""ESPN public API client -- the backbone for NBA data.

Free, no key, and (unlike stats.nba.com) not bot-gated. Undocumented, so this module
is kept thin: if ESPN changes shape, the fix is confined here.

Season-year convention: ESPN's `season=2026` means the 2025-26 season.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.providers.http import get_json

SITE = "https://site.api.espn.com/apis/site/v2/sports"
SITE_WEB = "https://site.web.api.espn.com/apis"
CORE = "https://sports.core.api.espn.com/v2/sports"

_PATHS = {"nba": ("basketball", "nba"), "mlb": ("baseball", "mlb")}


def _path(league: str) -> tuple[str, str]:
    try:
        return _PATHS[league]
    except KeyError as exc:
        raise ValueError(f"unsupported league {league!r}") from exc


def teams(league: str = "nba") -> list[dict[str, Any]]:
    sport, lg = _path(league)
    data = get_json(f"{SITE}/{sport}/{lg}/teams")
    entries = data["sports"][0]["leagues"][0]["teams"]
    return [e["team"] for e in entries]


def scoreboard(day: date, league: str = "nba") -> list[dict[str, Any]]:
    """All events for a date, including final scores."""
    sport, lg = _path(league)
    data = get_json(
        f"{SITE}/{sport}/{lg}/scoreboard", params={"dates": day.strftime("%Y%m%d"), "limit": 100}
    )
    return data.get("events", [])


def team_season_statistics(team_espn_id: int, season: int, league: str = "nba") -> dict[str, float]:
    """Flattened season stat map for one team.

    Returns every stat ESPN exposes keyed by its short name, including `possessions`,
    `paceFactor`, `avgPoints`, and shooting splits.
    """
    sport, lg = _path(league)
    data = get_json(
        f"{CORE}/{sport}/leagues/{lg}/seasons/{season}/types/2/teams/{team_espn_id}/statistics"
    )
    out: dict[str, float] = {}
    for category in data.get("splits", {}).get("categories", []):
        for stat in category.get("stats", []):
            name, value = stat.get("name"), stat.get("value")
            if name is not None and isinstance(value, int | float):
                out[name] = float(value)
    return out


def standings(season: int, league: str = "nba") -> dict[str, Any]:
    sport, lg = _path(league)
    return get_json(f"{SITE_WEB}/v2/sports/{sport}/{lg}/standings", params={"season": season})


def current_season(league: str = "nba") -> dict[str, Any]:
    """ESPN's own view of the active season.

    Returns e.g. {"year": 2027, "displayName": "2026-27", "startDate": "2026-09-30..."}.
    Reading this is what lets season rollover happen without a code change.
    """
    sport, lg = _path(league)
    data = get_json(
        f"{SITE_WEB}/common/v3/sports/{sport}/{lg}/statistics/byteam",
        params={"region": "us", "lang": "en", "contentorigin": "espn", "limit": 1},
    )
    return data.get("currentSeason", {})
