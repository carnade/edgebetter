"""First-half and first-quarter scoring.

Partial-game markets attract far less money than full-game sides and totals, which is the
usual reason to look at them. The specific claim worth testing is that opening drives are
scripted, so early scoring should be more predictable than the rest of the game.

That claim is testable rather than assumable: if the first half were more predictable
than the second, its share of the game total would vary less. This module measures that
directly instead of taking it on faith.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NflGame, NflTeamGame
from app.services.stats import Rate, mean_and_interval, rate

log = logging.getLogger(__name__)


@dataclass
class PartialProfile:
    """How scoring distributes across a game, and how stable that split is."""

    games: int
    first_quarter_mean: float
    first_half_mean: float
    second_half_mean: float
    full_mean: float

    first_half_share: float
    first_half_share_sd: float
    second_half_share_sd: float

    # Predictability check: coefficient of variation of each half's points.
    first_half_cv: float
    second_half_cv: float

    scoreless_first_quarter: Rate
    first_half_over_20: Rate

    @property
    def first_half_more_stable(self) -> bool:
        return self.first_half_cv < self.second_half_cv

    def summary(self) -> str:
        lines = [
            f"games                {self.games}",
            "",
            f"1Q mean              {self.first_quarter_mean:.2f} points",
            f"1H mean              {self.first_half_mean:.2f}",
            f"2H mean              {self.second_half_mean:.2f}",
            f"full game            {self.full_mean:.2f}",
            "",
            f"1H share of total    {self.first_half_share:.1%} "
            f"(SD {self.first_half_share_sd:.1%})",
            "",
            "IS THE FIRST HALF MORE PREDICTABLE?",
            f"  1H variability     {self.first_half_cv:.3f}  (CV of points)",
            f"  2H variability     {self.second_half_cv:.3f}",
        ]
        if self.first_half_more_stable:
            lines.append("  First half is the steadier half, as the scripted-drives idea predicts.")
        else:
            lines.append(
                "  Second half is at least as steady. The scripted-drives idea does not"
            )
            lines.append("  show up in the data, so partial markets are not inherently safer.")

        lines += [
            "",
            f"scoreless 1Q (team)  {self.scoreless_first_quarter.format()}",
            f"1H total over 20     {self.first_half_over_20.format()}",
        ]
        return "\n".join("  " + line for line in lines)


def profile(session: Session, *, season: int | None = None) -> PartialProfile:
    """Measure how scoring distributes across a game."""
    stmt = select(NflGame).where(
        NflGame.game_type == "REG",
        NflGame.home_score.is_not(None),
        NflGame.home_first_half.is_not(None),
    )
    if season:
        stmt = stmt.where(NflGame.season == season)
    games = session.scalars(stmt).all()
    if not games:
        raise ValueError("no games with derived partial scores; run the pbp ingest")

    first_q: list[float] = []
    first_h: list[float] = []
    second_h: list[float] = []
    full: list[float] = []
    shares: list[float] = []
    second_shares: list[float] = []
    over_20 = 0

    for g in games:
        fh = (g.home_first_half or 0) + (g.away_first_half or 0)
        fq = (g.home_first_quarter or 0) + (g.away_first_quarter or 0)
        total = (g.home_score or 0) + (g.away_score or 0)
        sh = total - fh
        if total <= 0:
            continue

        first_q.append(fq)
        first_h.append(fh)
        second_h.append(sh)
        full.append(total)
        shares.append(fh / total)
        second_shares.append(sh / total)
        if fh > 20:
            over_20 += 1

    def cv(values: list[float]) -> float:
        mean = statistics.fmean(values)
        return statistics.pstdev(values) / mean if mean else 0.0

    # Team-level rows for the scoreless-quarter rate.
    team_rows = session.scalars(
        select(NflTeamGame).where(NflTeamGame.first_quarter_points.is_not(None))
    ).all()
    if season:
        team_rows = [r for r in team_rows if r.season == season]
    scoreless = sum(1 for r in team_rows if r.first_quarter_points == 0)

    return PartialProfile(
        games=len(full),
        first_quarter_mean=statistics.fmean(first_q),
        first_half_mean=statistics.fmean(first_h),
        second_half_mean=statistics.fmean(second_h),
        full_mean=statistics.fmean(full),
        first_half_share=statistics.fmean(shares),
        first_half_share_sd=statistics.pstdev(shares),
        second_half_share_sd=statistics.pstdev(second_shares),
        first_half_cv=cv(first_h),
        second_half_cv=cv(second_h),
        scoreless_first_quarter=rate(scoreless, len(team_rows)),
        first_half_over_20=rate(over_20, len(full)),
    )


def first_half_line_estimate(session: Session, total_line: float) -> tuple[float, float, float]:
    """Convert a full-game total into an implied first-half total.

    Books price first-half totals near half the game number; this returns what the
    historical share actually implies, with an interval, so the two can be compared.
    """
    prof = profile(session)
    share = prof.first_half_share
    sd = prof.first_half_share_sd
    return (
        total_line * share,
        total_line * (share - 1.96 * sd / (prof.games**0.5)),
        total_line * (share + 1.96 * sd / (prof.games**0.5)),
    )
