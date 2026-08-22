"""Odds conversion, vig removal, consensus pricing, EV, and Kelly staking.

This is the layer that needs no predictive model to be correct, which is why the app
treats it as the reliable base and the projection model as a second opinion.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from statistics import median
from typing import Protocol


# --------------------------------------------------------------------------- odds
def american_to_decimal(american: int | float) -> float:
    """Convert American odds to decimal (including stake)."""
    a = float(american)
    if a == 0:
        raise ValueError("american odds of 0 are not a price")
    return 1.0 + (a / 100.0 if a > 0 else 100.0 / abs(a))


def decimal_to_american(decimal_odds: float) -> int:
    if decimal_odds <= 1.0:
        raise ValueError("decimal odds must exceed 1.0")
    b = decimal_odds - 1.0
    return round(b * 100) if b >= 1.0 else -round(100.0 / b)


def decimal_to_implied(decimal_odds: float) -> float:
    if decimal_odds <= 1.0:
        raise ValueError("decimal odds must exceed 1.0")
    return 1.0 / decimal_odds


def american_to_implied(american: int | float) -> float:
    return decimal_to_implied(american_to_decimal(american))


def implied_to_decimal(prob: float) -> float:
    if not 0.0 < prob < 1.0:
        raise ValueError("probability must be strictly between 0 and 1")
    return 1.0 / prob


# --------------------------------------------------------------------------- devig
class DevigMethod(Protocol):
    """Strategy for stripping the bookmaker's margin from a set of implied probs."""

    def __call__(self, implied: list[float]) -> list[float]: ...


def multiplicative_devig(implied: list[float]) -> list[float]:
    """Scale implied probabilities so they sum to 1.

    Removes the overround proportionally. The v1 default: simple, stable, and
    unbiased when favourite-longshot bias is mild. Power and Shin methods handle
    that bias better and belong behind this same interface later.
    """
    if not implied:
        return []
    if any(p <= 0 for p in implied):
        raise ValueError("implied probabilities must be positive")
    return [p / sum(implied) for p in implied]


def power_devig(implied: list[float], *, tolerance: float = 1e-9, max_iter: int = 100) -> list[float]:
    """Solve for k such that sum(p_i ** k) == 1.

    Shrinks longshots more than favourites, which better matches observed bias.
    Offered as an alternative strategy; not the default until it is validated here.
    """
    if not implied:
        return []
    if any(p <= 0 for p in implied):
        raise ValueError("implied probabilities must be positive")

    lo, hi = 0.5, 3.0
    for _ in range(max_iter):
        k = (lo + hi) / 2.0
        total = sum(p**k for p in implied)
        if abs(total - 1.0) < tolerance:
            break
        if total > 1.0:
            lo = k
        else:
            hi = k
    k = (lo + hi) / 2.0
    devigged = [p**k for p in implied]
    total = sum(devigged)
    return [p / total for p in devigged]


def overround(implied: list[float]) -> float:
    """The bookmaker's margin: how far the implied probabilities sum past 1."""
    return sum(implied) - 1.0


# ----------------------------------------------------------------- consensus / EV
@dataclass(frozen=True)
class BookPrice:
    bookmaker: str
    outcome: str
    american: int
    point: float | None = None

    @property
    def decimal(self) -> float:
        return american_to_decimal(self.american)

    @property
    def implied(self) -> float:
        return decimal_to_implied(self.decimal)


# A book whose implied probability sits this far from the median across books is
# treated as bad data, not as an opportunity.
#
# This exists because of a real case: on an F5 moneyline, seven books priced Houston
# between -175 and -186 while one book had them at +135 -- the two sides inverted. The
# median fair probability shrugged that off, but best-price selection grabbed it and
# reported a +43% edge. Devig was robust; price selection was not.
OUTLIER_PROB_TOLERANCE = 0.15


@dataclass(frozen=True)
class ConsensusOutcome:
    outcome: str
    fair_prob: float
    book_count: int
    best_book: str
    best_american: int
    best_decimal: float
    point: float | None
    # Books discarded as inconsistent with the consensus, surfaced so a quarantined
    # price is visible rather than silently dropped.
    outliers: tuple[str, ...] = ()


def consensus(
    prices: list[BookPrice], *, method: DevigMethod = multiplicative_devig
) -> list[ConsensusOutcome]:
    """Devig each book independently, then take the median fair probability per outcome.

    Median rather than mean: one stale or mispriced book should not drag the consensus.
    Books that do not price every outcome are skipped, since a partial market cannot
    be devigged coherently.
    """
    if not prices:
        return []

    outcomes: list[str] = []
    for p in prices:
        if p.outcome not in outcomes:
            outcomes.append(p.outcome)

    by_book: dict[str, dict[str, BookPrice]] = {}
    for p in prices:
        by_book.setdefault(p.bookmaker, {})[p.outcome] = p

    fair_by_outcome: dict[str, list[float]] = {o: [] for o in outcomes}
    for book_prices in by_book.values():
        if len(book_prices) != len(outcomes):
            continue  # incomplete market at this book
        ordered = [book_prices[o] for o in outcomes]
        devigged = method([p.implied for p in ordered])
        for outcome, prob in zip(outcomes, devigged, strict=True):
            fair_by_outcome[outcome].append(prob)

    results: list[ConsensusOutcome] = []
    for outcome in outcomes:
        probs = fair_by_outcome[outcome]
        if not probs:
            continue
        candidates = [p for p in prices if p.outcome == outcome]

        # Quarantine prices that contradict the consensus before shopping for the best
        # one. Without this, one inverted or stale book becomes the "best price" and
        # manufactures an edge that cannot be bet.
        reference = median([p.implied for p in candidates])
        keepers = [p for p in candidates if abs(p.implied - reference) <= OUTLIER_PROB_TOLERANCE]
        discarded = tuple(
            p.bookmaker for p in candidates if abs(p.implied - reference) > OUTLIER_PROB_TOLERANCE
        )
        if not keepers:
            keepers = candidates
            discarded = ()

        best = max(keepers, key=lambda p: p.decimal)
        results.append(
            ConsensusOutcome(
                outcome=outcome,
                fair_prob=median(probs),
                book_count=len(probs),
                best_book=best.bookmaker,
                best_american=best.american,
                best_decimal=best.decimal,
                point=best.point,
                outliers=discarded,
            )
        )
    return results


def expected_value(fair_prob: float, decimal_odds: float) -> float:
    """EV per unit staked: p*(b) - (1-p), where b is net decimal odds."""
    b = decimal_odds - 1.0
    return fair_prob * b - (1.0 - fair_prob)


def kelly_fraction(fair_prob: float, decimal_odds: float) -> float:
    """Full-Kelly stake as a fraction of bankroll. Negative means no bet."""
    b = decimal_odds - 1.0
    if b <= 0:
        return 0.0
    return (b * fair_prob - (1.0 - fair_prob)) / b


def quarter_kelly(fair_prob: float, decimal_odds: float) -> float:
    """Quarter Kelly, floored at zero.

    Full Kelly is not a stake size anyone should actually use: it assumes the
    probability estimate is exact, and drawdowns are brutal when it is not.
    """
    return max(0.0, kelly_fraction(fair_prob, decimal_odds)) / 4.0


def no_vig_fair_american(implied_pair: list[float]) -> list[int]:
    """Convenience: devig a two-way market and express the fair prices in American odds."""
    return [decimal_to_american(implied_to_decimal(p)) for p in multiplicative_devig(implied_pair)]


# ---------------------------------------------------------------- distributions
def normal_cdf(x: float, mu: float = 0.0, sigma: float = 1.0) -> float:
    if sigma <= 0:
        raise ValueError("sigma must be positive")
    return 0.5 * (1.0 + math.erf((x - mu) / (sigma * math.sqrt(2.0))))


def prob_over(line: float, mu: float, sigma: float) -> float:
    """P(total > line) for a Normal total.

    Sportsbook totals commonly land on a half point, which avoids pushes; when a line
    is a whole number a push is possible and this slightly overstates the over.
    """
    return 1.0 - normal_cdf(line, mu, sigma)
