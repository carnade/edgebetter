"""Scan a week's posted prop lines and grade every one.

Turns the props tool from a calculator into a scanner: instead of checking a prop you
already had in mind, this walks every line on the slate, projects the player, and ranks
what comes back.

All three markets go through identical analysis. They differ only in the bar each edge
must clear, which is set by that market's measured calibration error -- see
`nfl_prop_grades`.
"""

from __future__ import annotations

import logging
import statistics
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NflGame, NflPropLine
from app.services.nfl_prop_grades import GradedProp, grade_line, rank
from app.services.nfl_props import Market, project_prop

log = logging.getLogger(__name__)


@dataclass
class ScanResult:
    graded: list[GradedProp]
    lines_seen: int
    players_projected: int
    players_without_history: int
    week: int | None
    season: int | None
    # Games on the slate, against games we actually hold lines for. Derived from the data
    # rather than from the ingest run, so it catches every cause of a hole -- a poll cut
    # short by the credit floor, a book that never posted, an event id that did not match.
    games_in_week: int = 0
    games_with_lines: int = 0

    @property
    def actionable(self) -> list[GradedProp]:
        return [g for g in self.graded if g.grade.actionable]

    @property
    def side_skew(self) -> tuple[int, int]:
        """(overs, unders) among actionable picks."""
        picks = self.actionable
        overs = sum(1 for g in picks if g.side == "Over")
        return overs, len(picks) - overs

    @property
    def one_sided_warning(self) -> str | None:
        """Flag a scan that lands almost entirely on one side.

        Receiving yards are genuinely right-skewed -- a receiver's median game runs about
        75% of his average, which our distribution reproduces closely (0.767 modelled
        against 0.747 observed). So a lean toward unders is a real consequence of the
        shape, not a bug.

        It is still worth surfacing. A market-wide one-sided signal is far more often a
        model that is drifting than an edge nobody else noticed, and the reader should
        weigh it that way rather than taking twenty unders on trust.
        """
        overs, unders = self.side_skew
        total = overs + unders
        if total < 5:
            return None
        share = max(overs, unders) / total
        if share < 0.8:
            return None
        side = "Over" if overs > unders else "Under"
        return (
            f"{max(overs, unders)} of {total} picks are {side}. Receiving yards really "
            f"are right-skewed, so some lean is expected -- but a scan this one-sided is "
            f"more often a drifting model than a market-wide edge. Treat with suspicion "
            f"until the season provides a track record."
        )

    @property
    def thin_coverage(self) -> list[GradedProp]:
        """Actionable picks carrying a coverage warning."""
        return [g for g in self.actionable if g.coverage_warning is not None]

    @property
    def coverage_warning(self) -> str | None:
        """Flag a slate where most actionable picks rest on a single book.

        Worth saying once at the top rather than only per row: if every pick comes from one
        book, the scan is measuring that book rather than the market, and no amount of
        per-row labelling makes that obvious at a glance.
        """
        picks = self.actionable
        if not picks:
            return None
        thin = [g for g in picks if g.books_posting <= 1]
        if len(thin) < max(3, len(picks) * 0.6):
            return None
        books = {g.book for g in thin}
        only = f"only {next(iter(books))}" if len(books) == 1 else f"{len(books)} books"
        return (
            f"{len(thin)} of {len(picks)} actionable picks come from a single book "
            f"({only}). Player props are often posted by one book days before the rest of "
            f"the market, so this is normal early in the week -- but until other books "
            f"post, nothing is cross-checking these numbers."
        )

    @property
    def missing_games_warning(self) -> str | None:
        """Flag a slate we hold only part of.

        Without this a truncated week is invisible: the games that were never polled have
        no lines, so the scan simply shows fewer rows and looks complete. Anyone reading it
        would have no way to tell they were seeing two thirds of the slate.
        """
        if self.games_in_week <= 0 or self.games_with_lines >= self.games_in_week:
            return None
        missing = self.games_in_week - self.games_with_lines
        return (
            f"Lines for only {self.games_with_lines} of {self.games_in_week} games this "
            f"week -- {missing} missing. This scan is a partial slate, not a complete one. "
            f"Either the poll was cut short by the credit floor or no book had posted for "
            f"those games yet."
        )

    def summary(self) -> str:
        by_grade: dict[str, int] = defaultdict(int)
        for g in self.graded:
            by_grade[g.grade.value] += 1
        counts = " ".join(f"{k}:{by_grade.get(k, 0)}" for k in ("A", "B", "C", "D"))
        return (
            f"{self.lines_seen} lines, {len(self.graded)} graded ({counts}), "
            f"{self.players_without_history} players without enough history"
        )


def scan_week(
    session: Session,
    *,
    season: int | None = None,
    week: int | None = None,
    markets: tuple[Market, ...] | None = None,
) -> ScanResult:
    """Grade every posted prop line for a week.

    Defaults to every market rather than a hand-written list. The list used to be spelled
    out here, and when the count markets were added it silently kept excluding them: their
    lines were ingested and counted in `lines_seen`, then dropped before grading, so the
    scan showed fewer graded rows than lines with nothing to explain the difference.
    Deriving the default from `Market` means a new market appears in the scan the moment it
    exists.
    """
    markets = markets if markets is not None else tuple(Market)
    now = datetime.now(UTC)

    stmt = select(NflPropLine)
    if season:
        stmt = stmt.where(NflPropLine.season == season)
    if week:
        stmt = stmt.where(NflPropLine.week == week)
    lines = session.scalars(stmt).all()

    def _slate_size() -> int:
        stmt = select(NflGame)
        if season:
            stmt = stmt.where(NflGame.season == season)
        if week:
            stmt = stmt.where(NflGame.week == week)
        return len(session.scalars(stmt).all()) if (season and week) else 0

    if not lines:
        return ScanResult([], 0, 0, 0, week, season, games_in_week=_slate_size())

    # Latest observation per (game, market, player, book, side, point).
    latest: dict[tuple, NflPropLine] = {}
    for line in sorted(lines, key=lambda x: x.fetched_at, reverse=True):
        key = (
            line.game_id, line.market, line.player_name,
            line.bookmaker, line.outcome, line.point,
        )
        latest.setdefault(key, line)

    # How many books posted each prop, and where they set it. A prop is graded from one
    # book quite happily -- this exists so a line no other book agrees with can be
    # labelled rather than silently ranked alongside a well-covered one.
    books_by_prop: dict[tuple, set[str]] = defaultdict(set)
    points_by_side: dict[tuple, list[float]] = defaultdict(list)
    for line in latest.values():
        books_by_prop[(line.game_id, line.market, line.player_name)].add(line.bookmaker)
        points_by_side[
            (line.game_id, line.market, line.player_name, line.outcome)
        ].append(line.point)

    games = {
        g.game_id: g
        for g in session.scalars(
            select(NflGame).where(NflGame.game_id.in_({l.game_id for l in latest.values()}))
        ).all()
    }

    wanted = {m.value for m in markets}
    # One projection per (player, market, game) rather than per line -- a player may have
    # the same prop at several books and the projection does not change between them.
    projections: dict[tuple, object] = {}
    graded: list[GradedProp] = []
    missing: set[str] = set()

    for line in latest.values():
        if line.market not in wanted:
            continue
        game = games.get(line.game_id)
        if game is None or not line.player_id:
            if not line.player_id:
                missing.add(line.player_name)
            continue

        key = (line.player_id, line.market, line.game_id)
        if key not in projections:
            is_home = False
            opponent = game.away_team
            from app.models import NflPlayerGame

            recent = session.scalar(
                select(NflPlayerGame)
                .where(NflPlayerGame.player_id == line.player_id)
                .order_by(NflPlayerGame.season.desc(), NflPlayerGame.week.desc())
                .limit(1)
            )
            if recent is not None and recent.team == game.home_team:
                is_home, opponent = True, game.away_team
            elif recent is not None:
                is_home, opponent = False, game.home_team

            projections[key] = project_prop(
                session,
                line.player_id,
                Market(line.market),
                season=game.season,
                week=game.week,
                opponent=opponent,
                is_home=is_home,
                roof=game.roof,
                wind=game.wind,
                temp=game.temp,
            )

        projection = projections[key]
        if projection is None:
            missing.add(line.player_name)
            continue

        books = books_by_prop[(line.game_id, line.market, line.player_name)]
        points = points_by_side[
            (line.game_id, line.market, line.player_name, line.outcome)
        ]
        span = (max(points) - min(points)) if len(points) > 1 else None
        median_point = statistics.median(points) if len(points) > 1 else None

        result = grade_line(
            projection,
            side=line.outcome,
            line=line.point,
            price_american=line.price_american,
            book=line.bookmaker,
            books_posting=len(books),
            line_span=span,
            median_line=median_point,
        )
        if result is not None:
            graded.append(result)

    projected = len({k for k, v in projections.items() if v is not None})
    return ScanResult(
        graded=rank(graded),
        lines_seen=len(latest),
        players_projected=projected,
        players_without_history=len(missing),
        week=week,
        season=season,
        games_in_week=_slate_size(),
        games_with_lines=len({line.game_id for line in latest.values()}),
    )
