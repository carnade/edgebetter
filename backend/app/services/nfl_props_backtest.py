"""Walk-forward calibration test for player prop projections.

This is the deliverable, not the projection itself. Since we have no historical prop
lines to bet against, the honest question is not "would we have won" but **"are our
probabilities true?"** If the model says 60% and the outcome clears 60% of the time, the
distribution is honest and can be pointed at any line from any book.

Calibration is a far stricter test than it sounds. A model can have a good average error
and still be badly calibrated -- claiming 80% confidence on coin flips. It is also the
one property that makes a distribution usable without a market to check it against.

Everything is walk-forward: a player's form at week N uses only weeks before N, and
defensive adjustments likewise. The whole projection is rebuilt incrementally rather
than by re-querying, because a per-game database round trip over 100,000 player-games
would take hours.
"""

from __future__ import annotations

import logging
import math
import statistics
from collections import defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NflPlayerGame
from app.services.nfl_props import (
    APPLY_OPPONENT_FACTOR,
    COLD_FACTOR,
    COLD_THRESHOLD_F,
    EFFICIENCY_SHRINKAGE,
    HALF_LIFE,
    INDOOR_FACTOR,
    MAX_OPPONENT_EFFECT,
    CV_MULTIPLIER,
    MIN_DEFENCE_GAMES,
    SCALE_CORRECTION,
    VOLUME_SHRINKAGE,
    Market,
    _gamma_cdf,
)
from app.services.stats import rate

log = logging.getLogger(__name__)

# A player needs this much history before a projection is scored at all.
MIN_HISTORY = 6

# Calibration corrections, fitted on 2021-2024 and validated on 2025. Exposed as module
# globals so the fitting sweep can vary them without rewriting the file, and so the
# fitted values live in exactly one place.
_BIAS_CORR = 0.0
# Overrides the per-market CV_MULTIPLIER when set, for sweeping. None means use the
# shipped per-market values.
_CV_MULT: float | None = None

# Experiment knob: shrink efficiency on accumulated attempts rather than games played.
#
# Efficiency is a per-attempt quantity, so the evidence behind it is the number of
# attempts, not the number of appearances. Two backs with 14 games each can have wildly
# different amounts of information -- one with 18 carries a game, one with 6 -- and
# shrinking both by game count treats them identically. None means keep the old
# games-based rule.
_EFF_SHRINK_ATTEMPTS: float | None = None

# Lines are tested relative to the projection, mimicking how a book prices near the
# expected value rather than at arbitrary numbers.
LINE_OFFSETS = (-20.0, -10.0, -5.0, 0.0, 5.0, 10.0, 20.0)


@dataclass
class _Running:
    """Incremental per-player state, decayed toward recent games."""

    yards: list[float] = field(default_factory=list)
    volumes: list[float] = field(default_factory=list)

    def add(self, yards: float, volume: float) -> None:
        # Only games the player took part in. A zero-carry game says nothing about how
        # many yards he gains when he plays, and including it makes the distribution
        # claim a mass at zero that a gamma cannot represent.
        if volume <= 0:
            return
        self.yards.append(yards)
        self.volumes.append(volume)

    @property
    def games(self) -> int:
        return len(self.yards)


def _decayed(values: list[float]) -> tuple[float, float]:
    if not values:
        return 0.0, 0.0
    decay = 0.5 ** (1.0 / HALF_LIFE)
    total = weight = 0.0
    for i, v in enumerate(reversed(values)):
        w = decay**i
        total += w * v
        weight += w
    mean = total / weight if weight else 0.0
    var = sum((v - mean) ** 2 * decay**i for i, v in enumerate(reversed(values)))
    return mean, math.sqrt(max(var / weight, 0.0)) if weight else 0.0


@dataclass
class PropCalibration:
    market: Market
    scored: int
    mae: float
    bias: float
    baseline_mae: float
    # (stated probability, observed rate, n) at each tested line offset.
    curve: list[tuple[float, float, float, int]]
    max_gap: float
    mean_abs_gap: float

    @property
    def calibrated(self) -> bool:
        """Within three points everywhere is honest enough to point at a line."""
        return self.max_gap <= 0.03

    @property
    def beats_baseline(self) -> bool:
        return self.mae < self.baseline_mae

    def summary(self) -> str:
        lines = [
            f"market               {self.market.label}",
            f"player-games scored  {self.scored}",
            f"MAE                  {self.mae:.2f} yards",
            f"bias                 {self.bias:+.2f}",
            f"baseline MAE         {self.baseline_mae:.2f}  (player's own average)",
            f"vs baseline          {(1 - self.mae / self.baseline_mae) * 100:+.1f}%",
            "",
            "CALIBRATION -- does a stated probability happen that often?",
            f"  {'line':>8} {'we say':>8} {'actual':>8} {'gap':>7} {'n':>7}",
        ]
        for offset, stated, actual, n in self.curve:
            lines.append(
                f"  {offset:>+8.0f} {stated:>8.3f} {actual:>8.3f} "
                f"{actual - stated:>+7.3f} {n:>7}"
            )
        lines += [
            "",
            f"worst gap            {self.max_gap:.3f}",
            f"mean absolute gap    {self.mean_abs_gap:.3f}",
            "",
            "VERDICT",
        ]
        if self.calibrated:
            lines.append("  Calibrated. When this says 60%, it happens about 60% of the time,")
            lines.append("  so it can be pointed at any line from any book without a second")
            lines.append("  book to devig against.")
        else:
            lines.append(
                f"  NOT calibrated -- off by up to {self.max_gap:.1%}. The probabilities"
            )
            lines.append("  overstate confidence and should not be used to size a bet.")
        return "\n".join("  " + line for line in lines)


def backtest_market(
    session: Session,
    market: Market,
    *,
    seasons: tuple[int, ...] = (2021, 2022, 2023, 2024, 2025),
    score_seasons: tuple[int, ...] | None = None,
) -> PropCalibration:
    """Replay every player-game, projecting from prior weeks only.

    `score_seasons` restricts which seasons are *scored* without restricting which are
    used to build history, which is what makes an honest holdout possible: fit a constant
    on the early years, then score only the last one with the same warm-up the live model
    would have had.
    """
    rows = session.scalars(
        select(NflPlayerGame)
        .where(
            NflPlayerGame.position.in_(market.positions),
            NflPlayerGame.season.in_(seasons),
        )
        .order_by(NflPlayerGame.season, NflPlayerGame.week)
    ).all()
    if not rows:
        raise ValueError(f"no player rows for {market.value}; run the player ingest first")

    stat, vol = market.stat, market.volume

    players: dict[str, _Running] = defaultdict(_Running)
    # Defence: total yards allowed and games seen, for the league-relative multiplier.
    defence: dict[str, list[float]] = defaultdict(lambda: [0.0, 0.0])
    defence_weeks: dict[str, set[tuple[int, int]]] = defaultdict(set)
    league = [0.0, 0.0]  # total yards, observations
    # League yards and attempts, for a running per-attempt efficiency baseline. The
    # previous version shrank efficiency toward itself, which is a no-op -- so the
    # backtest was silently validating a model without efficiency shrinkage while
    # production applied it.
    league_attempts = [0.0, 0.0]  # total yards, total attempts

    errors: list[float] = []
    baseline_errors: list[float] = []
    # offset -> [stated total, hits, n]
    curve: dict[float, list[float]] = {o: [0.0, 0.0, 0.0] for o in LINE_OFFSETS}

    for row in rows:
        pid = row.player_id
        state = players[pid]
        actual = float(getattr(row, stat) or 0.0)
        volume = float(getattr(row, vol) or 0.0)

        league_mean = league[0] / league[1] if league[1] else 0.0
        # Score only games the player actually participated in -- the same condition the
        # projection is built on, and the one a book uses when it voids a prop.
        scoreable = state.games >= MIN_HISTORY and league_mean > 0 and volume > 0
        if score_seasons is not None and row.season not in score_seasons:
            scoreable = False

        if scoreable:
            mean_volume, _ = _decayed(state.volumes)
            mean_yards, sd_yards = _decayed(state.yards)
            # Decayed on the same half-life as volume, matching the shipped model, so
            # volume x efficiency reconciles with observed yards per game.
            efficiency = mean_yards / mean_volume if mean_volume > 0 else 0.0

            g = state.games
            shrink = VOLUME_SHRINKAGE.get(market.value, 0.0)
            vw = g / (g + shrink) if shrink > 0 else 1.0
            if _EFF_SHRINK_ATTEMPTS is None:
                ew = g / (g + EFFICIENCY_SHRINKAGE)
            else:
                attempts = sum(state.volumes)
                ew = attempts / (attempts + _EFF_SHRINK_ATTEMPTS)

            # Positional baselines from what the league has produced so far.
            league_eff = (
                league_attempts[0] / league_attempts[1] if league_attempts[1] > 0 else 0.0
            )
            base_volume = league_mean / league_eff if league_eff > 0 else mean_volume
            projected_volume = vw * mean_volume + (1 - vw) * base_volume
            projected_eff = (
                ew * efficiency + (1 - ew) * league_eff
                if league_eff > 0
                else efficiency
            )

            # Mirrors production: measured, but only applied if the flag says so.
            factor = 1.0
            if APPLY_OPPONENT_FACTOR:
                allowed, seen = defence[row.opponent]
                if len(defence_weeks[row.opponent]) >= MIN_DEFENCE_GAMES and seen > 0:
                    ratio = (allowed / seen) / league_mean
                    factor = max(
                        1 - MAX_OPPONENT_EFFECT, min(1 + MAX_OPPONENT_EFFECT, ratio)
                    )

            # Same venue and weather terms the live projection applies. Without these
            # the backtest would validate a different model from the one that ships.
            context = 1.0
            if row.roof in ("dome", "closed"):
                context *= INDOOR_FACTOR.get(market.value, 1.0)
            else:
                if row.wind is not None and row.wind >= 15:
                    if market is Market.PASS_YDS:
                        context *= 0.94
                    elif market is Market.RECV_YDS:
                        context *= 0.96
                if row.temp is not None and row.temp < COLD_THRESHOLD_F:
                    context *= COLD_FACTOR.get(market.value, 1.0)

            scale = SCALE_CORRECTION.get(market.value, 1.0)
            expected = (
                projected_volume * projected_eff * factor * context * scale - _BIAS_CORR
            )
            cv = (sd_yards / mean_yards) if (g >= 8 and mean_yards > 0 and sd_yards > 0) else 0.75
            mult = _CV_MULT if _CV_MULT is not None else CV_MULTIPLIER.get(market.value, 1.0)
            cv = max(0.35, min(1.0, cv * mult))
            sd = max(expected, 1e-6) * cv

            if expected > 0 and sd > 0:
                errors.append(expected - actual)
                baseline_errors.append(mean_yards - actual)

                shape = (expected / sd) ** 2
                scale = (sd**2) / expected
                for offset in LINE_OFFSETS:
                    line = expected + offset
                    if line < 0:
                        continue
                    stated = 1.0 - _gamma_cdf(line, shape, scale)
                    bucket = curve[offset]
                    bucket[0] += stated
                    bucket[1] += 1.0 if actual > line else 0.0
                    bucket[2] += 1.0

        # Advance state strictly after scoring.
        state.add(actual, volume)
        if volume > 0:
            defence[row.opponent][0] += actual
            defence[row.opponent][1] += 1
        defence_weeks[row.opponent].add((row.season, row.week))
        if volume > 0:
            league[0] += actual
            league[1] += 1
            league_attempts[0] += actual
            league_attempts[1] += volume

    if not errors:
        raise ValueError(f"no scoreable player-games for {market.value}")

    n = len(errors)
    points: list[tuple[float, float, float, int]] = []
    for offset in LINE_OFFSETS:
        stated_total, hits, count = curve[offset]
        if count < 100:
            continue
        points.append((offset, stated_total / count, hits / count, int(count)))

    gaps = [abs(actual - stated) for _, stated, actual, _ in points]

    return PropCalibration(
        market=market,
        scored=n,
        mae=sum(abs(e) for e in errors) / n,
        bias=sum(errors) / n,
        baseline_mae=sum(abs(e) for e in baseline_errors) / n,
        curve=points,
        max_gap=max(gaps) if gaps else 1.0,
        mean_abs_gap=statistics.fmean(gaps) if gaps else 1.0,
    )
