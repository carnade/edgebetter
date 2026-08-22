"""First-five-innings projection.

F5 is the market where this tool's pitching thesis should be strongest. It settles
before the bullpen appears, which removes exactly the variance that made the full-game
MLB model useless (team rate stats showed no skill there because relief innings and
late-inning randomness swamp the signal).

The run environment is measured, not assumed: across 1,910 games in 2026, F5 averaged
4.99 total runs (home 2.62, away 2.38, SD 3.25) against ~8.9 for the full game -- 56%,
close to the naive 5/9 but derived rather than guessed.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Game, GameInnings, Sport, StatSource, TeamSeasonStats
from app.services.projections_mlb import (
    LEAGUE_ERA,
    PitcherInput,
    PITCHER_INDEX_DAMPING,
    _pitcher_input,
    prob_home_wins,
    prob_total_over,
)

# Measured league F5 environment; refreshed by `league_f5_environment`.
DEFAULT_F5_TOTAL = 4.99
DEFAULT_F5_HOME = 2.62
DEFAULT_F5_AWAY = 2.38

# Innings the starter is assumed to cover of the first five. Starters who get pulled
# early hand the rest to the bullpen even inside F5, so this is not automatically 5.
F5_INNINGS = 5.0


@dataclass(frozen=True)
class F5Projection:
    home_runs: float
    away_runs: float
    home_pitcher: PitcherInput | None
    away_pitcher: PitcherInput | None
    starter_share_home: float
    starter_share_away: float

    @property
    def total(self) -> float:
        return self.home_runs + self.away_runs

    @property
    def margin(self) -> float:
        return self.home_runs - self.away_runs

    def prob_over(self, line: float) -> float:
        return prob_total_over(self.home_runs, self.away_runs, line)

    def prob_home_win(self) -> float:
        """F5 moneyline. Unlike a full game this market can tie, and books settle a
        tie as a push, so this is P(home wins | not tied)."""
        return prob_home_wins(self.home_runs, self.away_runs)


def league_f5_environment(session: Session, season: int) -> tuple[float, float, float]:
    """(total, home, away) average runs through five innings, measured from stored data."""
    row = session.execute(
        select(
            func.avg(GameInnings.home_runs + GameInnings.away_runs),
            func.avg(GameInnings.home_runs),
            func.avg(GameInnings.away_runs),
        )
        .select_from(GameInnings)
        .join(Game, Game.id == GameInnings.game_id)
        .where(GameInnings.inning <= 5, Game.season == season, Game.sport == Sport.MLB)
    ).one()
    # Averages above are per inning; scale to five innings.
    if row[0] is None:
        return DEFAULT_F5_TOTAL, DEFAULT_F5_HOME, DEFAULT_F5_AWAY
    return float(row[0]) * 5.0, float(row[1]) * 5.0, float(row[2]) * 5.0


def _starter_share(pitcher: PitcherInput | None) -> float:
    """Fraction of the first five innings the announced starter is expected to cover."""
    if pitcher is None or not pitcher.innings_per_start:
        return 0.85
    return max(0.4, min(1.0, pitcher.innings_per_start / F5_INNINGS))


def _f5_runs_allowed_index(pitcher: PitcherInput | None, team_era: float | None) -> float:
    """How much the opposing side concedes over five innings.

    Weighted toward the starter far more heavily than the full-game model, because
    that is the whole point of this market.
    """
    bullpen = team_era if team_era else LEAGUE_ERA
    if pitcher is None:
        return bullpen / LEAGUE_ERA

    share = _starter_share(pitcher)
    blended = pitcher.regressed_era * share + bullpen * (1.0 - share)
    index = blended / LEAGUE_ERA
    return 1.0 + (index - 1.0) * PITCHER_INDEX_DAMPING


def project(
    session: Session,
    home_team_id: int,
    away_team_id: int,
    season: int,
    *,
    home_pitcher_id: int | None = None,
    away_pitcher_id: int | None = None,
) -> F5Projection | None:
    home_stats = _team_stats(session, home_team_id, season)
    away_stats = _team_stats(session, away_team_id, season)
    if not home_stats or not away_stats:
        return None
    if not (home_stats.runs_for and home_stats.games_played):
        return None
    if not (away_stats.runs_for and away_stats.games_played):
        return None

    total_env, home_env, away_env = league_f5_environment(session, season)
    league_rpg_full = sum(
        r.runs_for / r.games_played
        for r in (home_stats, away_stats)
        if r.runs_for and r.games_played
    ) / 2.0 or 4.5

    home_p = _pitcher_input(session, home_pitcher_id, season)
    away_p = _pitcher_input(session, away_pitcher_id, season)

    # Offence indexed against the league, then scaled into the F5 environment.
    home_index = (home_stats.runs_for / home_stats.games_played) / league_rpg_full
    away_index = (away_stats.runs_for / away_stats.games_played) / league_rpg_full

    home_runs = home_env * home_index * _f5_runs_allowed_index(away_p, away_stats.team_era)
    away_runs = away_env * away_index * _f5_runs_allowed_index(home_p, home_stats.team_era)

    return F5Projection(
        home_runs=home_runs,
        away_runs=away_runs,
        home_pitcher=home_p,
        away_pitcher=away_p,
        starter_share_home=_starter_share(home_p),
        starter_share_away=_starter_share(away_p),
    )


def _team_stats(session: Session, team_id: int, season: int) -> TeamSeasonStats | None:
    return session.scalar(
        select(TeamSeasonStats).where(
            TeamSeasonStats.team_id == team_id,
            TeamSeasonStats.season == season,
            TeamSeasonStats.source == StatSource.MLB_STATSAPI,
        )
    )
