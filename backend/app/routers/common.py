"""Shared helpers for building API responses."""

from __future__ import annotations

from datetime import UTC, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import (
    Edge,
    Game,
    PitcherGameLog,
    PitcherSeasonStats,
    Player,
    Sport,
    Team,
)
from app.schemas.api import (
    EdgeOut,
    GameOut,
    PitcherOut,
    PriceOut,
    ProjectionOut,
    TeamOut,
)
from app.services import projections_mlb, projections_nba
from app.services.devig import american_to_decimal


def parse_sport(value: str) -> Sport:
    try:
        return Sport(value.lower())
    except ValueError as exc:
        raise ValueError(f"unknown sport {value!r}; expected mlb or nba") from exc


def team_out(team: Team) -> TeamOut:
    return TeamOut(
        id=team.id,
        abbrev=team.abbrev,
        name=team.display_name,
        location=team.location,
        conference=team.conference,
        division=team.division,
    )


def pitcher_out(session: Session, player: Player | None, season: int) -> PitcherOut | None:
    if player is None:
        return None
    stats = session.scalar(
        select(PitcherSeasonStats).where(
            PitcherSeasonStats.player_id == player.id, PitcherSeasonStats.season == season
        )
    )
    logs = session.scalars(
        select(PitcherGameLog)
        .where(PitcherGameLog.player_id == player.id, PitcherGameLog.season == season)
        .order_by(PitcherGameLog.game_date.desc())
        .limit(8)
    ).all()

    # Recent form as earned runs per 9 in each of the last starts, newest last.
    form: list[float] = []
    for log in reversed(logs):
        if log.innings_pitched and log.innings_pitched > 0 and log.earned_runs is not None:
            form.append(round(log.earned_runs * 9.0 / log.innings_pitched, 2))

    from app.services.projections_mlb import innings_per_start

    ip_per_start = None
    if stats:
        raw = stats.raw or {}
        try:
            games_pitched = int(raw.get("gamesPitched"))
        except (TypeError, ValueError):
            games_pitched = None
        value = innings_per_start(stats.innings_pitched, stats.games_started, games_pitched)
        ip_per_start = round(value, 2) if value is not None else None

    return PitcherOut(
        id=player.id,
        name=player.full_name,
        era=stats.era if stats else None,
        whip=stats.whip if stats else None,
        k_per_9=stats.k_per_9 if stats else None,
        bb_per_9=stats.bb_per_9 if stats else None,
        wins=stats.wins if stats else None,
        losses=stats.losses if stats else None,
        innings_pitched=round(stats.innings_pitched, 1) if stats and stats.innings_pitched else None,
        games_started=stats.games_started if stats else None,
        innings_per_start=ip_per_start,
        recent_form=form,
    )


def projection_out(session: Session, game: Game) -> ProjectionOut | None:
    if game.sport is Sport.NBA:
        proj = projections_nba.project(session, game.home_team_id, game.away_team_id, game.season)
        if proj is None:
            return None
        return ProjectionOut(
            home_score=round(proj.home_points, 1),
            away_score=round(proj.away_points, 1),
            total=round(proj.total, 1),
            margin=round(proj.margin, 1),
            prob_home_win=round(proj.prob_home_win(), 4),
            blended=proj.blended,
            possessions=round(proj.possessions, 1),
        )

    proj = projections_mlb.project(
        session,
        game.home_team_id,
        game.away_team_id,
        game.season,
        home_pitcher_id=game.home_probable_pitcher_id,
        away_pitcher_id=game.away_probable_pitcher_id,
    )
    if proj is None:
        return None
    return ProjectionOut(
        home_score=round(proj.home_runs, 2),
        away_score=round(proj.away_runs, 2),
        total=round(proj.total, 2),
        margin=round(proj.margin, 2),
        prob_home_win=round(proj.prob_home_win(), 4),
    )


def edge_out(edge: Edge, game: Game | None = None) -> EdgeOut:
    return EdgeOut(
        id=edge.id,
        game_id=edge.game_id,
        sport=edge.sport.value,
        market=edge.market,
        selection=edge.selection,
        point=edge.point,
        best_book=edge.best_book,
        best_price_american=edge.best_price_american,
        best_price_decimal=round(edge.best_price_decimal, 4),
        fair_prob=round(edge.fair_prob, 4),
        book_count=edge.book_count,
        ev=round(edge.ev, 4),
        kelly_quarter=round(edge.kelly_quarter, 4),
        model_prob=round(edge.model_prob, 4) if edge.model_prob is not None else None,
        model_ev=round(edge.model_ev, 4) if edge.model_ev is not None else None,
        model_line=round(edge.model_line, 2) if edge.model_line is not None else None,
        signals_agree=edge.signals_agree,
        matchup=f"{game.away_team.abbrev} @ {game.home_team.abbrev}" if game else None,
        start_time=game.start_time if game else None,
    )


def price_out(snapshot) -> PriceOut:
    return PriceOut(
        bookmaker=snapshot.bookmaker,
        market=snapshot.market,
        outcome=snapshot.outcome,
        american=snapshot.price_american,
        decimal=round(american_to_decimal(snapshot.price_american), 4),
        point=snapshot.point,
        fetched_at=snapshot.fetched_at,
    )


def game_out(session: Session, game: Game, *, with_projection: bool = True) -> GameOut:
    from app.services.ingest_odds import latest_snapshots

    edges = session.scalars(select(Edge).where(Edge.game_id == game.id)).all()
    best_total = best_total_book = None
    for snap in latest_snapshots(session, game.id):
        if snap.market == "totals" and snap.point is not None:
            best_total, best_total_book = snap.point, snap.bookmaker
            break

    return GameOut(
        id=game.id,
        sport=game.sport.value,
        external_id=game.external_id,
        start_time=game.start_time,
        status=game.status,
        is_final=game.is_final,
        home=team_out(game.home_team),
        away=team_out(game.away_team),
        home_score=game.home_score,
        away_score=game.away_score,
        home_pitcher=pitcher_out(session, game.home_probable_pitcher, game.season)
        if game.sport is Sport.MLB
        else None,
        away_pitcher=pitcher_out(session, game.away_probable_pitcher, game.season)
        if game.sport is Sport.MLB
        else None,
        projection=projection_out(session, game) if with_projection else None,
        best_total=best_total,
        best_total_book=best_total_book,
        top_edge_ev=max((e.ev for e in edges), default=None),
        edge_count=len(edges),
    )


def slate_window(session: Session, sport: Sport, *, hours_back: int = 6, hours_ahead: int = 36):
    now = datetime.now(UTC)
    return session.scalars(
        select(Game)
        .where(
            Game.sport == sport,
            Game.start_time >= now - timedelta(hours=hours_back),
            Game.start_time <= now + timedelta(hours=hours_ahead),
        )
        .order_by(Game.start_time)
    ).all()


def count_upcoming(session: Session, sport: Sport) -> int:
    now = datetime.now(UTC)
    return (
        session.scalar(
            select(func.count())
            .select_from(Game)
            .where(Game.sport == sport, Game.start_time >= now, Game.is_final.is_(False))
        )
        or 0
    )
