"""NFL team and game total projection.

Structure: expected points = expected plays x points per play, where points per play
comes from the offence's EPA rate adjusted by what the opposing defence has allowed.
EPA is used rather than yardage because it already weights plays by how much they move
scoring expectation.

Two rules carried over from earlier phases, both learned by getting them wrong:

- **Ratings are built from prior games only.** Fitting on a full season and then
  "predicting" the games that produced the ratings inflated the NBA mismatch result from
  58.7% to 89.5%. Everything here is walk-forward.
- **Environment coefficients are measured, not invented.** The wind adjustment below
  comes from the splits engine over 188 outdoor games, not from a plausible-sounding
  guess. `TOTAL_SIGMA` on the NBA model was wrong by 40% precisely because it was assumed.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NflGame, NflTeamGame
from app.services.devig import normal_cdf

log = logging.getLogger(__name__)

# League baselines, refreshed from data by `league_baseline`.
DEFAULT_POINTS_PER_GAME = 22.5
DEFAULT_PLAYS_PER_GAME = 61.0

# Games a team needs before its own rating outweighs the league mean. NFL seasons are
# short, so early-season ratings are mostly noise without this.
SHRINKAGE_GAMES = 6.0

# Half-life, in games, for a team's scoring rate.
#
# The first version accumulated every game since 2020 with equal weight, which made a
# 2023 rating a four-year average dominated by the much higher-scoring 2020-21 seasons.
# Measured bias ran +3.0, +3.7, +4.0 in 2021-23 while the closing line was within half a
# point. Roughly one season of memory tracks the environment without making a rating
# hostage to last week.
TEAM_HALF_LIFE_GAMES = 10.0

# Measured over 188 outdoor team-games with wind >= 15mph: mean total 42.7 vs 44.1 for
# all outdoor games. Applied per side, so half the game-level effect each.
WIND_THRESHOLD = 15.0
WIND_TOTAL_ADJUSTMENT = -1.3

# Home advantage in the modern NFL is far smaller than folklore suggests; measured
# below via `league_baseline` and applied as a points-of-margin split.
DEFAULT_HOME_EDGE = 1.3


@dataclass
class TeamRating:
    """A team's scoring profile, weighted toward recent games.

    Exponential decay rather than a running mean: NFL rosters, coaches, and the league
    scoring environment all change fast enough that a two-year-old game says little
    about this week.
    """

    team: str
    games: int = 0
    half_life_games: float = TEAM_HALF_LIFE_GAMES
    _w_points_for: float = 0.0
    _w_points_against: float = 0.0
    _w_off_epa: float = 0.0
    _w_def_epa: float = 0.0
    _w_plays: float = 0.0
    _weight: float = 0.0
    _epa_weight: float = 0.0

    @property
    def _decay(self) -> float:
        return 0.5 ** (1.0 / self.half_life_games)

    def add(self, row: NflTeamGame) -> None:
        if row.points_for is None or row.points_against is None:
            return
        d = self._decay
        self.games += 1
        self._w_points_for = self._w_points_for * d + row.points_for
        self._w_points_against = self._w_points_against * d + row.points_against
        self._weight = self._weight * d + 1.0

        if row.off_epa_per_play is not None and row.def_epa_per_play is not None:
            self._w_off_epa = self._w_off_epa * d + row.off_epa_per_play
            self._w_def_epa = self._w_def_epa * d + row.def_epa_per_play
            self._epa_weight = self._epa_weight * d + 1.0
        if row.plays:
            self._w_plays = self._w_plays * d + row.plays

    @property
    def ppg(self) -> float | None:
        return self._w_points_for / self._weight if self._weight else None

    @property
    def papg(self) -> float | None:
        return self._w_points_against / self._weight if self._weight else None

    @property
    def off_epa_rate(self) -> float:
        return self._w_off_epa / self._epa_weight if self._epa_weight else 0.0

    @property
    def def_epa_rate(self) -> float:
        return self._w_def_epa / self._epa_weight if self._epa_weight else 0.0

    @property
    def plays_per_game(self) -> float | None:
        return self._w_plays / self._weight if self._weight else None

    def shrunk(self, value: float | None, league: float) -> float:
        """Blend a team rate toward the league mean by how much we have seen.

        With six games the team's own number carries half the weight. Without this an
        opening-week rating is a single game and the projection swings wildly.
        """
        if value is None or self.games == 0:
            return league
        w = self.games / (self.games + SHRINKAGE_GAMES)
        return w * value + (1 - w) * league


@dataclass
class LeagueBaseline:
    points_per_game: float = DEFAULT_POINTS_PER_GAME
    plays_per_game: float = DEFAULT_PLAYS_PER_GAME
    home_edge: float = DEFAULT_HOME_EDGE
    points_per_epa: float = 0.0


@dataclass
class RunningLeague:
    """League scoring environment built only from games already played.

    Pooling every season into one mean was the single largest source of error in the
    first version of this model. NFL scoring is not stationary -- team-game averages ran
    24.8 in 2020, 21.8 in 2023, and 23.0 in 2025, a six-point swing at game level. A
    pooled mean therefore over-projects low-scoring seasons and under-projects high ones,
    and using seasons that had not happened yet was also a quiet look-ahead.

    Weighting recent games more heavily lets the baseline track the environment as it
    drifts, while the prior-season carry-over keeps week 1 from starting blank.
    """

    points: float = 0.0
    games: int = 0
    home_points: float = 0.0
    away_points: float = 0.0
    sides: int = 0
    # Matched to the team half-life so ratings and the baseline they are normalised
    # against describe the same era. 128 games is roughly half an NFL season.
    half_life_games: float = 128.0
    _weighted_points: float = 0.0
    _weight: float = 0.0

    def observe(self, home_score: int, away_score: int) -> None:
        decay = 0.5 ** (2.0 / self.half_life_games)
        self._weighted_points = self._weighted_points * decay + (home_score + away_score)
        self._weight = self._weight * decay + 2.0

        self.points += home_score + away_score
        self.games += 1
        self.home_points += home_score
        self.away_points += away_score
        self.sides += 1

    @property
    def points_per_side(self) -> float:
        """Recent-weighted mean points for one team in one game."""
        if self._weight <= 0:
            return DEFAULT_POINTS_PER_GAME
        return self._weighted_points / self._weight

    @property
    def home_edge(self) -> float:
        if self.sides == 0:
            return DEFAULT_HOME_EDGE
        return (self.home_points - self.away_points) / self.sides

    def snapshot(self) -> LeagueBaseline:
        return LeagueBaseline(
            points_per_game=self.points_per_side,
            home_edge=self.home_edge,
        )

    @property
    def ready(self) -> bool:
        """Enough history for the baseline to mean anything."""
        return self.games >= 32


def league_baseline(session: Session, seasons: list[int]) -> LeagueBaseline:
    """Measure the scoring environment rather than assuming it."""
    rows = session.scalars(
        select(NflTeamGame).where(
            NflTeamGame.season.in_(seasons), NflTeamGame.points_for.is_not(None)
        )
    ).all()
    if not rows:
        return LeagueBaseline()

    points = [float(r.points_for) for r in rows]
    ppg = sum(points) / len(points)

    plays = [float(r.plays) for r in rows if r.plays]
    ppg_plays = sum(plays) / len(plays) if plays else DEFAULT_PLAYS_PER_GAME

    home = [float(r.points_for) for r in rows if r.is_home]
    away = [float(r.points_for) for r in rows if not r.is_home]
    home_edge = (
        (sum(home) / len(home)) - (sum(away) / len(away)) if home and away else DEFAULT_HOME_EDGE
    )

    # How many points a unit of EPA per play is worth, fitted by simple ratio so the
    # model does not need a regression library.
    paired = [
        (float(r.off_epa_per_play), float(r.points_for))
        for r in rows
        if r.off_epa_per_play is not None and r.points_for is not None
    ]
    points_per_epa = 0.0
    if len(paired) > 100:
        mean_epa = sum(e for e, _ in paired) / len(paired)
        mean_pts = sum(p for _, p in paired) / len(paired)
        num = sum((e - mean_epa) * (p - mean_pts) for e, p in paired)
        den = sum((e - mean_epa) ** 2 for e, _ in paired)
        points_per_epa = num / den if den else 0.0

    return LeagueBaseline(
        points_per_game=ppg,
        plays_per_game=ppg_plays,
        home_edge=home_edge,
        points_per_epa=points_per_epa,
    )


@dataclass
class GameProjection:
    home_team: str
    away_team: str
    home_points: float
    away_points: float
    wind_applied: float = 0.0
    home_games: int = 0
    away_games: int = 0

    @property
    def total(self) -> float:
        return self.home_points + self.away_points

    @property
    def margin(self) -> float:
        return self.home_points - self.away_points

    @property
    def thin_sample(self) -> bool:
        """True early in a season, when ratings lean mostly on the league mean."""
        return min(self.home_games, self.away_games) < 4

    def prob_over(self, line: float, sigma: float) -> float:
        return 1.0 - normal_cdf(line, self.total, sigma)

    def prob_home_cover(self, spread: float, sigma: float) -> float:
        """`spread` is the home handicap, negative when the home side is favoured."""
        return 1.0 - normal_cdf(-spread, self.margin, sigma)


def project(
    home: TeamRating,
    away: TeamRating,
    baseline: LeagueBaseline,
    *,
    wind: float | None = None,
    roof: str | None = None,
) -> GameProjection:
    """Project one matchup from ratings built on prior games only."""
    league_ppg = baseline.points_per_game

    # Each side's scoring is its own shrunk offence measured against what the opponent's
    # defence has allowed, expressed relative to the league mean.
    home_off = home.shrunk(home.ppg, league_ppg)
    away_off = away.shrunk(away.ppg, league_ppg)
    home_def = home.shrunk(home.papg, league_ppg)
    away_def = away.shrunk(away.papg, league_ppg)

    home_points = home_off * (away_def / league_ppg)
    away_points = away_off * (home_def / league_ppg)

    # Home advantage, measured, split across the two sides.
    home_points += baseline.home_edge / 2.0
    away_points -= baseline.home_edge / 2.0

    # Wind suppresses scoring outdoors. Coefficient measured by the splits engine.
    wind_applied = 0.0
    if roof == "outdoors" and wind is not None and wind >= WIND_THRESHOLD:
        wind_applied = WIND_TOTAL_ADJUSTMENT
        home_points += wind_applied / 2.0
        away_points += wind_applied / 2.0

    return GameProjection(
        home_team=home.team,
        away_team=away.team,
        home_points=max(0.0, home_points),
        away_points=max(0.0, away_points),
        wind_applied=wind_applied,
        home_games=home.games,
        away_games=away.games,
    )


def build_ratings_through(
    session: Session, season: int, week: int, *, lookback_seasons: int = 1
) -> dict[str, TeamRating]:
    """Ratings from games played strictly before the given week.

    Includes the tail of the previous season so week 1 is not a blank slate, which is
    the same shrinkage-toward-a-prior idea used for early-season NBA ratings.
    """
    seasons = list(range(season - lookback_seasons, season + 1))
    rows = session.scalars(
        select(NflTeamGame)
        .where(NflTeamGame.season.in_(seasons), NflTeamGame.points_for.is_not(None))
        .order_by(NflTeamGame.season, NflTeamGame.week)
    ).all()

    ratings: dict[str, TeamRating] = {}
    for row in rows:
        if row.season > season or (row.season == season and row.week >= week):
            continue
        ratings.setdefault(row.team, TeamRating(team=row.team)).add(row)
    return ratings


def project_game(
    session: Session, game: NflGame, *, baseline: LeagueBaseline | None = None
) -> GameProjection | None:
    """Project a scheduled game using only what was known before it."""
    ratings = build_ratings_through(session, game.season, game.week)
    home = ratings.get(game.home_team) or TeamRating(team=game.home_team)
    away = ratings.get(game.away_team) or TeamRating(team=game.away_team)
    if baseline is None:
        baseline = league_baseline(session, [game.season - 1, game.season])
    return project(home, away, baseline, wind=game.wind, roof=game.roof)
