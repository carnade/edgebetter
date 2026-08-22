"""Player prop projections. Currently pitcher strikeouts.

Strikeouts are the most modelable prop in baseball: the rate is a stable pitcher skill,
the opponent effect is large and measurable, and the only real uncertainty is how long
the starter lasts. Every input is already in our database.

The opponent adjustment is not a rounding detail -- team strikeout rates span 18.7% to
25.4% against a 22.1% league average, so the same pitcher faces a ~15% swing in
strikeout environment depending on the opponent.
"""

from __future__ import annotations

import math
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PitcherSeasonStats, Sport, StatSource, TeamSeasonStats
from app.services.projections_mlb import DEFAULT_STARTER_INNINGS, innings_per_start

# Fallback when the league rate cannot be computed from stored stats.
LEAGUE_K_RATE = 0.221

# Measured, not assumed. Strikeouts are commonly described as overdispersed, so this
# started at 1.15 -- but the walk-forward backtest over 4,094 starts scored Poisson
# ahead of negative binomial on log loss (0.6808 vs 0.6814), so the extra variance is
# not supported by the data. 1.0 makes _nb_pmf collapse to Poisson exactly.
# Re-derive with `cli backtest strikeouts` before changing this.
DISPERSION = 1.0

# Support cap for the discrete distribution. The record for a nine-inning start is 20,
# so this sits far past anything reachable -- high enough that truncated tail mass is
# negligible rather than merely small.
MAX_STRIKEOUTS = 32


@dataclass(frozen=True)
class StrikeoutProjection:
    pitcher_name: str
    expected: float
    expected_innings: float
    k_per_9: float
    opponent_k_rate: float
    league_k_rate: float

    @property
    def opponent_factor(self) -> float:
        return self.opponent_k_rate / self.league_k_rate if self.league_k_rate else 1.0

    def prob_over(self, line: float) -> float:
        """P(strikeouts > line). Half-point lines avoid pushes; whole numbers can push."""
        return prob_over_line(self.expected, line)


def _nb_pmf(k: int, mean: float, dispersion: float) -> float:
    """Negative binomial by mean and variance ratio.

    With dispersion == 1 this reduces to Poisson, so the model degrades cleanly if the
    backtest says the extra variance is unwarranted.
    """
    if mean <= 0:
        return 1.0 if k == 0 else 0.0
    if dispersion <= 1.0:
        return math.exp(-mean + k * math.log(mean) - math.lgamma(k + 1))
    variance = mean * dispersion
    r = mean * mean / (variance - mean)
    p = r / (r + mean)
    return math.exp(
        math.lgamma(k + r) - math.lgamma(r) - math.lgamma(k + 1)
        + r * math.log(p) + k * math.log(1 - p)
    )


def distribution(
    mean: float, *, dispersion: float = DISPERSION, max_count: int = MAX_STRIKEOUTS
) -> list[float]:
    """PMF over 0..max_count. `max_count` is a parameter because the NFL count props
    reuse this: receptions and carries have their own reachable ranges."""
    return [_nb_pmf(k, mean, dispersion) for k in range(max_count + 1)]


def prob_over_line(
    mean: float,
    line: float,
    *,
    dispersion: float = DISPERSION,
    max_count: int = MAX_STRIKEOUTS,
) -> float:
    """P(count > line), summed over the integers that clear it.

    Summing rather than integrating is the whole point for these markets: at a 2.5 line
    almost all the probability sits on a few integers, and a continuous approximation
    misprices exactly the numbers people bet.
    """
    dist = distribution(mean, dispersion=dispersion, max_count=max_count)
    return sum(p for k, p in enumerate(dist) if k > line)


def expected_strikeouts(
    k_per_9: float, expected_innings: float, opponent_k_rate: float, league_k_rate: float
) -> float:
    """Rate x workload, scaled by how much this opponent strikes out."""
    base = k_per_9 * expected_innings / 9.0
    factor = opponent_k_rate / league_k_rate if league_k_rate else 1.0
    return base * factor


def league_k_rate(session: Session, season: int) -> float:
    rows = session.scalars(
        select(TeamSeasonStats).where(
            TeamSeasonStats.season == season,
            TeamSeasonStats.sport == Sport.MLB,
            TeamSeasonStats.source == StatSource.MLB_STATSAPI,
        )
    ).all()
    values = [r.strikeout_rate for r in rows if r.strikeout_rate]
    return sum(values) / len(values) if values else LEAGUE_K_RATE


def project_strikeouts(
    session: Session, pitcher_id: int, opponent_team_id: int, season: int
) -> StrikeoutProjection | None:
    """Project one starter's strikeouts against a specific opponent."""
    stats = session.scalar(
        select(PitcherSeasonStats).where(
            PitcherSeasonStats.player_id == pitcher_id, PitcherSeasonStats.season == season
        )
    )
    if stats is None or not stats.k_per_9:
        return None

    opponent = session.scalar(
        select(TeamSeasonStats).where(
            TeamSeasonStats.team_id == opponent_team_id,
            TeamSeasonStats.season == season,
            TeamSeasonStats.source == StatSource.MLB_STATSAPI,
        )
    )
    opp_rate = (opponent.strikeout_rate if opponent else None) or LEAGUE_K_RATE
    league = league_k_rate(session, season)

    raw = stats.raw or {}
    try:
        games_pitched = int(raw.get("gamesPitched"))
    except (TypeError, ValueError):
        games_pitched = None
    innings = (
        innings_per_start(stats.innings_pitched, stats.games_started, games_pitched)
        or DEFAULT_STARTER_INNINGS
    )

    from app.models import Player

    player = session.get(Player, pitcher_id)
    return StrikeoutProjection(
        pitcher_name=player.full_name if player else "",
        expected=expected_strikeouts(stats.k_per_9, innings, opp_rate, league),
        expected_innings=innings,
        k_per_9=stats.k_per_9,
        opponent_k_rate=opp_rate,
        league_k_rate=league,
    )
