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

# Minimum games of history before a prop can earn an actionable grade.
#
# Four is low enough that a genuinely thin sample can reach grade A, and that is the
# intended trade. A higher floor would be safer per-pick and blank out the first month of
# every season -- precisely when a changed role is most worth catching and least
# established. Four means coverage from about week four onward.
#
# The cost is real: a rookie with four games can produce a double-digit edge that is a
# small-sample artifact rather than a signal. The mitigation is display, not filtering --
# every row shows its games count and sample band so a thin pick is visible as thin, and
# can be judged on the player rather than rejected by a rule.
#
# Raise this and early-season coverage disappears; that is the decision being made here.
MIN_GAMES_FOR_GRADE = 4

# Below this many books posting the same prop, the LINE has not been cross-checked.
#
# This is not the MLB devig rule and does not work the same way. There, four books are a
# hard requirement, because a fair probability is the median across books and a median of
# two is meaningless. Here the probability comes from the player's own distribution, so one
# book is genuinely enough to grade against -- what a second book buys is a check on the
# *number*, not the maths.
#
# The risk a lone book carries is specific: if it has posted a line the rest of the market
# would not, our edge is measured against that book's quirk rather than against the market.
# There is no way to tell those apart from one quote. So a thin prop is still shown and
# still graded -- it is just labelled, and the reader decides.
MIN_BOOKS_FOR_CONSENSUS = 3

# How far one book's line may sit from the median before it is called an outlier. Books
# rarely differ by more than a point or two on the same player; a wider gap usually means
# one of them knows something (an injury, a snap-count report) that has not reached the
# others yet.
OUTLIER_LINE_YARDS = 2.5


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

    # How many books posted this prop at all, and how far apart their lines were. Used
    # only to warn -- neither one changes the grade.
    books_posting: int = 1
    line_span: float | None = None
    median_line: float | None = None

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
    def line_is_outlier(self) -> bool:
        """Whether this book's number sits well away from what the others posted."""
        if self.median_line is None or self.books_posting < 2:
            return False
        return abs(self.line - self.median_line) > OUTLIER_LINE_YARDS

    @property
    def coverage_warning(self) -> str | None:
        """Why this line may not be trustworthy, independent of the grade.

        Returns None when coverage is fine, so the UI can show a badge only when there is
        something to say.
        """
        if self.line_is_outlier:
            direction = "above" if self.line > (self.median_line or 0) else "below"
            return (
                f"{self.book} has this {abs(self.line - (self.median_line or 0)):.1f} "
                f"yards {direction} the other {self.books_posting - 1} book"
                f"{'s' if self.books_posting > 2 else ''}. Our edge may be against this "
                f"book's number rather than the market's -- check for late news."
            )
        if self.books_posting <= 1:
            return (
                f"Only {self.book} is posting this line. The projection does not need a "
                f"second book, but nothing here cross-checks the number itself."
            )
        if self.books_posting < MIN_BOOKS_FOR_CONSENSUS:
            return (
                f"Only {self.books_posting} books posting. Thin agreement -- the line has "
                f"had little cross-checking."
            )
        return None

    @property
    def expected_value(self) -> float:
        decimal = american_to_decimal(self.price_american)
        return self.model_prob * (decimal - 1.0) - (1.0 - self.model_prob)

    @property
    def grade(self) -> Grade:
        # A thin sample cannot earn an actionable grade whatever the numbers say.
        if self.band is Band.NOISE or self.games_of_history < MIN_GAMES_FOR_GRADE:
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

        if self.band is Band.NOISE or self.games_of_history < MIN_GAMES_FOR_GRADE:
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
    books_posting: int = 1,
    line_span: float | None = None,
    median_line: float | None = None,
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
        books_posting=books_posting,
        line_span=line_span,
        median_line=median_line,
    )


def best_bet_per_prop(
    graded: list[GradedProp], *, per_side: bool = False
) -> list[GradedProp]:
    """One row per prop, keeping the best version of it. Order is preserved.

    A prop is a player and a market -- "McCaffrey receptions" -- not a posting. The same
    prop appears once per book per side, so with four books and both sides it occupies
    eight rows of a table that is meant to be read top-down. Ranking then interleaves
    duplicates of one bet with genuinely different ones.

    The row kept is the one with the largest edge, which is the right answer for a bettor
    rather than an arbitrary tie-break: across books it is the best available price, and
    across differing numbers it is the softest line. Both are line shopping, and taking the
    maximum performs it. `books_posting` on the surviving row still records how many books
    were seen, so nothing about coverage is lost.

    With `per_side` the Over and Under of a prop are kept separately -- still one row each
    rather than one per book. Otherwise only the side carrying the edge survives, since the
    two sides of a number are the same question asked from opposite ends and listing both
    puts every pick's mirror at the far end of the ranking.
    """
    best: dict[tuple, GradedProp] = {}
    for g in graded:
        key = (g.player_id or g.player_name, g.market.value)
        if per_side:
            key += (g.side,)
        current = best.get(key)
        if current is None or g.edge > current.edge:
            best[key] = g
    keep = {id(g) for g in best.values()}
    return [g for g in graded if id(g) in keep]


def rank(graded: list[GradedProp]) -> list[GradedProp]:
    """Best first: grade, then how far past the bar the edge sits.

    Ranking on `edge_ratio` rather than raw edge is what keeps the three markets
    comparable -- a 4-point edge on receiving outranks a 6-point edge on passing,
    because passing has to clear a much higher bar to mean the same thing.
    """
    order = {Grade.A: 0, Grade.B: 1, Grade.C: 2, Grade.D: 3}
    return sorted(graded, key=lambda g: (order[g.grade], -g.edge_ratio))
