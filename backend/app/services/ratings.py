"""Single accessor for team ratings, with an explicit source resolution order.

Everything downstream reads ratings through here. That is deliberate: it makes it
impossible for the model to quietly come to depend on stats.nba.com, which is
optional and unreliable. If the enriched row is missing or stale, the ESPN-derived
row is used and nothing else changes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Sport, StatSource, Team, TeamSeasonStats

log = logging.getLogger(__name__)

# League baselines, used to normalise matchups and as a fallback when a team has no data.
NBA_LEAGUE_AVG_RATING = 113.0
NBA_LEAGUE_AVG_PACE = 99.0

# Shrinkage constant: with K games played, a team's rating is weighted half to the
# current season and half to the prior. Ten games is roughly where NBA team ratings
# start carrying real signal.
SHRINKAGE_K = 10.0


@dataclass(frozen=True)
class TeamRating:
    team_id: int
    season: int
    games_played: int
    off_rating: float
    def_rating: float
    pace: float
    source: StatSource
    # How much weight the current season carried after shrinkage; 1.0 means no blending.
    current_weight: float = 1.0

    @property
    def blended(self) -> bool:
        return self.current_weight < 0.999


def _row_for(
    session: Session, team_id: int, season: int, source: StatSource
) -> TeamSeasonStats | None:
    return session.scalar(
        select(TeamSeasonStats).where(
            TeamSeasonStats.team_id == team_id,
            TeamSeasonStats.season == season,
            TeamSeasonStats.source == source,
        )
    )


def _is_fresh(row: TeamSeasonStats, max_age_days: int) -> bool:
    fetched = row.fetched_at
    if fetched is None:
        return False
    if fetched.tzinfo is None:
        fetched = fetched.replace(tzinfo=UTC)
    return datetime.now(UTC) - fetched <= timedelta(days=max_age_days)


def _usable(row: TeamSeasonStats | None) -> bool:
    return bool(row and row.off_rating and row.def_rating and row.pace)


def raw_rating(session: Session, team_id: int, season: int) -> TeamRating | None:
    """Ratings for one team-season, preferring stats.nba.com only when it is fresh and complete."""
    settings = get_settings()

    if settings.enable_nba_stats_enrich:
        enriched = _row_for(session, team_id, season, StatSource.NBA_STATS)
        if _usable(enriched) and _is_fresh(enriched, settings.nba_stats_max_age_days):
            return TeamRating(
                team_id=team_id,
                season=season,
                games_played=enriched.games_played or 0,
                off_rating=enriched.off_rating,
                def_rating=enriched.def_rating,
                pace=enriched.pace,
                source=StatSource.NBA_STATS,
            )

    espn = _row_for(session, team_id, season, StatSource.ESPN)
    if _usable(espn):
        return TeamRating(
            team_id=team_id,
            season=season,
            games_played=espn.games_played or 0,
            off_rating=espn.off_rating,
            def_rating=espn.def_rating,
            pace=espn.pace,
            source=StatSource.ESPN,
        )
    return None


def team_rating(
    session: Session, team_id: int, season: int, *, prior_season: int | None = None
) -> TeamRating | None:
    """Current-season rating, shrunk toward the prior season while the sample is small.

    Early in a season a ten-game sample produces wild ratings. Blending toward last
    season with weight g/(g+K) keeps October projections sane, and the blend decays to
    nothing by midseason. This is the payoff for ingesting both seasons.
    """
    current = raw_rating(session, team_id, season)
    prior_season = prior_season if prior_season is not None else season - 1
    prior = raw_rating(session, team_id, prior_season)

    if current is None:
        # No current data at all: fall back to last season outright, flagged as fully blended.
        if prior is None:
            return None
        return TeamRating(
            team_id=team_id,
            season=season,
            games_played=0,
            off_rating=prior.off_rating,
            def_rating=prior.def_rating,
            pace=prior.pace,
            source=prior.source,
            current_weight=0.0,
        )

    if prior is None or current.games_played >= 40:
        return current

    g = float(current.games_played)
    w = g / (g + SHRINKAGE_K)
    return TeamRating(
        team_id=team_id,
        season=season,
        games_played=current.games_played,
        off_rating=w * current.off_rating + (1 - w) * prior.off_rating,
        def_rating=w * current.def_rating + (1 - w) * prior.def_rating,
        pace=w * current.pace + (1 - w) * prior.pace,
        source=current.source,
        current_weight=w,
    )


def league_averages(session: Session, season: int) -> tuple[float, float]:
    """(average rating, average pace) across teams with data, with sane fallbacks."""
    rows = session.scalars(
        select(TeamSeasonStats).where(
            TeamSeasonStats.season == season,
            TeamSeasonStats.sport == Sport.NBA,
            TeamSeasonStats.source == StatSource.ESPN,
        )
    ).all()
    ratings = [r.off_rating for r in rows if r.off_rating]
    paces = [r.pace for r in rows if r.pace]
    avg_rating = sum(ratings) / len(ratings) if ratings else NBA_LEAGUE_AVG_RATING
    avg_pace = sum(paces) / len(paces) if paces else NBA_LEAGUE_AVG_PACE
    return avg_rating, avg_pace


def all_team_ratings(session: Session, season: int) -> dict[int, TeamRating]:
    teams = session.scalars(select(Team).where(Team.sport == Sport.NBA)).all()
    out: dict[int, TeamRating] = {}
    for team in teams:
        rating = team_rating(session, team.id, season)
        if rating:
            out[team.id] = rating
    return out
