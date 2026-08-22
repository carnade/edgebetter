"""Conditional base rates: "under these conditions, what has historically happened?"

This is the core of the NFL tool and the part most likely to mislead if built naively.
There are only ~1,600 completed games from 2020 on, and each condition applied cuts that
sample hard. Every result therefore carries three things the raw percentage does not:

- a Wilson interval, so the uncertainty is visible;
- a sample band, so a 12-game "pattern" is labelled as noise rather than ranked as a find;
- a holdout comparison against a season excluded from exploration, which is the only real
  defence against testing many splits and keeping whichever looked best.

Break-even is 52.4% at standard -110 pricing, not 50%. A 54% hit rate is not an edge, and
the verdict wording reflects that.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import Select, and_, select
from sqlalchemy.orm import Session

from app.models import NflTeamGame
from app.services.stats import (
    BREAK_EVEN_110,
    HoldoutResult,
    Rate,
    holdout_compare,
    mean_and_interval,
    rate,
)

log = logging.getLogger(__name__)

# The season held out of exploration. Anything discovered on earlier seasons must still
# hold here before it is believed.
HOLDOUT_SEASON = 2025

MARKETS = ("over", "cover", "team_total", "first_half")


@dataclass
class Filters:
    """Conditions a split can be taken on.

    Deliberately a small, motivated set. An open-ended filter builder would invite
    dredging through hundreds of combinations until something looked significant.
    """

    seasons: list[int] | None = None
    wind_min: float | None = None
    wind_max: float | None = None
    temp_min: float | None = None
    temp_max: float | None = None
    roof: str | None = None
    surface: str | None = None
    outdoor_only: bool = False
    div_game: bool | None = None
    is_home: bool | None = None
    is_favourite: bool | None = None
    rest_advantage_min: int | None = None
    team: str | None = None
    opponent: str | None = None
    # Team total threshold, e.g. 24.5 points.
    team_total_line: float = 23.5

    def describe(self) -> str:
        parts: list[str] = []
        if self.outdoor_only:
            parts.append("outdoor")
        if self.roof:
            parts.append(f"roof={self.roof}")
        if self.surface:
            parts.append(f"surface={self.surface}")
        if self.wind_min is not None:
            parts.append(f"wind>={self.wind_min:g}")
        if self.wind_max is not None:
            parts.append(f"wind<={self.wind_max:g}")
        if self.temp_min is not None:
            parts.append(f"temp>={self.temp_min:g}")
        if self.temp_max is not None:
            parts.append(f"temp<={self.temp_max:g}")
        if self.div_game is not None:
            parts.append("divisional" if self.div_game else "non-divisional")
        if self.is_home is not None:
            parts.append("home" if self.is_home else "away")
        if self.is_favourite is not None:
            parts.append("favourite" if self.is_favourite else "underdog")
        if self.rest_advantage_min is not None:
            parts.append(f"rest_adv>={self.rest_advantage_min}")
        if self.team:
            parts.append(f"team={self.team}")
        if self.opponent:
            parts.append(f"opp={self.opponent}")
        return ", ".join(parts) or "all games"


def _apply(stmt: Select, f: Filters, seasons: list[int] | None) -> Select:
    conditions: list[Any] = [NflTeamGame.points_for.is_not(None)]

    if seasons:
        conditions.append(NflTeamGame.season.in_(seasons))
    if f.outdoor_only:
        conditions.append(NflTeamGame.roof == "outdoors")
    if f.roof:
        conditions.append(NflTeamGame.roof == f.roof)
    if f.surface:
        conditions.append(NflTeamGame.surface == f.surface)
    # Weather filters implicitly restrict to games that have weather at all, which means
    # outdoor games -- indoor rows carry null temp and wind by design, not by omission.
    if f.wind_min is not None:
        conditions.append(NflTeamGame.wind >= f.wind_min)
    if f.wind_max is not None:
        conditions.append(NflTeamGame.wind <= f.wind_max)
    if f.temp_min is not None:
        conditions.append(NflTeamGame.temp >= f.temp_min)
    if f.temp_max is not None:
        conditions.append(NflTeamGame.temp <= f.temp_max)
    if f.div_game is not None:
        conditions.append(NflTeamGame.div_game.is_(f.div_game))
    if f.is_home is not None:
        conditions.append(NflTeamGame.is_home.is_(f.is_home))
    if f.is_favourite is not None:
        conditions.append(NflTeamGame.is_favourite.is_(f.is_favourite))
    if f.rest_advantage_min is not None:
        conditions.append(NflTeamGame.rest_advantage >= f.rest_advantage_min)
    if f.team:
        conditions.append(NflTeamGame.team == f.team)
    if f.opponent:
        conditions.append(NflTeamGame.opponent == f.opponent)

    return stmt.where(and_(*conditions))


def _fetch(session: Session, f: Filters, seasons: list[int] | None) -> list[NflTeamGame]:
    return list(session.scalars(_apply(select(NflTeamGame), f, seasons)).all())


@dataclass
class MarketSplit:
    market: str
    label: str
    result: Rate
    holdout: HoldoutResult | None = None
    # Context that a hit rate alone cannot convey.
    mean_value: float | None = None
    mean_low: float | None = None
    mean_high: float | None = None

    @property
    def verdict(self) -> str:
        return self.result.verdict

    def format(self) -> str:
        line = f"{self.label:22s} {self.result.format()}  -> {self.verdict}"
        if self.mean_value is not None:
            line += f"\n{'':22s} mean {self.mean_value:.1f} [{self.mean_low:.1f}, {self.mean_high:.1f}]"
        if self.holdout:
            line += f"\n{'':22s} holdout {HOLDOUT_SEASON}: {self.holdout.holdout.format()} -> {self.holdout.status}"
        return line


@dataclass
class SplitReport:
    description: str
    n_team_games: int
    markets: list[MarketSplit] = field(default_factory=list)

    def format(self) -> str:
        head = f"{self.description}  ({self.n_team_games} team-games)"
        return "\n".join([head, "-" * len(head), *(m.format() for m in self.markets)])


def _count(rows: list[NflTeamGame], predicate, valid) -> tuple[int, int]:
    """(hits, n) counting only rows where the market actually resolved.

    Pushes are excluded rather than counted as losses: a push returns the stake, so
    including them would understate every hit rate.
    """
    eligible = [r for r in rows if valid(r)]
    return sum(1 for r in eligible if predicate(r)), len(eligible)


def analyse(session: Session, f: Filters) -> SplitReport:
    """Run one set of conditions across every market, with holdout comparison."""
    explore_seasons = f.seasons
    if explore_seasons is None:
        all_seasons = list(
            session.scalars(select(NflTeamGame.season).distinct()).all()
        )
        explore_seasons = sorted(s for s in all_seasons if s != HOLDOUT_SEASON)

    explore_rows = _fetch(session, f, explore_seasons)
    holdout_rows = _fetch(session, f, [HOLDOUT_SEASON])

    report = SplitReport(description=f.describe(), n_team_games=len(explore_rows))

    definitions = [
        (
            "over",
            "Game total: over",
            lambda r: r.went_over is True,
            lambda r: r.went_over is not None and not r.push_total,
        ),
        (
            "cover",
            "Spread: covered",
            lambda r: r.covered is True,
            lambda r: r.covered is not None and not r.push_spread,
        ),
        (
            "team_total",
            f"Team total: over {f.team_total_line:g}",
            lambda r: r.points_for is not None and r.points_for > f.team_total_line,
            lambda r: r.points_for is not None,
        ),
        (
            "first_half",
            "1H: team scored 10+",
            lambda r: r.first_half_points is not None and r.first_half_points >= 10,
            lambda r: r.first_half_points is not None,
        ),
    ]

    for key, label, predicate, valid in definitions:
        hits, n = _count(explore_rows, predicate, valid)
        if n == 0:
            continue
        h_hits, h_n = _count(holdout_rows, predicate, valid)

        split = MarketSplit(
            market=key,
            label=label,
            result=rate(hits, n),
            holdout=holdout_compare(hits, n, h_hits, h_n, label) if h_n else None,
        )

        # For point-scoring markets the average is more informative than a hit rate:
        # it says how the condition moved scoring, not just which side of a line it fell.
        if key == "team_total":
            values = [float(r.points_for) for r in explore_rows if r.points_for is not None]
            split.mean_value, split.mean_low, split.mean_high = mean_and_interval(values)
        elif key == "over":
            values = [
                float(r.points_for + r.points_against)
                for r in explore_rows
                if r.points_for is not None and r.points_against is not None
            ]
            split.mean_value, split.mean_low, split.mean_high = mean_and_interval(values)

        report.markets.append(split)

    return report


def compare(session: Session, baseline: Filters, conditioned: Filters) -> str:
    """Contrast a condition against a baseline.

    A hit rate in isolation is hard to read -- 51% over could be normal or notable. The
    honest question is always "compared with what?", so this pairs the two.
    """
    base = analyse(session, baseline)
    cond = analyse(session, conditioned)

    lines = [
        f"baseline:    {base.description}  ({base.n_team_games} team-games)",
        f"conditioned: {cond.description}  ({cond.n_team_games} team-games)",
        "",
        f"{'market':24s} {'baseline':>20s} {'conditioned':>20s} {'diff':>8s}",
    ]
    base_by_market = {m.market: m for m in base.markets}
    for split in cond.markets:
        b = base_by_market.get(split.market)
        if b is None:
            continue
        diff = split.result.rate - b.result.rate
        lines.append(
            f"{split.label:24s} "
            f"{b.result.rate:>19.1%} "
            f"{split.result.rate:>19.1%} "
            f"{diff:>+8.1%}"
        )
        if split.mean_value is not None and b.mean_value is not None:
            lines.append(
                f"{'  mean points':24s} {b.mean_value:>19.1f} {split.mean_value:>19.1f} "
                f"{split.mean_value - b.mean_value:>+8.1f}"
            )
    return "\n".join(lines)
