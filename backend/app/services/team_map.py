"""Load, seed, and resolve the cross-source team crosswalk.

Three upstreams name teams three different ways. A silent mismatch here surfaces as
"no games found" and is miserable to debug, so unknown names raise instead of
returning None.
"""

from __future__ import annotations

import logging
from functools import lru_cache
from pathlib import Path

import yaml
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Sport, Team

log = logging.getLogger(__name__)

# data/ is mounted at /srv/data in the container and sits at ../data in a source checkout.
_CANDIDATE_PATHS = [
    Path("/srv/data/team_map.yaml"),
    Path(__file__).resolve().parents[3] / "data" / "team_map.yaml",
]


class UnknownTeamError(LookupError):
    """Raised when an upstream returns a team name absent from the crosswalk.

    Deliberately fatal: continuing would silently drop games from the slate.
    """


def _map_path() -> Path:
    for p in _CANDIDATE_PATHS:
        if p.exists():
            return p
    raise FileNotFoundError(f"team_map.yaml not found in any of: {_CANDIDATE_PATHS}")


@lru_cache
def load_team_map() -> dict[str, list[dict]]:
    with _map_path().open() as fh:
        data = yaml.safe_load(fh)
    for sport in ("mlb", "nba"):
        if len(data.get(sport, [])) != 30:
            raise ValueError(f"{sport} crosswalk has {len(data.get(sport, []))} teams, expected 30")
    return data


def normalize(name: str) -> str:
    return " ".join(name.strip().lower().split())


def seed_teams(session: Session) -> int:
    """Upsert every team from the crosswalk. Idempotent -- safe to run on each boot."""
    data = load_team_map()
    written = 0
    for sport_key, rows in (("mlb", data["mlb"]), ("nba", data["nba"])):
        sport = Sport(sport_key)
        for row in rows:
            existing = session.scalar(
                select(Team).where(Team.sport == sport, Team.abbrev == row["abbrev"])
            )
            team = existing or Team(sport=sport, abbrev=row["abbrev"])
            team.display_name = row["display_name"]
            team.odds_api_name = row["odds_api_name"]
            team.mlb_id = row.get("mlb_id")
            team.espn_id = row.get("espn_id")
            team.location = row.get("location") or None
            team.conference = row.get("conference") or None
            team.division = row.get("division") or None
            if existing is None:
                session.add(team)
            written += 1
    session.commit()
    log.info("seeded %d teams from crosswalk", written)
    return written


@lru_cache
def _odds_name_index() -> dict[tuple[str, str], str]:
    """(sport, normalized name or alias) -> abbrev."""
    data = load_team_map()
    index: dict[tuple[str, str], str] = {}
    for sport_key in ("mlb", "nba"):
        for row in data[sport_key]:
            names = {row["odds_api_name"], row["display_name"], *row.get("aliases", [])}
            for name in names:
                index[(sport_key, normalize(name))] = row["abbrev"]
    return index


def abbrev_for_odds_name(sport: Sport, name: str) -> str:
    """Map a name from the odds feed to our abbrev, or raise."""
    try:
        return _odds_name_index()[(sport.value, normalize(name))]
    except KeyError as exc:
        raise UnknownTeamError(
            f"Odds feed returned unmapped {sport.value} team {name!r}. "
            f"Add it to data/team_map.yaml (as odds_api_name or an alias) -- "
            f"ignoring it would silently drop games from the slate."
        ) from exc


def team_by_odds_name(session: Session, sport: Sport, name: str) -> Team:
    abbrev = abbrev_for_odds_name(sport, name)
    team = session.scalar(select(Team).where(Team.sport == sport, Team.abbrev == abbrev))
    if team is None:
        raise UnknownTeamError(f"{sport.value} team {abbrev} in crosswalk but missing from db; reseed")
    return team


def team_by_mlb_id(session: Session, mlb_id: int) -> Team | None:
    return session.scalar(select(Team).where(Team.mlb_id == mlb_id))


def team_by_espn_id(session: Session, espn_id: int) -> Team | None:
    return session.scalar(select(Team).where(Team.sport == Sport.NBA, Team.espn_id == espn_id))
