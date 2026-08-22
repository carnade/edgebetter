"""NFL endpoints: conditional splits, schedule, and team ratings.

The splits endpoint is the centrepiece and always returns the interval, sample band, and
holdout verdict alongside every rate. Returning a bare percentage would let the UI
present a twelve-game coincidence as a finding, which is the exact failure this whole
subsystem is built to prevent.
"""

from __future__ import annotations

from datetime import date

from fastapi import APIRouter, Depends, HTTPException, Query
from pydantic import BaseModel
from sqlalchemy import select
from sqlalchemy.orm import Session

from app.db import get_session
from app.models import NflGame, NflTeamGame
from app.services.nfl_splits import HOLDOUT_SEASON, Filters, analyse
from app.services.stats import BREAK_EVEN_110

router = APIRouter(tags=["nfl"], prefix="/nfl")


# --------------------------------------------------------------------- schemas
class RateOut(BaseModel):
    hits: int
    n: int
    rate: float
    lower: float
    upper: float
    band: str
    verdict: str
    beats_break_even: bool


class HoldoutOut(BaseModel):
    season: int
    rate: RateOut
    status: str
    direction_held: bool
    survives: bool
    gap: float


class MarketSplitOut(BaseModel):
    market: str
    label: str
    result: RateOut
    holdout: HoldoutOut | None = None
    mean_value: float | None = None
    mean_low: float | None = None
    mean_high: float | None = None


class SplitReportOut(BaseModel):
    description: str
    n_team_games: int
    break_even: float
    holdout_season: int
    markets: list[MarketSplitOut]
    baseline: list[MarketSplitOut] = []


class NflGameOut(BaseModel):
    game_id: str
    season: int
    week: int
    gameday: date
    home_team: str
    away_team: str
    home_score: int | None = None
    away_score: int | None = None
    total_line: float | None = None
    spread_line: float | None = None
    roof: str | None = None
    surface: str | None = None
    temp: float | None = None
    wind: float | None = None
    div_game: bool
    home_qb_name: str | None = None
    away_qb_name: str | None = None
    home_rest: int | None = None
    away_rest: int | None = None
    # Model projection, shown only where the model has enough history.
    projected_total: float | None = None
    projected_margin: float | None = None
    projection_thin: bool = False


class TeamRatingOut(BaseModel):
    team: str
    games: int
    points_for: float | None = None
    points_against: float | None = None
    off_epa_per_play: float | None = None
    def_epa_per_play: float | None = None
    net_epa: float | None = None
    plays_per_game: float | None = None


class NflStatusOut(BaseModel):
    seasons: list[int]
    completed_games: int
    scheduled_games: int
    upcoming_season: int | None = None
    holdout_season: int
    model_beats_market: bool = False
    model_note: str


# ----------------------------------------------------------------- conversion
def _rate_out(r) -> RateOut:
    return RateOut(
        hits=r.hits,
        n=r.n,
        rate=round(r.rate, 4),
        lower=round(r.lower, 4),
        upper=round(r.upper, 4),
        band=r.band.value,
        verdict=r.verdict,
        beats_break_even=r.beats_break_even,
    )


def _split_out(split) -> MarketSplitOut:
    return MarketSplitOut(
        market=split.market,
        label=split.label,
        result=_rate_out(split.result),
        holdout=(
            HoldoutOut(
                season=HOLDOUT_SEASON,
                rate=_rate_out(split.holdout.holdout),
                status=split.holdout.status,
                direction_held=split.holdout.direction_held,
                survives=split.holdout.survives,
                gap=round(split.holdout.gap, 4),
            )
            if split.holdout
            else None
        ),
        mean_value=round(split.mean_value, 2) if split.mean_value is not None else None,
        mean_low=round(split.mean_low, 2) if split.mean_low is not None else None,
        mean_high=round(split.mean_high, 2) if split.mean_high is not None else None,
    )


# ---------------------------------------------------------------------- routes
@router.get("/splits", response_model=SplitReportOut)
def splits(
    wind_min: float | None = None,
    wind_max: float | None = None,
    temp_min: float | None = None,
    temp_max: float | None = None,
    roof: str | None = None,
    surface: str | None = None,
    outdoor: bool = False,
    div_game: bool | None = None,
    is_home: bool | None = None,
    is_favourite: bool | None = None,
    rest_advantage_min: int | None = None,
    team: str | None = None,
    opponent: str | None = None,
    team_total_line: float = Query(23.5, ge=0, le=60),
    include_baseline: bool = True,
    session: Session = Depends(get_session),
) -> SplitReportOut:
    """Historical base rates under a set of conditions, with uncertainty attached."""
    filters = Filters(
        wind_min=wind_min,
        wind_max=wind_max,
        temp_min=temp_min,
        temp_max=temp_max,
        roof=roof,
        surface=surface,
        outdoor_only=outdoor,
        div_game=div_game,
        is_home=is_home,
        is_favourite=is_favourite,
        rest_advantage_min=rest_advantage_min,
        team=team,
        opponent=opponent,
        team_total_line=team_total_line,
    )
    report = analyse(session, filters)

    baseline_markets: list[MarketSplitOut] = []
    if include_baseline:
        # A rate means little without something to compare it against; the UI shows the
        # unconditioned rate beside it so a "51% over" reads as normal rather than notable.
        base = analyse(
            session, Filters(outdoor_only=outdoor, team_total_line=team_total_line)
        )
        baseline_markets = [_split_out(m) for m in base.markets]

    return SplitReportOut(
        description=report.description,
        n_team_games=report.n_team_games,
        break_even=round(BREAK_EVEN_110, 4),
        holdout_season=HOLDOUT_SEASON,
        markets=[_split_out(m) for m in report.markets],
        baseline=baseline_markets,
    )


@router.get("/schedule", response_model=list[NflGameOut])
def schedule(
    season: int | None = None,
    week: int | None = None,
    limit: int = Query(64, ge=1, le=400),
    project: bool = False,
    session: Session = Depends(get_session),
) -> list[NflGameOut]:
    """Games for a season and week, optionally with model projections."""
    stmt = select(NflGame).where(NflGame.game_type == "REG")
    if season:
        stmt = stmt.where(NflGame.season == season)
    if week:
        stmt = stmt.where(NflGame.week == week)
    games = session.scalars(
        stmt.order_by(NflGame.season.desc(), NflGame.week, NflGame.gameday).limit(limit)
    ).all()

    out: list[NflGameOut] = []
    for g in games:
        row = NflGameOut(
            game_id=g.game_id,
            season=g.season,
            week=g.week,
            gameday=g.gameday,
            home_team=g.home_team,
            away_team=g.away_team,
            home_score=g.home_score,
            away_score=g.away_score,
            total_line=g.total_line,
            spread_line=g.spread_line,
            roof=g.roof,
            surface=g.surface,
            temp=g.temp,
            wind=g.wind,
            div_game=g.div_game,
            home_qb_name=g.home_qb_name,
            away_qb_name=g.away_qb_name,
            home_rest=g.home_rest,
            away_rest=g.away_rest,
        )
        if project:
            from app.services.nfl_projections import project_game

            projection = project_game(session, g)
            if projection is not None:
                row.projected_total = round(projection.total, 1)
                row.projected_margin = round(projection.margin, 1)
                row.projection_thin = projection.thin_sample
        out.append(row)
    return out


@router.get("/ratings", response_model=list[TeamRatingOut])
def ratings(
    season: int | None = None,
    through_week: int | None = None,
    session: Session = Depends(get_session),
) -> list[TeamRatingOut]:
    """Team ratings as of a point in the season, built from prior games only."""
    from app.services.nfl_projections import build_ratings_through

    if season is None:
        season = session.scalar(select(NflGame.season).order_by(NflGame.season.desc()).limit(1))
    if season is None:
        raise HTTPException(status_code=404, detail="no NFL data ingested")

    week = through_week or 99
    built = build_ratings_through(session, season, week)

    out = [
        TeamRatingOut(
            team=r.team,
            games=r.games,
            points_for=round(r.ppg, 2) if r.ppg is not None else None,
            points_against=round(r.papg, 2) if r.papg is not None else None,
            off_epa_per_play=round(r.off_epa_rate, 4),
            def_epa_per_play=round(r.def_epa_rate, 4),
            net_epa=round(r.off_epa_rate - r.def_epa_rate, 4),
            plays_per_game=round(r.plays_per_game, 1) if r.plays_per_game else None,
        )
        for r in built.values()
    ]
    out.sort(key=lambda t: t.net_epa or -99, reverse=True)
    return out


class QbImpactOut(BaseModel):
    backup_games: int
    starter_games: int
    backup_points: float
    starter_points: float
    points_swing: float
    backup_spread: float
    starter_spread: float
    line_swing: float
    backup_ats: RateOut
    starter_ats: RateOut
    fade_rate: float
    verdict: str


class PartialsOut(BaseModel):
    games: int
    first_quarter_mean: float
    first_half_mean: float
    second_half_mean: float
    full_mean: float
    first_half_share: float
    first_half_cv: float
    second_half_cv: float
    first_half_more_stable: bool
    scoreless_first_quarter: RateOut
    verdict: str


@router.get("/qb-impact", response_model=QbImpactOut)
def qb_impact(session: Session = Depends(get_session)) -> QbImpactOut:
    """What a backup quarterback start is actually worth, after the line moves."""
    from app.services.nfl_qb import analyse_qb_impact

    q = analyse_qb_impact(session)
    fade = 1.0 - q.backup_ats.rate
    return QbImpactOut(
        backup_games=q.backup_games,
        starter_games=q.starter_games,
        backup_points=round(q.backup_points, 2),
        starter_points=round(q.starter_points, 2),
        points_swing=round(q.points_swing, 2),
        backup_spread=round(q.backup_spread, 2),
        starter_spread=round(q.starter_spread, 2),
        line_swing=round(q.line_swing, 2),
        backup_ats=_rate_out(q.backup_ats),
        starter_ats=_rate_out(q.starter_ats),
        fade_rate=round(fade, 4),
        verdict=(
            f"Backups score {abs(q.points_swing):.1f} fewer points and the market moves "
            f"{abs(q.line_swing):.1f}. Fading them went {fade:.1%} against a "
            f"{BREAK_EVEN_110:.1%} break-even -- a lean, not an edge."
        ),
    )


@router.get("/partials", response_model=PartialsOut)
def partials(session: Session = Depends(get_session)) -> PartialsOut:
    """How scoring distributes across a game, and whether the first half is steadier."""
    from app.services.nfl_partials import profile

    p = profile(session)
    return PartialsOut(
        games=p.games,
        first_quarter_mean=round(p.first_quarter_mean, 2),
        first_half_mean=round(p.first_half_mean, 2),
        second_half_mean=round(p.second_half_mean, 2),
        full_mean=round(p.full_mean, 2),
        first_half_share=round(p.first_half_share, 4),
        first_half_cv=round(p.first_half_cv, 4),
        second_half_cv=round(p.second_half_cv, 4),
        first_half_more_stable=p.first_half_more_stable,
        scoreless_first_quarter=_rate_out(p.scoreless_first_quarter),
        verdict=(
            f"The first half carries {p.first_half_share:.1%} of scoring and is "
            f"{'steadier' if p.first_half_more_stable else 'no steadier'} than the second "
            f"({p.first_half_cv:.3f} vs {p.second_half_cv:.3f} variability)."
        ),
    )


class MovementOut(BaseModel):
    game_id: str
    matchup: str
    week: int
    observations: int
    open_total: float | None = None
    latest_total: float | None = None
    total_drift: float | None = None
    open_spread: float | None = None
    latest_spread: float | None = None
    spread_drift: float | None = None
    model_total_at_open: float | None = None
    model_disagreement: float | None = None
    moved_our_way: bool | None = None


class ClvOut(BaseModel):
    tracked_games: int
    games_with_movement: int
    resolved: int
    ready: bool
    clv_rate: RateOut | None = None
    mean_drift: float
    mean_abs_drift: float
    verdict: str
    movements: list[MovementOut] = []


@router.get("/movement", response_model=ClvOut)
def movement(
    season: int | None = None,
    session: Session = Depends(get_session),
) -> ClvOut:
    """Line movement since we started watching, and whether it favours our side.

    This is the one measurement that can show edge without beating the closing line:
    if the market drifts toward the number we would have taken at the open, being early
    is worth something even though our projection loses to the final number.
    """
    from app.services.nfl_clv import clv_report, game_movements

    report = clv_report(session, season=season)
    moves = [m for m in game_movements(session, season=season) if m.observations > 1]
    moves.sort(key=lambda m: abs(m.total_drift or 0), reverse=True)

    verdict = report.summary().split("READ")[-1].strip().replace("\n  ", " ")

    return ClvOut(
        tracked_games=report.tracked_games,
        games_with_movement=report.games_with_movement,
        resolved=report.resolved,
        ready=report.ready,
        clv_rate=_rate_out(report.clv_rate) if report.clv_rate else None,
        mean_drift=round(report.mean_drift, 3),
        mean_abs_drift=round(report.mean_abs_drift, 3),
        verdict=" ".join(verdict.split()),
        movements=[
            MovementOut(
                game_id=m.game_id,
                matchup=m.matchup,
                week=m.week,
                observations=m.observations,
                open_total=m.open_total,
                latest_total=m.latest_total,
                total_drift=round(m.total_drift, 2) if m.total_drift is not None else None,
                open_spread=m.open_spread,
                latest_spread=m.latest_spread,
                spread_drift=round(m.spread_drift, 2) if m.spread_drift is not None else None,
                model_total_at_open=m.model_total_at_open,
                model_disagreement=round(m.model_disagreement, 2)
                if m.model_disagreement is not None
                else None,
                moved_our_way=m.moved_our_way,
            )
            for m in moves[:60]
        ],
    )


class PropCandidateOut(BaseModel):
    player_id: str
    player_name: str
    position: str | None = None
    team: str | None = None
    games: int


class PropProjectionOut(BaseModel):
    player_id: str
    player_name: str
    position: str | None = None
    team: str | None = None
    opponent: str
    market: str
    market_label: str

    expected: float
    expected_median: float | None = None
    sd: float
    games_of_history: int
    band: str
    trustworthy: bool

    projected_volume: float
    projected_efficiency: float
    opponent_factor: float
    context_factor: float
    snap_pct: float | None = None
    target_share: float | None = None
    recent_yards: list[float] = []
    notes: list[str] = []

    # Reliability, measured on a held-out season.
    calibration: str
    calibration_note: str
    worst_calibration_gap: float

    # Present only when a line was supplied.
    line: float | None = None
    prob_over: float | None = None
    prob_under: float | None = None
    implied_fair_american: int | None = None


@router.get("/props/players", response_model=list[PropCandidateOut])
def prop_players(
    market: str = Query("recv_yds"),
    season: int | None = None,
    search: str | None = None,
    limit: int = Query(60, ge=1, le=300),
    session: Session = Depends(get_session),
) -> list[PropCandidateOut]:
    """Players with enough history to project in this market."""
    from sqlalchemy import func

    from app.models import NflPlayerGame
    from app.services.nfl_props import Market

    try:
        mk = Market(market)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"unknown market {market!r}") from exc

    if season is None:
        season = session.scalar(
            select(func.max(NflPlayerGame.season)).where(NflPlayerGame.season < 2026)
        )

    volume_col = getattr(NflPlayerGame, mk.volume)
    stmt = (
        select(
            NflPlayerGame.player_id,
            func.max(NflPlayerGame.player_name),
            func.max(NflPlayerGame.position),
            func.max(NflPlayerGame.team),
            func.count().label("games"),
        )
        .where(
            NflPlayerGame.season == season,
            NflPlayerGame.position.in_(mk.positions),
            volume_col > 0,
        )
        .group_by(NflPlayerGame.player_id)
        .having(func.count() >= 6)
        .order_by(func.sum(getattr(NflPlayerGame, mk.stat)).desc())
        .limit(limit)
    )
    if search:
        stmt = stmt.where(NflPlayerGame.player_name.ilike(f"%{search}%"))

    return [
        PropCandidateOut(
            player_id=pid, player_name=name, position=pos, team=team, games=games
        )
        for pid, name, pos, team, games in session.execute(stmt).all()
    ]


@router.get("/props/project", response_model=PropProjectionOut)
def prop_project(
    player_id: str,
    opponent: str,
    market: str = Query("recv_yds"),
    season: int | None = None,
    week: int = Query(18, ge=1, le=23),
    line: float | None = None,
    is_home: bool = True,
    roof: str | None = None,
    wind: float | None = None,
    temp: float | None = None,
    session: Session = Depends(get_session),
) -> PropProjectionOut:
    """Project one player's yardage distribution, and price a line if you supply one.

    You bring the line from wherever you like -- this needs no second book, because the
    probability comes from the player's own distribution rather than from devigging a
    market against itself.
    """
    from sqlalchemy import func

    from app.models import NflPlayerGame
    from app.services.nfl_props import Market, project_prop

    try:
        mk = Market(market)
    except ValueError as exc:
        raise HTTPException(status_code=404, detail=f"unknown market {market!r}") from exc

    if season is None:
        season = session.scalar(
            select(func.max(NflPlayerGame.season)).where(NflPlayerGame.season < 2026)
        )

    projection = project_prop(
        session, player_id, mk,
        season=season, week=week, opponent=opponent.upper(),
        is_home=is_home, roof=roof, wind=wind, temp=temp,
    )
    if projection is None:
        raise HTTPException(
            status_code=404, detail="not enough history for this player in this market"
        )

    prob_over = projection.prob_over(line) if line is not None else None
    fair = None
    if prob_over is not None and 0 < prob_over < 1:
        # The price this probability would be worth with no vig, so it can be compared
        # directly with whatever the book is offering.
        fair = (
            round(-100 * prob_over / (1 - prob_over))
            if prob_over > 0.5
            else round(100 * (1 - prob_over) / prob_over)
        )

    return PropProjectionOut(
        player_id=projection.player_id,
        player_name=projection.player_name,
        position=projection.position,
        team=projection.team,
        opponent=projection.opponent,
        market=mk.value,
        market_label=mk.label,
        expected=round(projection.expected, 1),
        expected_median=round(projection.median, 1) if projection.median else None,
        sd=round(projection.sd, 1),
        games_of_history=projection.games_of_history,
        band=projection.band.value,
        trustworthy=projection.trustworthy,
        projected_volume=round(projection.projected_volume, 2),
        projected_efficiency=round(projection.projected_efficiency, 2),
        opponent_factor=round(projection.opponent_factor, 3),
        context_factor=round(projection.context_factor, 3),
        snap_pct=round(projection.snap_pct, 3) if projection.snap_pct else None,
        target_share=round(projection.target_share, 3) if projection.target_share else None,
        recent_yards=[round(y, 1) for y in projection.recent_yards],
        notes=projection.notes,
        calibration=projection.calibration.value,
        calibration_note=projection.calibration_note,
        worst_calibration_gap=projection.worst_calibration_gap,
        line=line,
        prob_over=round(prob_over, 4) if prob_over is not None else None,
        prob_under=round(1 - prob_over, 4) if prob_over is not None else None,
        implied_fair_american=fair,
    )


class GradedPropOut(BaseModel):
    player_name: str
    player_id: str | None = None
    team: str | None = None
    opponent: str
    market: str
    market_label: str
    side: str
    line: float
    book: str
    price_american: int
    projected: float
    projected_median: float | None = None
    model_prob: float
    break_even: float
    edge: float
    required_edge: float
    edge_ratio: float
    expected_value: float
    grade: str
    grade_description: str
    reason: str
    games_of_history: int
    band: str
    calibration: str
    recent_yards: list[float] = []
    recent_over: int = 0
    recent_counted: int = 0
    books_posting: int = 1
    line_span: float | None = None
    coverage_warning: str | None = None


class ScanOut(BaseModel):
    season: int | None = None
    week: int | None = None
    lines_seen: int
    graded_count: int
    actionable_count: int
    players_without_history: int
    one_sided_warning: str | None = None
    coverage_warning: str | None = None
    missing_games_warning: str | None = None
    games_in_week: int = 0
    games_with_lines: int = 0
    grade_counts: dict[str, int] = {}
    props: list[GradedPropOut] = []


@router.get("/props/scan", response_model=ScanOut)
def props_scan(
    season: int | None = None,
    week: int | None = None,
    min_grade: str = Query("D", pattern="^[ABCD]$"),
    limit: int = Query(100, ge=1, le=400),
    session: Session = Depends(get_session),
) -> ScanOut:
    """Grade every posted prop line for a week, best first.

    All three markets get identical treatment. They differ only in the bar an edge must
    clear, which is that market's own measured calibration error.
    """
    from collections import Counter

    from app.services.nfl_prop_scanner import scan_week

    result = scan_week(session, season=season, week=week)
    order = {"A": 0, "B": 1, "C": 2, "D": 3}
    cutoff = order[min_grade]
    rows = [g for g in result.graded if order[g.grade.value] <= cutoff][:limit]

    return ScanOut(
        season=result.season,
        week=result.week,
        lines_seen=result.lines_seen,
        graded_count=len(result.graded),
        actionable_count=len(result.actionable),
        players_without_history=result.players_without_history,
        one_sided_warning=result.one_sided_warning,
        coverage_warning=result.coverage_warning,
        missing_games_warning=result.missing_games_warning,
        games_in_week=result.games_in_week,
        games_with_lines=result.games_with_lines,
        grade_counts=dict(Counter(g.grade.value for g in result.graded)),
        props=[
            GradedPropOut(
                player_name=g.player_name,
                player_id=g.player_id,
                team=g.team,
                opponent=g.opponent,
                market=g.market.value,
                market_label=g.market.label,
                side=g.side,
                line=g.line,
                book=g.book,
                price_american=g.price_american,
                projected=round(g.projected, 1),
                projected_median=round(g.projected_median, 1)
                if g.projected_median is not None
                else None,
                model_prob=round(g.model_prob, 4),
                break_even=round(g.break_even, 4),
                edge=round(g.edge, 4),
                required_edge=round(g.required_edge, 4),
                edge_ratio=round(g.edge_ratio, 2),
                expected_value=round(g.expected_value, 4),
                grade=g.grade.value,
                grade_description=g.grade.description,
                reason=g.reason,
                books_posting=g.books_posting,
                line_span=round(g.line_span, 1) if g.line_span is not None else None,
                coverage_warning=g.coverage_warning,
                games_of_history=g.games_of_history,
                band=g.band.value,
                calibration=g.calibration,
                recent_yards=[round(y, 1) for y in g.recent_yards],
                recent_over=g.recent_vs_line[0],
                recent_counted=g.recent_vs_line[1],
            )
            for g in rows
        ],
    )


@router.get("/status", response_model=NflStatusOut)
def status(session: Session = Depends(get_session)) -> NflStatusOut:
    """Data coverage and an honest note on what the model is worth."""
    seasons = sorted(
        {s for s in session.scalars(select(NflGame.season).distinct()).all() if s}
    )
    completed = len(
        session.scalars(select(NflGame.id).where(NflGame.home_score.is_not(None))).all()
    )
    scheduled = len(
        session.scalars(select(NflGame.id).where(NflGame.home_score.is_(None))).all()
    )
    upcoming = next((s for s in reversed(seasons) if s), None)

    return NflStatusOut(
        seasons=seasons,
        completed_games=completed,
        scheduled_games=scheduled,
        upcoming_season=upcoming,
        holdout_season=HOLDOUT_SEASON,
        model_beats_market=False,
        model_note=(
            "The projection model does not beat the closing line: 10.81 MAE on totals "
            "against the market's 10.26 over 1,310 walk-forward games. It is shown for "
            "context, never as a reason to bet. The splits below need no model to be "
            "correct and stand on their own."
        ),
    )
