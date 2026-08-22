"""MLB Stats API client. Free, no key, no rate limit in practice.

Docs are unofficial; every endpoint shape here was verified live against the 2026 season.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from app.providers.http import get_json

BASE = "https://statsapi.mlb.com/api/v1"
SPORT_ID = 1  # MLB


def schedule(game_date: date) -> list[dict[str, Any]]:
    """Games for a date, hydrated with probable pitchers and final scores."""
    data = get_json(
        f"{BASE}/schedule",
        params={
            "sportId": SPORT_ID,
            "date": game_date.isoformat(),
            "hydrate": "probablePitcher(note),team,linescore",
        },
    )
    games: list[dict[str, Any]] = []
    for day in data.get("dates", []):
        games.extend(day.get("games", []))
    return games


def teams(season: int) -> list[dict[str, Any]]:
    data = get_json(f"{BASE}/teams", params={"sportId": SPORT_ID, "season": season})
    return [t for t in data.get("teams", []) if t.get("sport", {}).get("id") == SPORT_ID]


def team_season_stats(team_id: int, season: int) -> dict[str, dict[str, Any]]:
    """Season hitting and pitching splits for one team, keyed by group name."""
    data = get_json(
        f"{BASE}/teams/{team_id}/stats",
        params={
            "stats": "season",
            "group": "hitting,pitching",
            "season": season,
            "sportId": SPORT_ID,
        },
    )
    out: dict[str, dict[str, Any]] = {}
    for block in data.get("stats", []):
        splits = block.get("splits") or []
        if not splits:
            continue
        group = block.get("group", {}).get("displayName")
        if group:
            out[group] = splits[0].get("stat", {})
    return out


def qualified_pitchers(season: int, limit: int = 200) -> list[dict[str, Any]]:
    """Season pitching leaderboard for qualified starters."""
    data = get_json(
        f"{BASE}/stats",
        params={
            "stats": "season",
            "group": "pitching",
            "season": season,
            "sportId": SPORT_ID,
            "playerPool": "qualified",
            "limit": limit,
        },
    )
    splits: list[dict[str, Any]] = []
    for block in data.get("stats", []):
        splits.extend(block.get("splits", []))
    return splits


def all_pitchers(season: int, limit: int = 2000) -> list[dict[str, Any]]:
    """Season line for every pitcher who has thrown, not just qualified starters.

    Needed to rank a team's whole rotation: the qualified leaderboard covers about 54
    pitchers league-wide, while ranking rotations needs all ~215 with real start counts.
    """
    data = get_json(
        f"{BASE}/stats",
        params={
            "stats": "season",
            "group": "pitching",
            "season": season,
            "sportId": SPORT_ID,
            "playerPool": "all",
            "limit": limit,
        },
    )
    splits: list[dict[str, Any]] = []
    for block in data.get("stats", []):
        splits.extend(block.get("splits", []))
    return splits


def pitcher_season_line(player_id: int, season: int) -> dict[str, Any] | None:
    """Season pitching line for one pitcher, qualified or not.

    Needed because many probable starters fall short of the qualified-innings cutoff.
    """
    data = get_json(
        f"{BASE}/people/{player_id}/stats",
        params={"stats": "season", "group": "pitching", "season": season},
    )
    for block in data.get("stats", []):
        splits = block.get("splits") or []
        if splits:
            return splits[0].get("stat", {})
    return None


def pitcher_game_log(player_id: int, season: int) -> list[dict[str, Any]]:
    data = get_json(
        f"{BASE}/people/{player_id}/stats",
        params={"stats": "gameLog", "group": "pitching", "season": season},
    )
    splits: list[dict[str, Any]] = []
    for block in data.get("stats", []):
        splits.extend(block.get("splits", []))
    return splits


def player(player_id: int) -> dict[str, Any] | None:
    data = get_json(f"{BASE}/people/{player_id}")
    people = data.get("people") or []
    return people[0] if people else None


def standings(season: int) -> list[dict[str, Any]]:
    data = get_json(
        f"{BASE}/standings",
        params={"leagueId": "103,104", "season": season, "standingsTypes": "regularSeason"},
    )
    return data.get("records", [])


def current_season() -> int:
    """The season MLB itself considers active."""
    data = get_json(f"{BASE}/seasons/current", params={"sportId": SPORT_ID})
    seasons = data.get("seasons") or []
    if seasons and seasons[0].get("seasonId"):
        return int(seasons[0]["seasonId"])
    raise ValueError("MLB API returned no current season")
