"""Grade a prop line against our projection, discounted by how much we trust ourselves.

The naive grade is "our probability minus the price's break-even". That is only as good
as the probability, and ours are not equally good across markets: replayed over 2021-2025,
receiving yards are accurate to 1.9 points at worst, rushing to 2.6, and passing to 3.6.

So an edge has to clear our own measured error before it counts. A +3 point edge is a real
signal on receiving and sits inside the error bars on passing. That is why all three
markets get identical analysis and still grade differently -- the bar moves, not the
method.

Those bars are much tighter than they were. They used to be 3.5 / 6.9 / 8.6, and two
separate things were wrong: the receiving figure was a mean gap while the other two were
max gaps, so they were never on the same scale, and the model itself was over-projecting
in a way that inflated all three. With both fixed, every market now beats the player's own
scoring average, which none of them managed before.

The alternative would be to hide the weaker markets, which is worse: it throws away
information and hides the reason.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from enum import Enum

from app.services.devig import american_to_decimal
from app.services.nfl_props import MARKET_CALIBRATION, Market, PropProjection
from app.services.stats import Band

log = logging.getLogger(__name__)

# An edge below this is not worth acting on regardless of calibration -- it is inside
# the noise of any estimate built from a few dozen games.
MIN_MEANINGFUL_EDGE = 0.02

# How far past our measured error an edge must sit to earn the top grade.
STRONG_MULTIPLE = 2.0


class Grade(str, Enum):
    A = "A"
    B = "B"
    C = "C"
    D = "D"

    @property
    def actionable(self) -> bool:
        return self in (Grade.A, Grade.B)

    @property
    def description(self) -> str:
        return {
            Grade.A: "edge clears our error margin with room to spare",
            Grade.B: "edge clears our error margin",
            Grade.C: "edge is inside our own error margin",
            Grade.D: "no edge at this price",
        }[self]


@dataclass(frozen=True)
class GradedProp:
    """One side of one prop, priced and graded."""

    player_name: str
    player_id: str | None
    team: str | None
    opponent: str
    market: Market
    side: str            # Over / Under
    line: float
    book: str
    price_american: int

    projected: float          # mean
    projected_median: float | None  # the 50/50 point, which is what decides the bet
    model_prob: float
    break_even: float
    games_of_history: int
    band: Band

    calibration_gap: float
    calibration: str
    # Recent games, most recent last, so the reader can sanity-check the projection
    # against what actually happened rather than taking a probability on trust.
    recent_yards: tuple[float, ...] = ()

    @property
    def recent_vs_line(self) -> tuple[int, int]:
        """(games over the line, games counted) among the recent games shown."""
        if not self.recent_yards:
            return 0, 0
        return sum(1 for y in self.recent_yards if y > self.line), len(self.recent_yards)

    @property
    def edge(self) -> float:
        """Percentage points by which our probability beats the price's break-even."""
        return self.model_prob - self.break_even

    @property
    def required_edge(self) -> float:
        """The bar this market must clear, set by its own measured error."""
        return max(self.calibration_gap, MIN_MEANINGFUL_EDGE)

    @property
    def edge_ratio(self) -> float:
        """How many times over the bar the edge sits. Below 1.0 is inside our error."""
        return self.edge / self.required_edge if self.required_edge > 0 else 0.0

    @property
    def expected_value(self) -> float:
        decimal = american_to_decimal(self.price_american)
        return self.model_prob * (decimal - 1.0) - (1.0 - self.model_prob)

    @property
    def grade(self) -> Grade:
        # A thin sample cannot earn an actionable grade whatever the numbers say.
        if self.band is Band.NOISE or self.games_of_history < 4:
            return Grade.C if self.edge > 0 else Grade.D
        if self.edge <= 0:
            return Grade.D
        if self.edge >= self.required_edge * STRONG_MULTIPLE:
            return Grade.A
        if self.edge >= self.required_edge:
            return Grade.B
        return Grade.C

    @property
    def reason(self) -> str:
        """Why this grade, in a sentence the UI can show verbatim."""
        edge_pts = self.edge * 100
        bar_pts = self.required_edge * 100

        if self.band is Band.NOISE or self.games_of_history < 4:
            return (
                f"only {self.games_of_history} games of history -- not enough to grade "
                f"regardless of the number"
            )
        if self.edge <= 0:
            return (
                f"we make it {self.model_prob:.1%}, the price needs {self.break_even:.1%}"
            )
        if self.edge >= self.required_edge * STRONG_MULTIPLE:
            return (
                f"+{edge_pts:.1f} pts against a {bar_pts:.1f} pt bar -- "
                f"more than double our error margin"
            )
        if self.edge >= self.required_edge:
            return f"+{edge_pts:.1f} pts against a {bar_pts:.1f} pt bar"
        return (
            f"+{edge_pts:.1f} pts, but this market is only accurate to "
            f"{bar_pts:.1f} pts -- inside our own error"
        )


def grade_line(
    projection: PropProjection,
    *,
    side: str,
    line: float,
    price_american: int,
    book: str,
) -> GradedProp | None:
    """Grade one side of one posted line against a projection."""
    prob_over = projection.prob_over(line)
    if prob_over is None:
        return None

    normalised = side.strip().lower()
    if normalised not in {"over", "under"}:
        return None
    model_prob = prob_over if normalised == "over" else 1.0 - prob_over

    decimal = american_to_decimal(price_american)
    calibration, gap, _ = MARKET_CALIBRATION[projection.market.value]

    return GradedProp(
        player_name=projection.player_name,
        player_id=projection.player_id,
        team=projection.team,
        opponent=projection.opponent,
        market=projection.market,
        side="Over" if normalised == "over" else "Under",
        line=line,
        book=book,
        price_american=price_american,
        projected=projection.expected,
        projected_median=projection.median,
        model_prob=model_prob,
        break_even=1.0 / decimal,
        games_of_history=projection.games_of_history,
        band=projection.band,
        calibration_gap=gap,
        calibration=calibration.value,
        recent_yards=tuple(projection.recent_yards[-6:]),
    )


def rank(graded: list[GradedProp]) -> list[GradedProp]:
    """Best first: grade, then how far past the bar the edge sits.

    Ranking on `edge_ratio` rather than raw edge is what keeps the three markets
    comparable -- a 4-point edge on receiving outranks a 6-point edge on passing,
    because passing has to clear a much higher bar to mean the same thing.
    """
    order = {Grade.A: 0, Grade.B: 1, Grade.C: 2, Grade.D: 3}
    return sorted(graded, key=lambda g: (order[g.grade], -g.edge_ratio))
