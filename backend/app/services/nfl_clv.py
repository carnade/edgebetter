"""Closing line value: did the market move toward the number we would have taken?

Every model in this project has failed to beat the closing line, which is the hardest
benchmark there is. This module asks a different and much more answerable question:
when our projection disagreed with an *opening* line, did the market subsequently move
in our direction?

That matters because a bet is placed at the price available when you place it, not at
the close. If our disagreements predict the drift, the edge is real and comes from being
early rather than from being smarter than the final number. If they do not, we have
learned that cheaply and definitively.

CLV is the professional standard for exactly this reason: it is measurable in weeks,
needs no settled results, and risks nothing.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NflGame, NflLineHistory
from app.services.stats import Rate, mean_and_interval, rate

log = logging.getLogger(__name__)

# A line has to move by at least this much to count as having moved at all; anything
# smaller is rounding between books.
MIN_MOVE = 0.5

# How far our projection must sit from the opener before we would have taken a side.
MIN_DISAGREEMENT = 1.0


@dataclass
class GameMovement:
    """One game's line from first observation to latest."""

    game_id: str
    season: int
    week: int
    matchup: str
    observations: int
    open_total: float | None
    latest_total: float | None
    open_spread: float | None
    latest_spread: float | None
    model_total_at_open: float | None
    hours_span: float | None

    @property
    def total_drift(self) -> float | None:
        if self.open_total is None or self.latest_total is None:
            return None
        return self.latest_total - self.open_total

    @property
    def spread_drift(self) -> float | None:
        if self.open_spread is None or self.latest_spread is None:
            return None
        return self.latest_spread - self.open_spread

    @property
    def model_disagreement(self) -> float | None:
        """Model total minus opening total. Positive means we were on the over."""
        if self.model_total_at_open is None or self.open_total is None:
            return None
        return self.model_total_at_open - self.open_total

    @property
    def moved_our_way(self) -> bool | None:
        """Did the line drift toward the side our model favoured at the open?

        This is the whole measurement. It resolves before the game is played, which is
        what makes it usable weeks earlier than results would be.
        """
        disagreement = self.model_disagreement
        drift = self.total_drift
        if disagreement is None or drift is None:
            return None
        if abs(disagreement) < MIN_DISAGREEMENT or abs(drift) < MIN_MOVE:
            return None
        return (disagreement > 0) == (drift > 0)


@dataclass
class ClvReport:
    tracked_games: int
    games_with_movement: int
    resolved: int
    clv_rate: Rate | None
    mean_drift: float
    mean_abs_drift: float
    drift_low: float
    drift_high: float
    by_week: dict[int, tuple[int, int]]

    @property
    def ready(self) -> bool:
        """Enough resolved games for the CLV number to carry any weight."""
        return self.resolved >= 30

    def summary(self) -> str:
        lines = [
            f"games tracked        {self.tracked_games}",
            f"lines that moved     {self.games_with_movement}",
            f"resolved for CLV     {self.resolved}",
            "",
            f"mean total drift     {self.mean_drift:+.2f} points "
            f"[{self.drift_low:+.2f}, {self.drift_high:+.2f}]",
            f"mean absolute drift  {self.mean_abs_drift:.2f}",
        ]

        if self.clv_rate is not None and self.resolved:
            lines += ["", f"line moved our way   {self.clv_rate.format()}"]

        lines += ["", "READ"]
        if not self.ready:
            need = 30 - self.resolved
            lines.append(
                f"  Not enough data yet -- {self.resolved} resolved games, need about "
                f"{need} more."
            )
            lines.append(
                "  CLV becomes readable a few weeks into the season, once lines have had"
            )
            lines.append("  time to move on games we were watching from the open.")
        elif self.clv_rate.lower > 0.5:
            lines.append(
                f"  The market moved toward our side {self.clv_rate.rate:.1%} of the time,"
            )
            lines.append(
                "  and the interval clears 50%. That is genuine edge from being early:"
            )
            lines.append("  bet at the open, not at the close.")
        elif self.clv_rate.upper < 0.5:
            lines.append(
                f"  The market moved AGAINST our side {1 - self.clv_rate.rate:.1%} of the"
            )
            lines.append(
                "  time. Our disagreements are systematically the wrong way round --"
            )
            lines.append("  strong evidence the model is the thing that is wrong.")
        else:
            lines.append(
                f"  The market moved our way {self.clv_rate.rate:.1%} of the time, and the"
            )
            lines.append(
                "  interval straddles 50%. No evidence we see anything before the market"
            )
            lines.append("  does -- our disagreements are noise, not foresight.")
        return "\n".join("  " + line for line in lines)


def game_movements(session: Session, *, season: int | None = None) -> list[GameMovement]:
    """Collapse the observation history into one open-to-latest record per game."""
    stmt = select(NflLineHistory).order_by(NflLineHistory.game_id, NflLineHistory.fetched_at)
    if season:
        stmt = stmt.where(NflLineHistory.season == season)
    rows = session.scalars(stmt).all()

    grouped: dict[str, list[NflLineHistory]] = defaultdict(list)
    for row in rows:
        grouped[row.game_id].append(row)

    games = {
        g.game_id: g
        for g in session.scalars(
            select(NflGame).where(NflGame.game_id.in_(list(grouped)))
        ).all()
    }

    out: list[GameMovement] = []
    for game_id, history in grouped.items():
        game = games.get(game_id)
        if game is None:
            continue
        first, last = history[0], history[-1]
        span = None
        if first.hours_to_kickoff is not None and last.hours_to_kickoff is not None:
            span = first.hours_to_kickoff - last.hours_to_kickoff

        out.append(
            GameMovement(
                game_id=game_id,
                season=game.season,
                week=game.week,
                matchup=f"{game.away_team} @ {game.home_team}",
                observations=len(history),
                open_total=first.total_line,
                latest_total=last.total_line,
                open_spread=first.spread_line,
                latest_spread=last.spread_line,
                # The model's view at the open, stored at the time rather than recomputed.
                model_total_at_open=first.model_total,
                hours_span=span,
            )
        )

    out.sort(key=lambda m: (m.season, m.week, m.matchup))
    return out


def clv_report(session: Session, *, season: int | None = None) -> ClvReport:
    """Measure whether the market drifts toward our side."""
    movements = game_movements(session, season=season)

    drifts = [m.total_drift for m in movements if m.total_drift is not None]
    moved = [d for d in drifts if abs(d) >= MIN_MOVE]

    resolved = [m.moved_our_way for m in movements if m.moved_our_way is not None]
    hits = sum(1 for r in resolved if r)

    by_week: dict[int, tuple[int, int]] = {}
    for m in movements:
        outcome = m.moved_our_way
        if outcome is None:
            continue
        h, n = by_week.get(m.week, (0, 0))
        by_week[m.week] = (h + (1 if outcome else 0), n + 1)

    mean_drift, low, high = mean_and_interval(drifts) if drifts else (0.0, 0.0, 0.0)

    return ClvReport(
        tracked_games=len(movements),
        games_with_movement=len(moved),
        resolved=len(resolved),
        clv_rate=rate(hits, len(resolved)) if resolved else None,
        mean_drift=mean_drift,
        mean_abs_drift=sum(abs(d) for d in drifts) / len(drifts) if drifts else 0.0,
        drift_low=low,
        drift_high=high,
        by_week=by_week,
    )
