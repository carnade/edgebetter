"""Fetch and store sportsbook odds, while staying inside a 500-credit monthly budget.

Three guards keep usage in budget, in order of how much they save:
  1. Skip entirely when no game starts soon -- this is what stops the NBA offseason
     from burning credits on empty responses for six weeks.
  2. Refuse to poll once remaining credits fall under a reserve floor.
  3. Record every call's quota headers so the floor is based on the provider's own
     accounting rather than our guess.

The frontend never calls this. It reads the snapshots this job writes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ApiUsage, Game, OddsSnapshot, Sport
from app.providers import oddsapi
from app.providers.http import ProviderError
from app.services.team_map import UnknownTeamError, abbrev_for_odds_name

log = logging.getLogger(__name__)

SPORT_KEYS = {Sport.MLB: "baseball_mlb", Sport.NBA: "basketball_nba"}


@dataclass(frozen=True)
class OddsPollResult:
    polled: bool
    reason: str
    snapshots_written: int = 0
    games_matched: int = 0
    credits_used: int | None = None
    credits_remaining: int | None = None


def latest_remaining(session: Session) -> int | None:
    """Most recent credit balance the provider reported."""
    return session.scalar(
        select(ApiUsage.requests_remaining)
        .where(ApiUsage.provider == "the_odds_api", ApiUsage.requests_remaining.is_not(None))
        .order_by(ApiUsage.called_at.desc())
        .limit(1)
    )


def _upcoming_game_count(session: Session, sport: Sport, hours: int) -> int:
    """Games that have not started yet within the lookahead window.

    Deliberately excludes in-progress games: their prices are in-play, which this tool
    does not analyse, so a slate already under way is not a reason to spend a credit.
    """
    now = datetime.now(UTC)
    return len(
        session.scalars(
            select(Game.id).where(
                Game.sport == sport,
                Game.is_final.is_(False),
                Game.start_time >= now,
                Game.start_time <= now + timedelta(hours=hours),
            )
        ).all()
    )


def poll_odds(session: Session, sport: Sport, *, force: bool = False) -> OddsPollResult:
    settings = get_settings()
    sport_key = SPORT_KEYS[sport]

    if not settings.odds_enabled:
        log.info("odds: THE_ODDS_API_KEY not set; skipping %s (stats are unaffected)", sport.value)
        return OddsPollResult(polled=False, reason="no_api_key")

    # Guard 1: nothing to price.
    upcoming = _upcoming_game_count(session, sport, settings.odds_lookahead_hours)
    if upcoming == 0 and not force:
        log.info(
            "odds: no %s game within %dh; skipping to preserve credits",
            sport.value,
            settings.odds_lookahead_hours,
        )
        return OddsPollResult(polled=False, reason="no_upcoming_games")

    # Guard 2: protect the reserve floor.
    cost = settings.credit_cost(sport_key)
    remaining = latest_remaining(session)
    if remaining is not None and remaining - cost < settings.odds_credit_reserve:
        log.warning(
            "odds: %d credits remaining, call costs %d, reserve floor is %d; refusing to poll",
            remaining,
            cost,
            settings.odds_credit_reserve,
        )
        return OddsPollResult(
            polled=False, reason="credit_reserve", credits_remaining=remaining
        )

    started = datetime.now(UTC)
    try:
        response = oddsapi.odds(
            settings.the_odds_api_key,
            sport_key,
            regions=settings.odds_regions,
            markets=settings.markets_for(sport_key),
            odds_format=settings.odds_format,
        )
    except ProviderError as exc:
        session.add(
            ApiUsage(
                provider="the_odds_api",
                endpoint=f"/sports/{sport_key}/odds",
                sport_key=sport_key,
                ok=False,
                note=str(exc)[:500],
                called_at=started,
            )
        )
        session.commit()
        log.error("odds poll for %s failed: %s", sport.value, exc)
        return OddsPollResult(polled=False, reason=f"error: {exc}")

    session.add(
        ApiUsage(
            provider="the_odds_api",
            endpoint=f"/sports/{sport_key}/odds",
            sport_key=sport_key,
            requests_last=response.requests_last,
            requests_used=response.requests_used,
            requests_remaining=response.requests_remaining,
            status_code=response.status_code,
            ok=True,
            called_at=response.fetched_at,
        )
    )
    session.commit()

    written, matched = _store(session, sport, response)
    log.info(
        "odds %s: %d snapshots across %d games (cost %s, %s credits left)",
        sport.value,
        written,
        matched,
        response.requests_last,
        response.requests_remaining,
    )
    return OddsPollResult(
        polled=True,
        reason="ok",
        snapshots_written=written,
        games_matched=matched,
        credits_used=response.requests_last,
        credits_remaining=response.requests_remaining,
    )


def _store(session: Session, sport: Sport, response: oddsapi.OddsResponse) -> tuple[int, int]:
    """Persist one poll as immutable snapshot rows.

    Only pre-game prices are kept. Once a game starts, books switch to in-play pricing:
    a total of 3.5 on a game whose full-game number was 9 is not a line that moved, it
    is a different market. Mixing the two would corrupt the line-movement history and
    invite meaningless comparisons against a full-game projection.
    """
    written = 0
    matched = 0
    now = datetime.now(UTC)

    for event in response.data or []:
        game = _match_game(session, sport, event)
        if game is None:
            continue
        if game.start_time <= now or game.is_final:
            log.debug("skipping in-play prices for game %s", game.external_id)
            continue
        matched += 1

        for book in event.get("bookmakers") or []:
            book_key = book.get("key") or "unknown"
            for market in book.get("markets") or []:
                market_key = market.get("key")
                if not market_key:
                    continue
                for outcome in market.get("outcomes") or []:
                    price = outcome.get("price")
                    name = outcome.get("name")
                    if price is None or name is None:
                        continue
                    session.add(
                        OddsSnapshot(
                            game_id=game.id,
                            bookmaker=book_key,
                            market=market_key,
                            outcome=str(name),
                            price_american=int(price),
                            point=outcome.get("point"),
                            fetched_at=response.fetched_at,
                        )
                    )
                    written += 1

    session.commit()
    return written, matched


def _match_game(session: Session, sport: Sport, event: dict) -> Game | None:
    """Map an odds event onto a scheduled game via the crosswalk.

    An unmapped team name is fatal by design: silently dropping it would make games
    vanish from the slate with no visible cause.
    """
    home_name = event.get("home_team")
    away_name = event.get("away_team")
    commence = event.get("commence_time")
    if not home_name or not away_name or not commence:
        return None

    home_abbrev = abbrev_for_odds_name(sport, home_name)
    away_abbrev = abbrev_for_odds_name(sport, away_name)
    start = datetime.fromisoformat(str(commence).replace("Z", "+00:00"))

    # Match on teams within a day of the posted start; books and leagues disagree on
    # exact start times, and doubleheaders share a date.
    candidates = session.scalars(
        select(Game).where(
            Game.sport == sport,
            Game.start_time >= start - timedelta(hours=24),
            Game.start_time <= start + timedelta(hours=24),
        )
    ).all()

    best = None
    best_delta = timedelta(days=99)
    for game in candidates:
        if game.home_team.abbrev != home_abbrev or game.away_team.abbrev != away_abbrev:
            continue
        delta = abs(game.start_time - start)
        if delta < best_delta:
            best, best_delta = game, delta

    if best is None:
        log.debug("odds event %s @ %s has no scheduled game", away_abbrev, home_abbrev)
    return best


def latest_snapshots(session: Session, game_id: int) -> list[OddsSnapshot]:
    """Most recent snapshot per (book, market, outcome) for a game."""
    rows = session.scalars(
        select(OddsSnapshot)
        .where(OddsSnapshot.game_id == game_id)
        .order_by(OddsSnapshot.fetched_at.desc())
    ).all()
    seen: set[tuple[str, str, str, float | None]] = set()
    latest: list[OddsSnapshot] = []
    for row in rows:
        key = (row.bookmaker, row.market, row.outcome, row.point)
        if key in seen:
            continue
        seen.add(key)
        latest.append(row)
    return latest


__all__ = [
    "OddsPollResult",
    "UnknownTeamError",
    "latest_remaining",
    "latest_snapshots",
    "poll_odds",
]
