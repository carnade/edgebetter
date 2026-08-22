"""Walk-forward validation for the strikeout model.

Same discipline as the NBA and mismatch backtests: a pitcher's rate and workload are
built only from starts before the game being predicted, so nothing leaks backwards. The
Phase 1 backtests caught a sigma wrong by 40% and a win rate inflated by look-ahead;
this exists so the same class of error cannot ship here.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Game, PitcherGameLog, Sport, TeamSeasonStats
from app.services.projections_props import (
    DISPERSION,
    expected_strikeouts,
    prob_over_line,
)

MIN_PRIOR_STARTS = 5


@dataclass
class StrikeoutBacktest:
    n: int
    mae: float
    bias: float
    rmse: float
    baseline_mae: float
    actual_sd: float
    # Calibration at half-point lines placed relative to the projection.
    calibration: list[tuple[float, float, float, int]]
    poisson_logloss: float
    nb_logloss: float

    def summary(self) -> str:
        lines = [
            f"starts scored        {self.n}",
            f"MAE                  {self.mae:.2f} strikeouts",
            f"bias                 {self.bias:+.2f} (model minus actual)",
            f"RMSE                 {self.rmse:.2f}",
            f"actual SD            {self.actual_sd:.2f}",
            f"pitcher-average baseline MAE {self.baseline_mae:.2f}",
            f"improvement          {(1 - self.mae / self.baseline_mae) * 100:+.1f}% vs baseline",
            f"log loss  Poisson    {self.poisson_logloss:.4f}",
            f"log loss  NegBinom   {self.nb_logloss:.4f}  "
            f"({'NB fits better' if self.nb_logloss < self.poisson_logloss else 'Poisson fits better'})",
        ]
        return "\n".join("  " + line for line in lines)

    def calibration_table(self) -> str:
        rows = [f"  {'line offset':>11} {'model P(over)':>13} {'actual':>7} {'n':>6} {'gap':>7}"]
        for offset, predicted, actual, n in self.calibration:
            rows.append(
                f"  {offset:>+11.1f} {predicted:>13.3f} {actual:>7.3f} {n:>6} {actual - predicted:>+7.3f}"
            )
        return "\n".join(rows)


def backtest_strikeouts(session: Session, season: int) -> StrikeoutBacktest:
    """Replay every start, projecting from prior starts only."""
    # Opponent strikeout rates are season-final; they move slowly and using running
    # values would add noise without removing meaningful look-ahead.
    opp_rate: dict[int, float] = {}
    for row in session.scalars(
        select(TeamSeasonStats).where(
            TeamSeasonStats.season == season, TeamSeasonStats.sport == Sport.MLB
        )
    ).all():
        if row.strikeout_rate:
            opp_rate[row.team_id] = row.strikeout_rate
    league = statistics.fmean(opp_rate.values()) if opp_rate else 0.221

    logs = session.scalars(
        select(PitcherGameLog)
        .where(PitcherGameLog.season == season)
        .order_by(PitcherGameLog.game_date)
    ).all()

    # Map each start to the opposing team via the game the pitcher was announced for.
    running: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0])  # ip, k, starts

    errors: list[float] = []
    baseline_errors: list[float] = []
    actuals: list[int] = []
    predictions: list[tuple[float, int]] = []

    for entry in logs:
        pid = entry.player_id
        ip, ks, starts = running[pid]

        if starts >= MIN_PRIOR_STARTS and ip > 0 and entry.strikeouts is not None:
            k_per_9 = 9.0 * ks / ip
            expected_ip = ip / starts
            rate = opp_rate.get(entry.opponent_id or -1, league)
            projected = expected_strikeouts(k_per_9, expected_ip, rate, league)

            actual = entry.strikeouts
            errors.append(projected - actual)
            # Baseline: this pitcher's own running average, with no opponent adjustment.
            baseline_errors.append((ks / starts) - actual)
            actuals.append(actual)
            predictions.append((projected, actual))

        if entry.innings_pitched and entry.strikeouts is not None:
            running[pid][0] += entry.innings_pitched
            running[pid][1] += entry.strikeouts
            running[pid][2] += 1

    if not errors:
        raise ValueError(f"no scoreable starts for {season}")

    n = len(errors)

    # Calibration at half-point lines placed relative to each projection.
    calibration: list[tuple[float, float, float, int]] = []
    for offset in (-2.5, -1.5, -0.5, 0.5, 1.5, 2.5):
        predicted = statistics.fmean(
            prob_over_line(proj, proj + offset) for proj, _ in predictions
        )
        hits = sum(1 for proj, actual in predictions if actual > proj + offset)
        calibration.append((offset, predicted, hits / n, n))

    def logloss(dispersion: float) -> float:
        total = 0.0
        for proj, actual in predictions:
            p = prob_over_line(proj, proj, dispersion=dispersion)
            p = min(max(p, 1e-6), 1 - 1e-6)
            total += -(math.log(p) if actual > proj else math.log(1 - p))
        return total / n

    import math

    return StrikeoutBacktest(
        n=n,
        mae=sum(abs(e) for e in errors) / n,
        bias=sum(errors) / n,
        rmse=(sum(e * e for e in errors) / n) ** 0.5,
        baseline_mae=sum(abs(e) for e in baseline_errors) / n,
        actual_sd=statistics.pstdev(actuals),
        calibration=calibration,
        poisson_logloss=logloss(1.0),
        nb_logloss=logloss(DISPERSION),
    )
