"""MLB game projection: expected runs, then a Poisson distribution over them.

Runs allowed are split between the announced starter and the bullpen, weighted by how
deep that starter typically goes. Rate stats are regressed toward league average by
sample size, because a 40-inning ERA is mostly noise.

Caveat carried in code deliberately: real run distributions are overdispersed relative
to Poisson (blowouts happen more than Poisson allows), so tail probabilities here are
slightly understated. A negative binomial would fit better and belongs behind the same
interface -- see `total_distribution`.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PitcherSeasonStats, Sport, StatSource, TeamSeasonStats

# Typical MLB run environment; used as a fallback and as the regression target.
LEAGUE_RUNS_PER_GAME = 4.5
LEAGUE_ERA = 4.20

# Innings of regression applied to a pitcher's ERA. Roughly the point at which ERA
# starts carrying as much signal as the league prior.
PITCHER_REGRESSION_INNINGS = 60.0
# Innings a starter is assumed to cover when their own rate is unknown or unusable.
DEFAULT_STARTER_INNINGS = 5.2
INNINGS_PER_GAME = 9.0

# A start almost never covers fewer than 3 or more than 8 innings in the modern game.
MIN_STARTER_INNINGS = 3.0
MAX_STARTER_INNINGS = 8.0
# Below this share of appearances-as-starts, season innings are mostly relief work and
# dividing them by starts is meaningless -- a swingman with 68 IP and 2 starts would
# otherwise look like a 34-inning complete-game pitcher and erase the bullpen entirely.
MIN_START_SHARE = 0.7


def innings_per_start(
    innings_pitched: float | None, games_started: int | None, games_pitched: int | None = None
) -> float | None:
    """Average innings in a start, or None when the season line cannot support one."""
    if not innings_pitched or not games_started or games_started <= 0:
        return None
    if games_pitched and games_started / games_pitched < MIN_START_SHARE:
        return None
    value = innings_pitched / games_started
    if value < MIN_STARTER_INNINGS or value > MAX_STARTER_INNINGS:
        return None
    return value

# Home advantage, calibrated against the 2026 season: the walk-forward backtest
# measured a 0.519 home win rate, and the original 1.04/0.96 split implied 0.545.
HOME_RUN_MULTIPLIER = 1.02
AWAY_RUN_MULTIPLIER = 0.98

# How much of a starter's apparent quality to actually believe.
#
# Without damping, scaling a team's offence directly by the opposing starter's ERA
# index produced home win probabilities up to 0.785 -- well outside the range books
# ever price (real MLB moneylines top out near 0.70). ERA differences do not translate
# one-for-one into run suppression: ERA is noisy, and the opposing offence, park, and
# defence all sit between the pitcher and the scoreboard.
#
# This is a calibration constant, not a fitted one. The team-level backtest cannot
# measure it, because the stored pitcher lines are season-to-date and would leak the
# future if applied to past games. Deriving it properly needs as-of-date pitcher
# snapshots, which is the right next step for this model.
PITCHER_INDEX_DAMPING = 0.55

MAX_RUNS = 30  # Poisson support cap for convolution.


@dataclass(frozen=True)
class PitcherInput:
    name: str
    era: float | None
    innings_pitched: float | None
    innings_per_start: float | None
    k_per_9: float | None = None
    whip: float | None = None

    @property
    def regressed_era(self) -> float:
        """ERA shrunk toward league average by innings pitched."""
        if self.era is None or self.innings_pitched is None:
            return LEAGUE_ERA
        ip = max(self.innings_pitched, 0.0)
        w = ip / (ip + PITCHER_REGRESSION_INNINGS)
        return w * self.era + (1 - w) * LEAGUE_ERA


@dataclass(frozen=True)
class MlbProjection:
    home_runs: float
    away_runs: float
    home_pitcher: PitcherInput | None
    away_pitcher: PitcherInput | None

    @property
    def total(self) -> float:
        return self.home_runs + self.away_runs

    @property
    def margin(self) -> float:
        return self.home_runs - self.away_runs

    def prob_over(self, line: float) -> float:
        return prob_total_over(self.home_runs, self.away_runs, line)

    def prob_home_win(self) -> float:
        return prob_home_wins(self.home_runs, self.away_runs)

    def prob_team_over(self, line: float, *, home: bool) -> float:
        return prob_team_over(self.home_runs if home else self.away_runs, line)


# ------------------------------------------------------------------ distributions
def _poisson_pmf(k: int, lam: float) -> float:
    if lam <= 0:
        return 1.0 if k == 0 else 0.0
    return math.exp(-lam + k * math.log(lam) - math.lgamma(k + 1))


def _poisson_vector(lam: float, cap: int = MAX_RUNS) -> list[float]:
    return [_poisson_pmf(k, lam) for k in range(cap + 1)]


def total_distribution(home_lambda: float, away_lambda: float, cap: int = MAX_RUNS) -> list[float]:
    """Distribution of combined runs, by convolving two independent Poissons.

    The sum of independent Poissons is itself Poisson, so this could be a one-liner --
    it is written as an explicit convolution so a switch to a negative binomial (which
    does not have that property) is a local change.
    """
    home = _poisson_vector(home_lambda, cap)
    away = _poisson_vector(away_lambda, cap)
    totals = [0.0] * (2 * cap + 1)
    for i, ph in enumerate(home):
        if ph < 1e-12:
            continue
        for j, pa in enumerate(away):
            totals[i + j] += ph * pa
    return totals


def prob_total_over(home_lambda: float, away_lambda: float, line: float) -> float:
    """P(total runs > line). A whole-number line can push; that mass is excluded."""
    totals = total_distribution(home_lambda, away_lambda)
    return sum(p for runs, p in enumerate(totals) if runs > line)


def prob_team_over(team_lambda: float, line: float) -> float:
    """P(one team's runs > line).

    The game model already produces each side's expected runs separately and then
    collapses them into a total; the team-totals market consumes that per-side number
    directly, so this needs no new modelling -- only the marginal instead of the sum.
    """
    return sum(p for runs, p in enumerate(_poisson_vector(team_lambda)) if runs > line)


def prob_home_wins(home_lambda: float, away_lambda: float) -> float:
    """P(home wins), renormalised to exclude ties -- baseball has no draws."""
    home = _poisson_vector(home_lambda)
    away = _poisson_vector(away_lambda)
    home_win = tie = 0.0
    for i, ph in enumerate(home):
        for j, pa in enumerate(away):
            joint = ph * pa
            if i > j:
                home_win += joint
            elif i == j:
                tie += joint
    non_tie = 1.0 - tie
    return home_win / non_tie if non_tie > 0 else 0.5


# ---------------------------------------------------------------------- projection
def _runs_allowed_index(pitcher: PitcherInput | None, team_era: float | None) -> float:
    """How many runs the opposing defence concedes relative to league average.

    The starter covers the innings they typically pitch; the bullpen (approximated by
    the team's overall ERA) covers the rest.
    """
    bullpen_era = team_era if team_era else LEAGUE_ERA

    if pitcher is None:
        return bullpen_era / LEAGUE_ERA

    starter_innings = pitcher.innings_per_start or DEFAULT_STARTER_INNINGS
    starter_innings = min(max(starter_innings, 1.0), INNINGS_PER_GAME)
    relief_innings = INNINGS_PER_GAME - starter_innings

    blended = (
        pitcher.regressed_era * starter_innings + bullpen_era * relief_innings
    ) / INNINGS_PER_GAME
    index = blended / LEAGUE_ERA
    # Shrink toward 1.0 (league-average run prevention).
    return 1.0 + (index - 1.0) * PITCHER_INDEX_DAMPING


def project_runs(
    *,
    home_offense_rpg: float,
    away_offense_rpg: float,
    home_pitcher: PitcherInput | None,
    away_pitcher: PitcherInput | None,
    home_team_era: float | None,
    away_team_era: float | None,
    league_rpg: float = LEAGUE_RUNS_PER_GAME,
) -> tuple[float, float]:
    """Expected runs for (home, away).

    Each side's offence is scaled by how the opposing pitching staff compares to league
    average, then nudged by home-field advantage.
    """
    home_off_index = home_offense_rpg / league_rpg if league_rpg else 1.0
    away_off_index = away_offense_rpg / league_rpg if league_rpg else 1.0

    # The home team bats against the away team's pitching, and vice versa.
    home_runs = league_rpg * home_off_index * _runs_allowed_index(away_pitcher, away_team_era)
    away_runs = league_rpg * away_off_index * _runs_allowed_index(home_pitcher, home_team_era)

    return home_runs * HOME_RUN_MULTIPLIER, away_runs * AWAY_RUN_MULTIPLIER


def _pitcher_input(session: Session, player_id: int | None, season: int) -> PitcherInput | None:
    if not player_id:
        return None
    row = session.scalar(
        select(PitcherSeasonStats).where(
            PitcherSeasonStats.player_id == player_id, PitcherSeasonStats.season == season
        )
    )
    if row is None:
        return None
    raw = row.raw or {}
    ip_per_start = innings_per_start(
        row.innings_pitched, row.games_started, _as_int(raw.get("gamesPitched"))
    )
    return PitcherInput(
        name="",
        era=row.era,
        innings_pitched=row.innings_pitched,
        innings_per_start=ip_per_start,
        k_per_9=row.k_per_9,
        whip=row.whip,
    )


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _team_stats(session: Session, team_id: int, season: int) -> TeamSeasonStats | None:
    return session.scalar(
        select(TeamSeasonStats).where(
            TeamSeasonStats.team_id == team_id,
            TeamSeasonStats.season == season,
            TeamSeasonStats.source == StatSource.MLB_STATSAPI,
        )
    )


def league_runs_per_game(session: Session, season: int) -> float:
    rows = session.scalars(
        select(TeamSeasonStats).where(
            TeamSeasonStats.season == season,
            TeamSeasonStats.sport == Sport.MLB,
            TeamSeasonStats.source == StatSource.MLB_STATSAPI,
        )
    ).all()
    values = [
        r.runs_for / r.games_played for r in rows if r.runs_for and r.games_played
    ]
    return sum(values) / len(values) if values else LEAGUE_RUNS_PER_GAME


def project(
    session: Session,
    home_team_id: int,
    away_team_id: int,
    season: int,
    *,
    home_pitcher_id: int | None = None,
    away_pitcher_id: int | None = None,
) -> MlbProjection | None:
    home_stats = _team_stats(session, home_team_id, season)
    away_stats = _team_stats(session, away_team_id, season)
    if not home_stats or not away_stats:
        return None
    if not (home_stats.runs_for and home_stats.games_played):
        return None
    if not (away_stats.runs_for and away_stats.games_played):
        return None

    home_p = _pitcher_input(session, home_pitcher_id, season)
    away_p = _pitcher_input(session, away_pitcher_id, season)

    home_runs, away_runs = project_runs(
        home_offense_rpg=home_stats.runs_for / home_stats.games_played,
        away_offense_rpg=away_stats.runs_for / away_stats.games_played,
        home_pitcher=home_p,
        away_pitcher=away_p,
        home_team_era=home_stats.team_era,
        away_team_era=away_stats.team_era,
        league_rpg=league_runs_per_game(session, season),
    )
    return MlbProjection(
        home_runs=home_runs, away_runs=away_runs, home_pitcher=home_p, away_pitcher=away_p
    )
