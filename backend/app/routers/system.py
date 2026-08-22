"""Health and odds-quota endpoints."""

from __future__ import annotations

from fastapi import APIRouter, Depends
from sqlalchemy import select, text
from sqlalchemy.orm import Session

from app.config import get_settings
from app.db import get_session
from app.models import ApiUsage
from app.schemas.api import BudgetOut, HealthOut, OddsStatusOut

router = APIRouter(tags=["system"])


@router.get("/health", response_model=HealthOut)
def health(session: Session = Depends(get_session)) -> HealthOut:
    settings = get_settings()
    try:
        session.execute(text("SELECT 1"))
        db_ok = True
    except Exception:  # noqa: BLE001 - health must report, not raise
        db_ok = False
    return HealthOut(
        status="ok" if db_ok else "degraded",
        database=db_ok,
        odds_configured=settings.odds_enabled,
        nba_enrich_enabled=settings.enable_nba_stats_enrich,
    )


@router.get("/odds/status", response_model=OddsStatusOut)
def odds_status(session: Session = Depends(get_session)) -> OddsStatusOut:
    """Credit budget and last poll.

    The UI shows this so an empty odds panel always has a visible explanation --
    no key, no upcoming games, or credits held back at the reserve floor.
    """
    settings = get_settings()
    last = session.scalar(
        select(ApiUsage)
        .where(ApiUsage.provider == "the_odds_api")
        .order_by(ApiUsage.called_at.desc())
        .limit(1)
    )

    if not settings.odds_enabled:
        reason = "THE_ODDS_API_KEY is not set; stats work, edges need a key"
    elif last is None:
        reason = "configured, no poll recorded yet"
    elif last.ok:
        reason = "ok"
    else:
        reason = last.note or "last poll failed"

    return OddsStatusOut(
        enabled=settings.odds_enabled,
        reason=reason,
        credits_remaining=last.requests_remaining if last else None,
        credits_used=last.requests_used if last else None,
        last_poll=last.called_at if last else None,
        last_poll_ok=last.ok if last else None,
    )


@router.get("/odds/budget", response_model=BudgetOut)
def odds_budget(session: Session = Depends(get_session)) -> BudgetOut:
    """How the monthly credit quota is being divided today.

    Surfaced so an empty props table always explains itself: the answer is usually
    "game-level polling consumed the allowance", not "nothing was found".
    """
    from app.services.credit_budget import compute

    b = compute(session)
    return BudgetOut(
        remaining=b.remaining,
        days_left=b.days_left,
        daily_allowance=b.daily_allowance,
        reserve=b.reserve,
        game_level_cost_today=b.game_level_cost_today,
        props_allowance=b.props_allowance,
        props_markets_per_game=b.props_markets_per_game,
        props_games_today=b.props_games_today,
        reason=b.reason,
    )
