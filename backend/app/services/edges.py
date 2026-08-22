"""Combine the two signals into ranked, stakeable edges.

Layer 1 (devigged market consensus) needs no model to be correct and is what the EV
and stake are computed from. Layer 2 (the projection) is recorded alongside as an
independent opinion, and `signals_agree` marks where the two point the same way --
a much stronger tell than either on its own.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import delete, select
from sqlalchemy.orm import Session

from app.models import Edge, Game, Sport
from app.services import projections_mlb, projections_nba
from app.services.devig import (
    BookPrice,
    ConsensusOutcome,
    consensus,
    expected_value,
    kelly_fraction,
    quarter_kelly,
)
from app.services.ingest_odds import latest_snapshots

log = logging.getLogger(__name__)

# Below this the "edge" is inside the noise of our own consensus estimate.
MIN_EV = 0.005


def compute_edges_for_game(session: Session, game: Game) -> list[Edge]:
    """Devig every market for one game and attach model probabilities where available."""
    snapshots = latest_snapshots(session, game.id)
    if not snapshots:
        return []

    projection = _project(session, game)

    by_market: dict[tuple[str, float | None], list[BookPrice]] = {}
    for snap in snapshots:
        # Totals and spreads are only comparable at the same number, so the line is
        # part of the key -- devigging across different lines would be meaningless.
        key = (snap.market, snap.point if snap.market != "h2h" else None)
        by_market.setdefault(key, []).append(
            BookPrice(
                bookmaker=snap.bookmaker,
                outcome=snap.outcome,
                american=snap.price_american,
                point=snap.point,
            )
        )

    edges: list[Edge] = []
    now = datetime.now(UTC)

    for (market, point), prices in by_market.items():
        for outcome in consensus(prices):
            ev = expected_value(outcome.fair_prob, outcome.best_decimal)
            if ev < MIN_EV:
                continue

            model_prob = _model_prob(game, projection, market, outcome, point)
            model_ev = (
                expected_value(model_prob, outcome.best_decimal) if model_prob is not None else None
            )

            edges.append(
                Edge(
                    game_id=game.id,
                    sport=game.sport,
                    market=market,
                    selection=outcome.outcome,
                    point=point,
                    best_book=outcome.best_book,
                    best_price_american=outcome.best_american,
                    best_price_decimal=outcome.best_decimal,
                    fair_prob=outcome.fair_prob,
                    book_count=outcome.book_count,
                    ev=ev,
                    kelly_full=kelly_fraction(outcome.fair_prob, outcome.best_decimal),
                    kelly_quarter=quarter_kelly(outcome.fair_prob, outcome.best_decimal),
                    model_prob=model_prob,
                    model_ev=model_ev,
                    model_line=_model_line(projection, market),
                    signals_agree=(model_ev is not None and model_ev > 0),
                    computed_at=now,
                )
            )
    return edges


def _project(session: Session, game: Game):
    if game.sport is Sport.NBA:
        return projections_nba.project(session, game.home_team_id, game.away_team_id, game.season)
    return projections_mlb.project(
        session,
        game.home_team_id,
        game.away_team_id,
        game.season,
        home_pitcher_id=game.home_probable_pitcher_id,
        away_pitcher_id=game.away_probable_pitcher_id,
    )


def _model_line(projection, market: str) -> float | None:
    if projection is None:
        return None
    if market == "totals":
        return projection.total
    if market == "spreads":
        return -projection.margin  # expressed as the home handicap
    return None


def _model_prob(
    game: Game, projection, market: str, outcome: ConsensusOutcome, point: float | None
) -> float | None:
    """Model probability for this specific selection, or None when unavailable."""
    if projection is None:
        return None

    name = outcome.outcome.strip().lower()

    if market == "totals" and point is not None:
        if name == "over":
            return projection.prob_over(point)
        if name == "under":
            return 1.0 - projection.prob_over(point)
        return None

    if market == "h2h":
        home_prob = projection.prob_home_win()
        if _is_home(game, outcome.outcome):
            return home_prob
        if _is_away(game, outcome.outcome):
            return 1.0 - home_prob
        return None

    if market == "spreads" and point is not None:
        # Only the NBA projection exposes a cover probability; MLB run lines are not
        # well modelled by the Poisson margin, so they are left to the market.
        cover = getattr(projection, "prob_home_cover", None)
        if cover is None:
            return None
        if _is_home(game, outcome.outcome):
            return cover(point)
        if _is_away(game, outcome.outcome):
            return 1.0 - cover(-point)
    return None


def _is_home(game: Game, name: str) -> bool:
    return name.strip().lower() in {
        game.home_team.display_name.lower(),
        game.home_team.odds_api_name.lower(),
    }


def _is_away(game: Game, name: str) -> bool:
    return name.strip().lower() in {
        game.away_team.display_name.lower(),
        game.away_team.odds_api_name.lower(),
    }


def recompute_edges(session: Session, sport: Sport) -> int:
    """Rebuild edges for every upcoming game in a sport."""
    now = datetime.now(UTC)
    games = session.scalars(
        select(Game).where(
            Game.sport == sport, Game.is_final.is_(False), Game.start_time >= now
        )
    ).all()

    session.execute(
        delete(Edge).where(
            Edge.sport == sport, Edge.game_id.in_([g.id for g in games] or [-1])
        )
    )

    written = 0
    for game in games:
        for edge in compute_edges_for_game(session, game):
            session.add(edge)
            written += 1

    session.commit()
    log.info("edges %s: %d across %d games", sport.value, written, len(games))
    return written
