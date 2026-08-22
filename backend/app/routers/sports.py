"""Slate, game detail, teams, pitchers, and edges -- everything the UI reads."""

from __future__ import annotations

from datetime import date as date_type

from fastapi import APIRouter, Depends, HTTPException, Query
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import (
    Edge,
    Game,
    OddsSnapshot,
    PitcherSeasonStats,
    Player,
    Sport,
    StatSource,
    Team,
    TeamSeasonStats,
)
from app.routers.common import (
    count_upcoming,
    edge_out,
    game_out,
    parse_sport,
    pitcher_out,
    price_out,
    projection_out,
    slate_window,
    team_out,
)
from app.schemas.api import (
    EdgeOut,
    MarketRowOut,
    GameDetailOut,
    GameOut,
    GateCheckOut,
    MismatchBandOut,
    MismatchEvidenceOut,
    MismatchOut,
    PitcherOut,
    RotationSlotOut,
    SportStatusOut,
    TeamStatsOut,
)
from app.services.ingest_odds import latest_snapshots
from app.services.season_resolver import resolve

router = APIRouter(tags=["sports"])


def _sport(sport: str) -> Sport:
    try:
        return parse_sport(sport)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


def _active_season(sport: Sport) -> int:
    info = resolve(sport)
    return info.season if info.started else info.prior_season


@router.get("/{sport}/status", response_model=SportStatusOut)
def sport_status(sport: str, session: Session = Depends(get_session)) -> SportStatusOut:
    """Season state and data coverage. Lets the UI explain an empty slate."""
    s = _sport(sport)
    info = resolve(s)
    # Before a season tips off, the prior season is the one we actually serve, and the
    # season before that becomes its prior -- otherwise both fields report the same year.
    season = info.season if info.started else info.prior_season
    prior_season = info.prior_season if info.started else info.prior_season - 1

    # Distinct teams, not rows: a team can hold both an ESPN and an nba_stats row.
    teams_with_stats = len(
        set(
            session.scalars(
                select(TeamSeasonStats.team_id).where(
                    TeamSeasonStats.sport == s, TeamSeasonStats.season == season
                )
            ).all()
        )
    )
    return SportStatusOut(
        sport=s.value,
        season=season,
        season_display=info.display,
        season_started=info.started,
        prior_season=prior_season,
        upcoming_games=count_upcoming(session, s),
        teams_with_stats=teams_with_stats,
    )


@router.get("/{sport}/slate", response_model=list[GameOut])
def slate(
    sport: str,
    hours_ahead: int = Query(36, ge=1, le=240),
    session: Session = Depends(get_session),
) -> list[GameOut]:
    """Games in the near window, with projections and any priced edges."""
    s = _sport(sport)
    return [game_out(session, g) for g in slate_window(session, s, hours_ahead=hours_ahead)]


@router.get("/{sport}/games", response_model=list[GameOut])
def games(
    sport: str,
    day: date_type | None = Query(None, alias="date"),
    limit: int = Query(50, ge=1, le=500),
    session: Session = Depends(get_session),
) -> list[GameOut]:
    s = _sport(sport)
    stmt = select(Game).where(Game.sport == s)
    if day:
        stmt = stmt.where(Game.game_date == day)
    rows = session.scalars(stmt.order_by(Game.start_time.desc()).limit(limit)).all()
    return [game_out(session, g, with_projection=False) for g in rows]


@router.get("/games/{game_id}", response_model=GameDetailOut)
def game_detail(game_id: int, session: Session = Depends(get_session)) -> GameDetailOut:
    game = session.get(Game, game_id)
    if game is None:
        raise HTTPException(status_code=404, detail="game not found")

    base = game_out(session, game)
    edges = session.scalars(
        select(Edge).where(Edge.game_id == game.id).order_by(Edge.ev.desc())
    ).all()

    # Full snapshot history powers the line-movement chart; the latest per book is
    # what the odds table shows.
    history = session.scalars(
        select(OddsSnapshot)
        .where(OddsSnapshot.game_id == game.id)
        .order_by(OddsSnapshot.fetched_at)
    ).all()

    return GameDetailOut(
        **base.model_dump(),
        prices=[price_out(s) for s in latest_snapshots(session, game.id)],
        edges=[edge_out(e, game) for e in edges],
        line_history=[price_out(s) for s in history],
    )


@router.get("/{sport}/edges", response_model=list[EdgeOut])
def edges(
    sport: str,
    min_ev: float = Query(0.0),
    agree_only: bool = Query(False, description="only where model and market agree"),
    limit: int = Query(100, ge=1, le=500),
    session: Session = Depends(get_session),
) -> list[EdgeOut]:
    """Positive-EV plays, ranked. EV comes from the devigged market consensus."""
    s = _sport(sport)
    stmt = select(Edge).where(Edge.sport == s, Edge.ev >= min_ev)
    if agree_only:
        stmt = stmt.where(Edge.signals_agree.is_(True))
    rows = session.scalars(stmt.order_by(Edge.ev.desc()).limit(limit)).all()
    return [edge_out(e, session.get(Game, e.game_id)) for e in rows]


@router.get("/{sport}/teams", response_model=list[TeamStatsOut])
def teams(
    sport: str,
    season: int | None = None,
    session: Session = Depends(get_session),
) -> list[TeamStatsOut]:
    s = _sport(sport)
    season = season or _active_season(s)
    source = StatSource.ESPN if s is Sport.NBA else StatSource.MLB_STATSAPI

    rows = session.execute(
        select(Team, TeamSeasonStats)
        .join(TeamSeasonStats, TeamSeasonStats.team_id == Team.id)
        .where(
            Team.sport == s,
            TeamSeasonStats.season == season,
            TeamSeasonStats.source == source,
        )
    ).all()

    out = []
    for team, stats in rows:
        net = (
            stats.off_rating - stats.def_rating
            if stats.off_rating and stats.def_rating
            else None
        )
        out.append(
            TeamStatsOut(
                team=team_out(team),
                season=season,
                games_played=stats.games_played,
                points_for=round(stats.points_for, 1) if stats.points_for else None,
                points_against=round(stats.points_against, 1) if stats.points_against else None,
                off_rating=round(stats.off_rating, 1) if stats.off_rating else None,
                def_rating=round(stats.def_rating, 1) if stats.def_rating else None,
                net_rating=round(net, 1) if net is not None else None,
                pace=round(stats.pace, 1) if stats.pace else None,
                runs_for=stats.runs_for,
                runs_against=stats.runs_against,
                team_era=stats.team_era,
                team_whip=stats.team_whip,
                team_ops=stats.team_ops,
                source=stats.source.value,
            )
        )
    key = (lambda r: r.net_rating or -99) if s is Sport.NBA else (lambda r: -(r.team_era or 99))
    out.sort(key=key, reverse=True)
    return out


def _slot_out(slot) -> RotationSlotOut | None:
    if slot is None:
        return None
    return RotationSlotOut(
        player_id=slot.player_id,
        name=slot.name,
        rank=slot.rank,
        rotation_size=slot.rotation_size,
        era=slot.era,
        regressed_era=round(slot.regressed_era, 2),
        whip=slot.whip,
        k_per_9=slot.k_per_9,
        games_started=slot.games_started,
        is_top_two=slot.is_top_two,
        is_bottom_two=slot.is_bottom_two,
    )


@router.get("/mlb/mismatches", response_model=list[MismatchOut])
def mismatches(
    season: int | None = None,
    hours_ahead: int = Query(48, ge=1, le=240),
    min_score: float = Query(0.0, ge=0, le=100),
    strict_only: bool = Query(False),
    session: Session = Depends(get_session),
) -> list[MismatchOut]:
    """Upcoming games ranked by how lopsided the pitching matchup is."""
    from app.services.mismatches import find_mismatches

    season = season or _active_season(Sport.MLB)
    rows = find_mismatches(session, season, hours_ahead=hours_ahead, min_score=min_score)
    if strict_only:
        rows = [m for m in rows if m.strict]

    return [
        MismatchOut(
            game_id=m.game_id,
            start_time=m.start_time,
            matchup=f"{m.underdog_abbrev} @ {m.favourite_abbrev}"
            if m.favourite_is_home
            else f"{m.favourite_abbrev} @ {m.underdog_abbrev}",
            favourite=m.favourite_abbrev,
            underdog=m.underdog_abbrev,
            favourite_is_home=m.favourite_is_home,
            score=round(m.score, 1),
            strict=m.strict,
            team_gap=round(m.team_gap, 3),
            era_gap=round(m.era_gap, 2),
            favourite_team_rank=m.favourite_team.rank,
            underdog_team_rank=m.underdog_team.rank,
            favourite_team_tier=m.favourite_team.tier.value,
            underdog_team_tier=m.underdog_team.tier.value,
            favourite_pythagorean=round(m.favourite_team.pythagorean, 3),
            underdog_pythagorean=round(m.underdog_team.pythagorean, 3),
            favourite_pitcher=_slot_out(m.favourite_pitcher),
            underdog_pitcher=_slot_out(m.underdog_pitcher),
            model_win_prob=round(m.model_win_prob, 4) if m.model_win_prob is not None else None,
            market_fair_prob=round(m.market_fair_prob, 4)
            if m.market_fair_prob is not None
            else None,
            best_american=m.best_american,
            best_book=m.best_book,
            risk_to_win_one=round(m.risk_to_win_one, 2)
            if m.risk_to_win_one is not None
            else None,
            ev=round(m.ev, 4) if m.ev is not None else None,
            model_ev=round(m.model_ev, 4) if m.model_ev is not None else None,
            kelly_quarter=round(m.kelly_quarter, 4) if m.kelly_quarter is not None else None,
            book_count=m.book_count,
            verdict=m.verdict,
            model_disagrees=m.model_disagrees,
            grade=m.grade,
            checks=[
                GateCheckOut(key=c.key, label=c.label, passed=c.passed, detail=c.detail)
                for c in m.checks
            ],
            passed_checks=m.passed_checks,
            blocking_reason=m.blocking_reason,
            break_even_prob=round(m.break_even_prob, 4)
            if m.break_even_prob is not None
            else None,
            band_label=m.band_label,
            band_win_rate=round(m.band_win_rate, 4) if m.band_win_rate is not None else None,
            band_break_even=m.band_break_even,
            band_sample=m.band_sample,
        )
        for m in rows
    ]


@router.get("/mlb/mismatches/evidence", response_model=MismatchEvidenceOut)
def mismatch_evidence(
    season: int | None = None, session: Session = Depends(get_session)
) -> MismatchEvidenceOut:
    """How the mismatch score has actually performed on completed games this season."""
    from app.services.mismatches import cached_walk_forward

    season = season or _active_season(Sport.MLB)
    result = cached_walk_forward(session, season)

    def break_even(rate: float) -> int | None:
        """American price at which this win rate exactly breaks even."""
        if not 0 < rate < 1:
            return None
        return round(-100 * rate / (1 - rate))

    bands = []
    for label, wins, games in result.bands:
        rate = wins / games if games else 0.0
        bands.append(
            MismatchBandOut(
                label=label,
                wins=wins,
                games=games,
                win_rate=round(rate, 4),
                break_even_american=break_even(rate),
            )
        )

    strict_rate = result.strict_wins / result.strict_games if result.strict_games else None
    return MismatchEvidenceOut(
        bands=bands,
        strict_wins=result.strict_wins,
        strict_games=result.strict_games,
        strict_win_rate=round(strict_rate, 4) if strict_rate is not None else None,
        strict_break_even_american=break_even(strict_rate) if strict_rate else None,
        baseline_home_win_rate=round(result.baseline_home_win_rate, 4),
        caveat=(
            "Walk-forward: each game graded only on what was known beforehand. Grading "
            "with full-season stats instead would inflate these numbers badly - the "
            "strict rate reads 89.5% that way versus "
            f"{strict_rate:.1%} here." if strict_rate else "Walk-forward measurement."
        ),
    )


@router.get("/mlb/markets", response_model=dict[str, list[MarketRowOut]])
def markets(
    hours_ahead: int = Query(48, ge=1, le=240),
    min_books: int = Query(4, ge=1, le=20),
    session: Session = Depends(get_session),
) -> dict[str, list[MarketRowOut]]:
    """Per-event markets priced by devig and line shopping."""
    from app.services.markets import all_markets

    out: dict[str, list[MarketRowOut]] = {}
    for market, rows in all_markets(session, hours_ahead=hours_ahead, min_books=min_books).items():
        out[market] = [
            MarketRowOut(
                game_id=r.game_id,
                matchup=r.matchup,
                start_time=r.start_time,
                market=r.market,
                market_label=r.market_label,
                subject=r.subject,
                selection=r.selection,
                point=r.point,
                best_book=r.best_book,
                best_american=r.best_american,
                fair_prob=round(r.fair_prob, 4),
                break_even_prob=round(r.break_even_prob, 4),
                book_count=r.book_count,
                ev=round(r.ev, 4),
                kelly_quarter=round(r.kelly_quarter, 4),
                outliers=list(r.outliers),
                model_value=r.model_value,
                model_unvalidated=r.model_unvalidated,
            )
            for r in rows
        ]
    return out


@router.get("/mlb/pitchers", response_model=list[PitcherOut])
def pitchers(
    season: int | None = None,
    limit: int = Query(80, ge=1, le=300),
    session: Session = Depends(get_session),
) -> list[PitcherOut]:
    """Pitchers with a season line, best ERA first."""
    season = season or _active_season(Sport.MLB)
    rows = session.execute(
        select(Player, PitcherSeasonStats)
        .join(PitcherSeasonStats, PitcherSeasonStats.player_id == Player.id)
        .where(PitcherSeasonStats.season == season, PitcherSeasonStats.era.is_not(None))
        .order_by(PitcherSeasonStats.era)
        .limit(limit)
    ).all()
    results = [pitcher_out(session, player, season) for player, _ in rows]
    return [p for p in results if p is not None]
