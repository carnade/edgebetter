"""Walk-forward backtest of the NBA projection model.

Deliberately walk-forward: ratings for a game on date D are built only from games
played before D, so nothing the model sees could not have been known at tip-off. An
in-sample test that fits full-season ratings and then "predicts" the games that
produced them would report a flatteringly low error and tell us nothing.

Teams start with no history, so the first `burn_in` games per team are used to build
ratings but excluded from scoring.
"""

from __future__ import annotations

import statistics
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Game, GameTeamLog, Sport
from app.services.projections_nba import (
    HOME_ADVANTAGE,
    MARGIN_BIAS_CORRECTION,
    MARGIN_SIGMA,
    TOTAL_BIAS_CORRECTION,
    TOTAL_SIGMA,
)
from app.services.devig import prob_over


@dataclass
class _Running:
    """Running points scored and allowed for one team."""

    points_for: float = 0.0
    points_against: float = 0.0
    games: int = 0

    def add(self, pf: int, pa: int) -> None:
        self.points_for += pf
        self.points_against += pa
        self.games += 1

    @property
    def off(self) -> float | None:
        return self.points_for / self.games if self.games else None

    @property
    def dfn(self) -> float | None:
        return self.points_against / self.games if self.games else None


@dataclass
class BacktestResult:
    games_scored: int
    total_mae: float
    total_bias: float
    total_rmse: float
    margin_mae: float
    margin_bias: float
    margin_rmse: float
    actual_sigma: float
    over_rate_at_model_line: float
    baseline_mae: float
    errors: list[float] = field(default_factory=list)

    def summary(self) -> str:
        lines = [
            f"games scored           {self.games_scored}",
            f"total MAE              {self.total_mae:.2f} points",
            f"total bias             {self.total_bias:+.2f} points (model minus actual)",
            f"total RMSE             {self.total_rmse:.2f}",
            f"margin MAE             {self.margin_mae:.2f} points",
            f"margin bias            {self.margin_bias:+.2f}",
            f"margin RMSE            {self.margin_rmse:.2f} (model assumes {MARGIN_SIGMA})",
            f"actual total sigma     {self.actual_sigma:.2f} (model assumes {TOTAL_SIGMA})",
            f"over rate at model line {self.over_rate_at_model_line:.3f} (calibrated = 0.500)",
            f"league-average baseline {self.baseline_mae:.2f} MAE",
        ]
        return "\n".join("  " + line for line in lines)


def backtest_nba(
    session: Session, season: int, *, burn_in: int = 10, apply_corrections: bool = True
) -> BacktestResult:
    """Replay a season one game at a time, projecting before observing.

    `apply_corrections=False` reports the raw model, which is how the bias constants
    were derived in the first place. Note those constants are fitted on this season,
    so re-running with them on is a check that they work, not independent evidence.
    """
    games = session.scalars(
        select(Game)
        .where(Game.sport == Sport.NBA, Game.season == season, Game.is_final.is_(True))
        .order_by(Game.start_time)
    ).all()

    running: dict[int, _Running] = {}
    league_points = 0.0
    league_team_games = 0

    total_errors: list[float] = []
    margin_errors: list[float] = []
    actual_totals: list[float] = []
    baseline_errors: list[float] = []
    overs = 0

    for game in games:
        if game.home_score is None or game.away_score is None:
            continue

        home = running.setdefault(game.home_team_id, _Running())
        away = running.setdefault(game.away_team_id, _Running())

        league_avg = league_points / league_team_games if league_team_games else None

        # Only score once both sides have enough history to rate.
        if (
            home.games >= burn_in
            and away.games >= burn_in
            and league_avg
            and home.off
            and away.off
            and home.dfn
            and away.dfn
        ):
            # Ratio form of the pace-and-efficiency model: a team's scoring scaled by
            # how the opponent's defence compares with the league.
            proj_home = home.off * (away.dfn / league_avg) + HOME_ADVANTAGE / 2.0
            proj_away = away.off * (home.dfn / league_avg) - HOME_ADVANTAGE / 2.0

            if apply_corrections:
                proj_home -= TOTAL_BIAS_CORRECTION / 2.0 + MARGIN_BIAS_CORRECTION / 2.0
                proj_away -= TOTAL_BIAS_CORRECTION / 2.0 - MARGIN_BIAS_CORRECTION / 2.0

            actual_total = game.home_score + game.away_score
            actual_margin = game.home_score - game.away_score
            proj_total = proj_home + proj_away

            total_errors.append(proj_total - actual_total)
            margin_errors.append((proj_home - proj_away) - actual_margin)
            actual_totals.append(actual_total)
            baseline_errors.append(2 * league_avg - actual_total)
            if actual_total > proj_total:
                overs += 1

        home.add(game.home_score, game.away_score)
        away.add(game.away_score, game.home_score)
        league_points += game.home_score + game.away_score
        league_team_games += 2

    if not total_errors:
        raise ValueError(f"no scoreable games for season {season}; ingest it first")

    n = len(total_errors)
    return BacktestResult(
        games_scored=n,
        total_mae=sum(abs(e) for e in total_errors) / n,
        total_bias=sum(total_errors) / n,
        total_rmse=(sum(e * e for e in total_errors) / n) ** 0.5,
        margin_mae=sum(abs(e) for e in margin_errors) / n,
        margin_bias=sum(margin_errors) / n,
        margin_rmse=(sum(e * e for e in margin_errors) / n) ** 0.5,
        actual_sigma=statistics.pstdev(actual_totals),
        over_rate_at_model_line=overs / n,
        baseline_mae=sum(abs(e) for e in baseline_errors) / n,
        errors=total_errors,
    )


def calibration_report(result: BacktestResult, sigma: float = TOTAL_SIGMA) -> str:
    """Calibration curve: does a stated P(over) actually happen that often?

    For each offset from the model's own projected total, the model's P(over) depends
    only on the offset and sigma -- never on the outcome. The realised rate is then the
    fraction of games whose actual total cleared that line. If the two columns track,
    the assumed sigma is honest.

    (An earlier version of this function derived the probability from the observed
    error, which made the comparison tautological and always produced 0.000/1.000.)
    """
    lines = [f"  {'line offset':>11}  {'model P(over)':>13}  {'actual':>7}  {'n':>5}  {'gap':>6}"]
    for offset in (-15, -10, -5, -2, 0, 2, 5, 10, 15):
        # err = projected - actual, so actual - (projected + offset) = -err - offset.
        model_p = prob_over(float(offset), 0.0, sigma)
        hits = sum(1 for err in result.errors if -err > offset)
        n = len(result.errors)
        rate = hits / n
        lines.append(
            f"  {offset:>+11}  {model_p:>13.3f}  {rate:>7.3f}  {n:>5}  {rate - model_p:>+6.3f}"
        )
    return "\n".join(lines)


def suggest_constants(result: BacktestResult) -> str:
    """What the data says the model constants should be."""
    return "\n".join(
        [
            f"  TOTAL_SIGMA   measured {result.total_rmse:.1f} (currently {TOTAL_SIGMA})",
            f"  MARGIN_SIGMA  measured {result.margin_rmse:.1f} (currently {MARGIN_SIGMA})",
            f"  total bias    {result.total_bias:+.2f} -> subtract this from projected totals",
            f"  vs baseline   {result.baseline_mae - result.total_mae:+.2f} MAE improvement "
            f"({(1 - result.total_mae / result.baseline_mae) * 100:.1f}% better than league average)",
        ]
    )


# --------------------------------------------------------------------------- MLB
@dataclass
class MlbBacktestResult:
    games_scored: int
    total_mae: float
    total_bias: float
    total_rmse: float
    margin_mae: float
    margin_rmse: float
    baseline_mae: float
    # Spread of projected home-win probabilities -- the diagnostic for compounding.
    proj_margin_sd: float
    actual_margin_sd: float
    home_win_rate: float
    proj_home_win_mean: float

    def summary(self) -> str:
        return "\n".join(
            "  " + line
            for line in [
                f"games scored           {self.games_scored}",
                f"total MAE              {self.total_mae:.2f} runs",
                f"total bias             {self.total_bias:+.2f} runs",
                f"total RMSE             {self.total_rmse:.2f}",
                f"margin MAE             {self.margin_mae:.2f} runs",
                f"margin RMSE            {self.margin_rmse:.2f}",
                f"league-average baseline {self.baseline_mae:.2f} MAE",
                f"projected margin SD    {self.proj_margin_sd:.3f}",
                f"actual margin SD       {self.actual_margin_sd:.3f}",
                f"home win rate          {self.home_win_rate:.3f} "
                f"(model mean P(home) {self.proj_home_win_mean:.3f})",
            ]
        )


def backtest_mlb(session: Session, season: int, *, burn_in: int = 20) -> MlbBacktestResult:
    """Walk-forward MLB backtest at team level.

    Deliberately excludes starting pitchers: the stored pitcher lines are season-to-date
    as of now, so using them for a game played in May would leak the future. This tests
    the model's structure -- specifically whether multiplying an offence index by a
    pitching index spreads projections wider than reality supports.
    """
    from app.services.projections_mlb import (
        AWAY_RUN_MULTIPLIER,
        HOME_RUN_MULTIPLIER,
    )

    games = session.scalars(
        select(Game)
        .where(Game.sport == Sport.MLB, Game.season == season, Game.is_final.is_(True))
        .order_by(Game.start_time)
    ).all()

    scored: dict[int, _Running] = {}
    league_runs = 0.0
    league_team_games = 0

    total_errors: list[float] = []
    margin_errors: list[float] = []
    baseline_errors: list[float] = []
    proj_margins: list[float] = []
    actual_margins: list[float] = []
    home_wins = 0

    for game in games:
        if game.home_score is None or game.away_score is None:
            continue

        home = scored.setdefault(game.home_team_id, _Running())
        away = scored.setdefault(game.away_team_id, _Running())
        league_avg = league_runs / league_team_games if league_team_games else None

        if (
            home.games >= burn_in
            and away.games >= burn_in
            and league_avg
            and home.off
            and away.off
            and home.dfn
            and away.dfn
        ):
            # Same ratio structure as the production model, with runs allowed per game
            # standing in for the pitching index.
            proj_home = home.off * (away.dfn / league_avg) * HOME_RUN_MULTIPLIER
            proj_away = away.off * (home.dfn / league_avg) * AWAY_RUN_MULTIPLIER

            actual_total = game.home_score + game.away_score
            actual_margin = game.home_score - game.away_score

            total_errors.append(proj_home + proj_away - actual_total)
            margin_errors.append(proj_home - proj_away - actual_margin)
            baseline_errors.append(2 * league_avg - actual_total)
            proj_margins.append(proj_home - proj_away)
            actual_margins.append(actual_margin)
            if game.home_score > game.away_score:
                home_wins += 1

        home.add(game.home_score, game.away_score)
        away.add(game.away_score, game.home_score)
        league_runs += game.home_score + game.away_score
        league_team_games += 2

    if not total_errors:
        raise ValueError(f"no scoreable MLB games for {season}; ingest the season first")

    from app.services.projections_mlb import prob_home_wins

    n = len(total_errors)
    return MlbBacktestResult(
        games_scored=n,
        total_mae=sum(abs(e) for e in total_errors) / n,
        total_bias=sum(total_errors) / n,
        total_rmse=(sum(e * e for e in total_errors) / n) ** 0.5,
        margin_mae=sum(abs(e) for e in margin_errors) / n,
        margin_rmse=(sum(e * e for e in margin_errors) / n) ** 0.5,
        baseline_mae=sum(abs(e) for e in baseline_errors) / n,
        proj_margin_sd=statistics.pstdev(proj_margins),
        actual_margin_sd=statistics.pstdev(actual_margins),
        home_win_rate=home_wins / n,
        proj_home_win_mean=sum(
            prob_home_wins(4.5 + m / 2, 4.5 - m / 2) for m in proj_margins
        )
        / n,
    )
