"""Per-event markets, priced by devig and line shopping.

Deliberately model-light. The Phase 2 backtests were unkind: the F5 model scored -1.1%
against a league-average baseline and the strikeout model +1.0%, so neither earns the
right to generate a signal. What survives is the part that never needed a model --
devigging each book's prices and shopping for the best number.

Model projections are still attached where one exists, clearly flagged as unvalidated,
because seeing them next to the market is informative even when they are not tradeable.
"""

from __future__ import annotations

import logging
from collections import defaultdict
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Game, OddsSnapshot, Sport
from app.services.devig import (
    BookPrice,
    consensus,
    expected_value,
    quarter_kelly,
)

log = logging.getLogger(__name__)

PER_EVENT_MARKETS = (
    "h2h_1st_5_innings",
    "totals_1st_5_innings",
    "pitcher_strikeouts",
    "team_totals",
)

MARKET_LABELS = {
    "h2h_1st_5_innings": "First 5 innings — moneyline",
    "totals_1st_5_innings": "First 5 innings — total",
    "pitcher_strikeouts": "Pitcher strikeouts",
    "team_totals": "Team totals",
}

# Models that failed their walk-forward gate. Their projections are displayed for
# context but must never drive a recommendation.
UNVALIDATED_MODELS = {"h2h_1st_5_innings", "totals_1st_5_innings", "pitcher_strikeouts"}

MIN_BOOKS = 4


@dataclass(frozen=True)
class MarketRow:
    game_id: int
    matchup: str
    start_time: datetime
    market: str
    market_label: str
    subject: str | None       # player or team, when the market names one
    selection: str            # Over / Under / team
    point: float | None
    best_book: str
    best_american: int
    fair_prob: float
    book_count: int
    ev: float
    kelly_quarter: float
    outliers: tuple[str, ...]
    model_value: float | None = None
    model_unvalidated: bool = False

    @property
    def break_even_prob(self) -> float:
        from app.services.devig import american_to_decimal

        return 1.0 / american_to_decimal(self.best_american)


def _model_value(session: Session, game: Game, market: str, subject: str | None) -> float | None:
    """The projection for this market, or None. Context only -- never a signal."""
    try:
        if market in {"h2h_1st_5_innings", "totals_1st_5_innings"}:
            from app.services import projections_f5

            proj = projections_f5.project(
                session, game.home_team_id, game.away_team_id, game.season,
                home_pitcher_id=game.home_probable_pitcher_id,
                away_pitcher_id=game.away_probable_pitcher_id,
            )
            return round(proj.total, 2) if proj else None

        if market == "pitcher_strikeouts" and subject:
            from app.models import Player
            from app.services.projections_props import project_strikeouts

            player = session.scalar(select(Player).where(Player.full_name == subject))
            if player is None:
                return None
            opponent = (
                game.away_team_id if player.team_id == game.home_team_id else game.home_team_id
            )
            proj = project_strikeouts(session, player.id, opponent, game.season)
            return round(proj.expected, 2) if proj else None

        if market == "team_totals":
            from app.services import projections_mlb

            proj = projections_mlb.project(
                session, game.home_team_id, game.away_team_id, game.season,
                home_pitcher_id=game.home_probable_pitcher_id,
                away_pitcher_id=game.away_probable_pitcher_id,
            )
            if proj is None or not subject:
                return None
            is_home = subject.strip().lower() == game.home_team.display_name.strip().lower()
            return round(proj.home_runs if is_home else proj.away_runs, 2)
    except Exception as exc:  # noqa: BLE001 - a missing projection must not break pricing
        log.debug("model value unavailable for %s: %s", market, exc)
    return None


def market_rows(
    session: Session, market: str, *, hours_ahead: int = 48, min_books: int = MIN_BOOKS
) -> list[MarketRow]:
    """Devig one market across every game we have prices for, ranked by EV."""
    now = datetime.now(UTC)
    games = {
        g.id: g
        for g in session.scalars(
            select(Game).where(
                Game.sport == Sport.MLB,
                Game.is_final.is_(False),
                Game.start_time >= now,
                Game.start_time <= now + timedelta(hours=hours_ahead),
            )
        ).all()
    }
    if not games:
        return []

    snapshots = session.scalars(
        select(OddsSnapshot)
        .where(OddsSnapshot.market == market, OddsSnapshot.game_id.in_(list(games)))
        .order_by(OddsSnapshot.fetched_at.desc())
    ).all()

    # Latest price per (game, subject, point, book, outcome).
    latest: dict[tuple, OddsSnapshot] = {}
    for snap in snapshots:
        key = (snap.game_id, snap.player_name, snap.point, snap.bookmaker, snap.outcome)
        latest.setdefault(key, snap)

    grouped: dict[tuple, list[OddsSnapshot]] = defaultdict(list)
    for snap in latest.values():
        grouped[(snap.game_id, snap.player_name, snap.point)].append(snap)

    rows: list[MarketRow] = []
    for (game_id, subject, point), snaps in grouped.items():
        game = games.get(game_id)
        if game is None:
            continue
        prices = [
            BookPrice(
                bookmaker=s.bookmaker, outcome=s.outcome, american=s.price_american, point=s.point
            )
            for s in snaps
        ]
        for outcome in consensus(prices):
            if outcome.book_count < min_books:
                continue
            rows.append(
                MarketRow(
                    game_id=game_id,
                    matchup=f"{game.away_team.abbrev} @ {game.home_team.abbrev}",
                    start_time=game.start_time,
                    market=market,
                    market_label=MARKET_LABELS.get(market, market),
                    subject=subject,
                    selection=outcome.outcome,
                    point=point,
                    best_book=outcome.best_book,
                    best_american=outcome.best_american,
                    fair_prob=outcome.fair_prob,
                    book_count=outcome.book_count,
                    ev=expected_value(outcome.fair_prob, outcome.best_decimal),
                    kelly_quarter=quarter_kelly(outcome.fair_prob, outcome.best_decimal),
                    outliers=outcome.outliers,
                    model_value=_model_value(session, game, market, subject),
                    model_unvalidated=market in UNVALIDATED_MODELS,
                )
            )

    rows.sort(key=lambda r: r.ev, reverse=True)
    return rows


def all_markets(session: Session, **kwargs) -> dict[str, list[MarketRow]]:
    return {m: market_rows(session, m, **kwargs) for m in PER_EVENT_MARKETS}
