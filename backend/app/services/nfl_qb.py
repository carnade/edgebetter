"""Backup quarterback starts, and what they are actually worth.

A quarterback change is widely held to be the largest single-factor swing in football,
and unlike most such claims it is cleanly measurable here: nflverse records the starting
QB for every game, so "this team's usual starter is not playing" is a fact rather than an
inference.

The question this module answers is narrower than the folklore: the market knows about
the change too, and moves the line for it. So the useful measurement is not "do backups
lose more" -- obviously they do -- but **whether the line moves far enough**. That is a
question about closing lines, not about wins.
"""

from __future__ import annotations

import logging
from collections import Counter, defaultdict
from dataclasses import dataclass, field

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NflGame, NflTeamGame
from app.services.stats import BREAK_EVEN_110, Rate, mean_and_interval, rate

log = logging.getLogger(__name__)

# Starts a quarterback needs in a season before we treat them as that team's starter.
STARTER_MIN_STARTS = 6


@dataclass
class QbSeason:
    """Who actually started for a team in a season."""

    team: str
    season: int
    counts: Counter = field(default_factory=Counter)

    @property
    def primary(self) -> str | None:
        if not self.counts:
            return None
        name, starts = self.counts.most_common(1)[0]
        return name if starts >= STARTER_MIN_STARTS else None

    def is_backup(self, qb: str | None) -> bool | None:
        """True when this QB is not the team's established starter for the season.

        Returns None rather than False when the team has no established starter -- a
        club that rotated three quarterbacks all year has no "backup" to speak of, and
        counting those games either way would distort the measurement.
        """
        primary = self.primary
        if primary is None or not qb:
            return None
        return qb != primary


def qb_seasons(session: Session) -> dict[tuple[str, int], QbSeason]:
    """Map (team, season) to who started, from completed games only."""
    rows = session.scalars(
        select(NflTeamGame).where(
            NflTeamGame.points_for.is_not(None), NflTeamGame.qb_name.is_not(None)
        )
    ).all()

    out: dict[tuple[str, int], QbSeason] = {}
    for row in rows:
        key = (row.team, row.season)
        record = out.setdefault(key, QbSeason(team=row.team, season=row.season))
        record.counts[row.qb_name] += 1
    return out


@dataclass
class QbImpact:
    """What changes when a backup starts."""

    backup_games: int
    starter_games: int

    backup_points: float
    starter_points: float
    backup_points_ci: tuple[float, float]

    backup_spread: float
    starter_spread: float

    backup_ats: Rate
    starter_ats: Rate
    backup_over: Rate

    @property
    def points_swing(self) -> float:
        return self.backup_points - self.starter_points

    @property
    def line_swing(self) -> float:
        """How much more the market handicaps a team starting a backup.

        Spreads are stored from the team's perspective and negative when favoured, so a
        positive swing means the market made them a bigger underdog.
        """
        return self.backup_spread - self.starter_spread

    @property
    def market_overreacts(self) -> bool | None:
        """Did the line move further than the scoring drop justified?

        The ATS rate answers this directly: if backups still cover about half the time,
        the market priced the change correctly. Only meaningful with a real sample.
        """
        if not self.backup_ats.band.trustworthy:
            return None
        return self.backup_ats.rate > 0.5

    def summary(self) -> str:
        lines = [
            f"backup starts        {self.backup_games}",
            f"established starters {self.starter_games}",
            "",
            f"points scored        {self.backup_points:.1f} with backup "
            f"vs {self.starter_points:.1f} with starter  ({self.points_swing:+.1f})",
            f"  backup 95% CI      [{self.backup_points_ci[0]:.1f}, {self.backup_points_ci[1]:.1f}]",
            "",
            f"market handicap      {self.backup_spread:+.1f} with backup "
            f"vs {self.starter_spread:+.1f} with starter  ({self.line_swing:+.1f})",
            "",
            f"backup ATS           {self.backup_ats.format()}",
            f"starter ATS          {self.starter_ats.format()}",
            f"backup game overs    {self.backup_over.format()}",
        ]

        lines += ["", "READ"]
        if self.backup_games < 30:
            lines.append("  Too few backup starts to conclude anything.")
        elif self.market_overreacts is True:
            lines.append(
                f"  Backups covered {self.backup_ats.rate:.1%} of the time, so the market "
                "moved the line"
            )
            lines.append(
                "  further than the drop in scoring justified -- it overreacts to the news."
            )
            if not self.backup_ats.beats_break_even:
                lines.append(
                    "  The interval still straddles break-even, so this is a lean, not an edge."
                )
        else:
            fade = 1.0 - self.backup_ats.rate
            fade_lower = 1.0 - self.backup_ats.upper
            lines.append(
                f"  Backups covered {self.backup_ats.rate:.1%}, so fading them went "
                f"{fade:.1%}."
            )
            lines.append(
                f"  Break-even is {BREAK_EVEN_110:.1%}, and the lower bound of that fade is "
                f"{fade_lower:.1%},"
            )
            lines.append(
                "  so the interval straddles break-even: a lean at best, not an edge."
            )
            lines.append(
                f"  The market moved {abs(self.line_swing):.1f} points for a "
                f"{abs(self.points_swing):.1f}-point scoring drop -- close to right."
            )
        return "\n".join("  " + line for line in lines)


def analyse_qb_impact(session: Session) -> QbImpact:
    """Compare backup starts against established-starter games on the same measures."""
    seasons = qb_seasons(session)

    rows = session.scalars(
        select(NflTeamGame).where(
            NflTeamGame.points_for.is_not(None), NflTeamGame.qb_name.is_not(None)
        )
    ).all()

    backup: list[NflTeamGame] = []
    starter: list[NflTeamGame] = []
    for row in rows:
        record = seasons.get((row.team, row.season))
        if record is None:
            continue
        flag = record.is_backup(row.qb_name)
        if flag is True:
            backup.append(row)
        elif flag is False:
            starter.append(row)

    def points(sample: list[NflTeamGame]) -> list[float]:
        return [float(r.points_for) for r in sample if r.points_for is not None]

    def spreads(sample: list[NflTeamGame]) -> list[float]:
        return [float(r.team_spread) for r in sample if r.team_spread is not None]

    def ats(sample: list[NflTeamGame]) -> Rate:
        eligible = [r for r in sample if r.covered is not None and not r.push_spread]
        return rate(sum(1 for r in eligible if r.covered), len(eligible))

    def overs(sample: list[NflTeamGame]) -> Rate:
        eligible = [r for r in sample if r.went_over is not None and not r.push_total]
        return rate(sum(1 for r in eligible if r.went_over), len(eligible))

    backup_pts, backup_lo, backup_hi = mean_and_interval(points(backup))
    starter_pts, _, _ = mean_and_interval(points(starter))
    backup_spreads = spreads(backup)
    starter_spreads = spreads(starter)

    return QbImpact(
        backup_games=len(backup),
        starter_games=len(starter),
        backup_points=backup_pts,
        starter_points=starter_pts,
        backup_points_ci=(backup_lo, backup_hi),
        backup_spread=sum(backup_spreads) / len(backup_spreads) if backup_spreads else 0.0,
        starter_spread=sum(starter_spreads) / len(starter_spreads) if starter_spreads else 0.0,
        backup_ats=ats(backup),
        starter_ats=ats(starter),
        backup_over=overs(backup),
    )


def upcoming_qb_changes(session: Session, season: int, week: int) -> list[dict]:
    """Teams whose listed starter differs from last season's primary.

    Useful before a season opens, when the only signal available is who is listed.
    """
    seasons = qb_seasons(session)
    games = session.scalars(
        select(NflGame).where(
            NflGame.season == season, NflGame.week == week, NflGame.game_type == "REG"
        )
    ).all()

    flagged: list[dict] = []
    for game in games:
        for team, qb, opponent in (
            (game.home_team, game.home_qb_name, game.away_team),
            (game.away_team, game.away_qb_name, game.home_team),
        ):
            if not qb:
                continue
            prior = seasons.get((team, season - 1))
            previous = prior.primary if prior else None
            if previous and qb != previous:
                flagged.append(
                    {
                        "game_id": game.game_id,
                        "team": team,
                        "opponent": opponent,
                        "listed_qb": qb,
                        "previous_starter": previous,
                    }
                )
    return flagged
