"""Statistical guards for conditional base rates.

NFL gives us about 1,615 completed games from 2020 on. Conditioning cuts that fast:
outdoor games with wind above 15mph in divisional December matchups is roughly a dozen
games, and in a dozen games you can always find a 9-3 record that looks like an edge.

Everything here exists to stop that from being mistaken for a finding:

- `wilson_interval` reports the uncertainty around a rate rather than a bare percentage.
- `sample_band` labels how much weight a sample can bear at all.
- `holdout_compare` checks whether a pattern found in one period survives in another,
  which is the only real defence against multiple comparisons. Test fifty splits at
  p<0.05 and roughly two look significant by chance alone.

Break-even for a standard -110 line is 52.4%, so that is the reference point a rate has
to clear, not 50%.
"""

from __future__ import annotations

import math
from dataclasses import dataclass
from enum import Enum

# Standard American odds of -110 need this hit rate to break even.
BREAK_EVEN_110 = 110 / 210  # 0.5238...

# Sample thresholds. Separating 55% from a coin flip needs roughly 400 observations;
# separating 60% needs about 100. These bands encode that reality rather than leaving
# it to intuition.
NOISE_MAX = 30
SUGGESTIVE_MAX = 100
MEANINGFUL_MIN = 300


class Band(str, Enum):
    NOISE = "noise"
    SUGGESTIVE = "suggestive"
    MODERATE = "moderate"
    MEANINGFUL = "meaningful"

    @property
    def trustworthy(self) -> bool:
        return self in (Band.MODERATE, Band.MEANINGFUL)


def sample_band(n: int) -> Band:
    """How much weight a sample of this size can bear."""
    if n < NOISE_MAX:
        return Band.NOISE
    if n < SUGGESTIVE_MAX:
        return Band.SUGGESTIVE
    if n < MEANINGFUL_MIN:
        return Band.MODERATE
    return Band.MEANINGFUL


@dataclass(frozen=True)
class Rate:
    """A hit rate with its uncertainty, never a bare percentage."""

    hits: int
    n: int
    lower: float
    upper: float
    band: Band

    @property
    def rate(self) -> float:
        return self.hits / self.n if self.n else 0.0

    @property
    def width(self) -> float:
        return self.upper - self.lower

    @property
    def beats_break_even(self) -> bool:
        """True only when the whole interval clears the -110 break-even.

        A point estimate above 52.4% means nothing if the interval straddles it, which
        is the case for almost every small split.
        """
        return self.lower > BREAK_EVEN_110

    @property
    def verdict(self) -> str:
        if self.band is Band.NOISE:
            return "too few games to say anything"
        if self.beats_break_even:
            return "clears break-even"
        if self.upper < BREAK_EVEN_110:
            return "below break-even"
        return "indistinguishable from break-even"

    def format(self) -> str:
        return (
            f"{self.rate:.1%} ({self.hits}/{self.n})  "
            f"CI [{self.lower:.1%}, {self.upper:.1%}]  {self.band.value}"
        )


def wilson_interval(hits: int, n: int, z: float = 1.96) -> tuple[float, float]:
    """Wilson score interval for a binomial proportion.

    Preferred over the normal approximation because it stays inside [0, 1] and behaves
    sensibly at small n and extreme rates -- exactly the conditions conditional splits
    produce. The normal approximation would happily report an interval above 100% on a
    9-3 record, which is where naive splits analysis starts lying.
    """
    if n <= 0:
        return 0.0, 1.0
    if hits < 0 or hits > n:
        raise ValueError(f"hits ({hits}) must be between 0 and n ({n})")

    p = hits / n
    denominator = 1 + z * z / n
    centre = (p + z * z / (2 * n)) / denominator
    margin = (z / denominator) * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n))
    return max(0.0, centre - margin), min(1.0, centre + margin)


def rate(hits: int, n: int, z: float = 1.96) -> Rate:
    lower, upper = wilson_interval(hits, n, z)
    return Rate(hits=hits, n=n, lower=lower, upper=upper, band=sample_band(n))


@dataclass(frozen=True)
class HoldoutResult:
    """The same split measured in two periods.

    A pattern that appears in the exploration period and vanishes out of sample was
    noise. This is the check that separates a finding from a coincidence.
    """

    explore: Rate
    holdout: Rate
    label: str = ""

    @property
    def direction_held(self) -> bool:
        """Both periods land on the same side of break-even."""
        if self.holdout.n == 0:
            return False
        explore_side = self.explore.rate > BREAK_EVEN_110
        holdout_side = self.holdout.rate > BREAK_EVEN_110
        return explore_side == holdout_side

    @property
    def gap(self) -> float:
        return self.holdout.rate - self.explore.rate

    @property
    def status(self) -> str:
        if self.holdout.n < NOISE_MAX:
            return "no holdout sample"
        if not self.direction_held:
            return "reversed out of sample"
        if abs(self.gap) > 0.15:
            return "held direction but moved sharply"
        return "held"

    @property
    def survives(self) -> bool:
        """Deliberately strict: a pattern must hold direction and have a real sample."""
        return (
            self.holdout.n >= NOISE_MAX
            and self.direction_held
            and self.explore.band.trustworthy
        )


def holdout_compare(
    explore_hits: int, explore_n: int, holdout_hits: int, holdout_n: int, label: str = ""
) -> HoldoutResult:
    return HoldoutResult(
        explore=rate(explore_hits, explore_n),
        holdout=rate(holdout_hits, holdout_n),
        label=label,
    )


def required_sample(effect: float, base: float = 0.5, z: float = 1.96) -> int:
    """Roughly how many observations are needed to detect a rate this far from `base`.

    Useful for telling a user up front that the split they want cannot be answered,
    rather than answering it badly.
    """
    if effect <= 0:
        raise ValueError("effect must be positive")
    target = base + effect
    variance = target * (1 - target)
    return max(1, math.ceil(variance * (z / effect) ** 2))


def mean_and_interval(values: list[float], z: float = 1.96) -> tuple[float, float, float]:
    """(mean, lower, upper) for a continuous quantity such as points scored."""
    n = len(values)
    if n == 0:
        return 0.0, 0.0, 0.0
    mean = sum(values) / n
    if n == 1:
        return mean, mean, mean
    variance = sum((v - mean) ** 2 for v in values) / (n - 1)
    margin = z * math.sqrt(variance / n)
    return mean, mean - margin, mean + margin
