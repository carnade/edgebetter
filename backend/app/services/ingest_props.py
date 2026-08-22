"""Per-event market polling: first 5 innings, team totals, and player props.

Unlike game-level odds, these bill per game, so a naive loop over a 15-game slate at
four markets costs 60 credits -- an eighth of the monthly free tier in one job. Every
call here is therefore gated twice: the allocator decides how many games are affordable,
and `credit_budget.can_spend` re-checks immediately before each request so no caller can
bypass the reserve floor.

Which games get polled is decided by the mismatch ranking, so free data chooses where
the metered spend goes.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ApiUsage, Game, OddsSnapshot, Sport
from app.providers import oddsapi
from app.providers.http import ProviderError
from app.services.credit_budget import Budget, can_spend, compute
from app.services.ingest_odds import SPORT_KEYS

log = logging.getLogger(__name__)

# Markets where the outcome's `description` field carries the subject (a player for
# props, a team for team totals) and `name` carries Over/Under.
DESCRIPTION_MARKETS = {
    "pitcher_strikeouts",
    "pitcher_hits_allowed",
    "pitcher_outs",
    "pitcher_earned_runs",
    "pitcher_walks",
    "batter_hits",
    "batter_home_runs",
    "batter_total_bases",
    "batter_rbis",
    "team_totals",
}


@dataclass
class PropsPollResult:
    polled_games: int = 0
    snapshots_written: int = 0
    credits_spent: int = 0
    credits_remaining: int | None = None
    skipped: list[str] = field(default_factory=list)
    dry_run: bool = False
    budget: Budget | None = None

    def summary(self) -> str:
        if self.dry_run:
            return (
                f"DRY RUN: would poll {self.polled_games} games for "
                f"{self.credits_spent} credits"
            )
        return (
            f"{self.snapshots_written} snapshots from {self.polled_games} games, "
            f"{self.credits_spent} credits spent, {self.credits_remaining} remaining"
        )


def select_games(session: Session, season: int, limit: int) -> list[Game]:
    """The games worth spending credits on, ranked by the mismatch score.

    Only games with a cached event id are eligible -- discovering one costs a free
    /events call, never a metered request.
    """
    from app.services.mismatches import find_mismatches

    ranked = find_mismatches(session, season, hours_ahead=36)
    chosen: list[Game] = []
    for mismatch in ranked:
        if len(chosen) >= limit:
            break
        game = session.get(Game, mismatch.game_id)
        if game is None or not game.odds_event_id:
            continue
        chosen.append(game)
    return chosen


def poll_props(
    session: Session,
    sport: Sport = Sport.MLB,
    *,
    season: int | None = None,
    limit: int | None = None,
    dry_run: bool | None = None,
) -> PropsPollResult:
    """Poll per-event markets for the most lopsided affordable games."""
    settings = get_settings()
    dry_run = settings.props_dry_run if dry_run is None else dry_run
    markets = settings.props_markets_list
    result = PropsPollResult(dry_run=dry_run)

    if not settings.odds_enabled:
        result.skipped.append("no API key")
        return result

    budget = compute(session, props_markets=markets)
    result.budget = budget
    allowed = budget.props_games_today if limit is None else min(limit, budget.props_games_today)

    if allowed <= 0:
        result.skipped.append(budget.reason)
        log.info("props: nothing affordable today (%s)", budget.reason)
        return result

    if season is None:
        from app.services.season_resolver import mlb_season

        season = mlb_season().season

    games = select_games(session, season, allowed)
    if not games:
        result.skipped.append("no ranked games with a cached event id")
        return result

    per_game = budget.props_markets_per_game

    for game in games:
        if dry_run:
            result.polled_games += 1
            result.credits_spent += per_game
            log.info(
                "props DRY RUN: would poll %s @ %s for %s",
                game.away_team.abbrev,
                game.home_team.abbrev,
                ",".join(markets),
            )
            continue

        # Second gate, immediately before spending. The allocator is advisory; this is
        # the line no caller gets past.
        if not can_spend(session, per_game):
            result.skipped.append("reserve floor reached mid-run")
            log.warning("props: stopping, reserve floor reached")
            break

        started = datetime.now(UTC)
        try:
            response = oddsapi.event_odds(
                settings.the_odds_api_key,
                SPORT_KEYS[sport],
                game.odds_event_id,
                regions=settings.odds_regions,
                markets=markets,
                odds_format=settings.odds_format,
            )
        except ProviderError as exc:
            session.add(
                ApiUsage(
                    provider="the_odds_api",
                    endpoint=f"/events/{game.odds_event_id}/odds",
                    sport_key=SPORT_KEYS[sport],
                    ok=False,
                    note=str(exc)[:500],
                    called_at=started,
                )
            )
            session.commit()
            log.error("props poll for game %s failed: %s", game.id, exc)
            result.skipped.append(f"game {game.id}: {exc}")
            continue

        session.add(
            ApiUsage(
                provider="the_odds_api",
                endpoint=f"/events/{game.odds_event_id}/odds",
                sport_key=SPORT_KEYS[sport],
                requests_last=response.requests_last,
                requests_used=response.requests_used,
                requests_remaining=response.requests_remaining,
                status_code=response.status_code,
                ok=True,
                called_at=response.fetched_at,
            )
        )
        session.commit()

        result.polled_games += 1
        result.credits_spent += response.requests_last or per_game
        result.credits_remaining = response.requests_remaining
        result.snapshots_written += _store_event(session, game, response)

    log.info("props %s: %s", sport.value, result.summary())
    return result


def _store_event(session: Session, game: Game, response: oddsapi.OddsResponse) -> int:
    """Persist one event's markets as immutable snapshots.

    Player props and team totals put the subject in `description` and Over/Under in
    `name`; straight markets put the selection in `name`. Both shapes collapse onto
    (market, player_name, outcome, point).
    """
    # A started game is priced in-play, which is a different market entirely.
    if game.start_time <= datetime.now(UTC) or game.is_final:
        log.debug("props: skipping in-play game %s", game.id)
        return 0

    written = 0
    for book in response.data.get("bookmakers", []) or []:
        book_key = book.get("key") or "unknown"
        for market in book.get("markets", []) or []:
            market_key = market.get("key")
            if not market_key:
                continue
            for outcome in market.get("outcomes", []) or []:
                price = outcome.get("price")
                name = outcome.get("name")
                if price is None or name is None:
                    continue
                subject = outcome.get("description") if market_key in DESCRIPTION_MARKETS else None
                session.add(
                    OddsSnapshot(
                        game_id=game.id,
                        bookmaker=book_key,
                        market=market_key,
                        outcome=str(name),
                        player_name=str(subject) if subject else None,
                        price_american=int(price),
                        point=outcome.get("point"),
                        fetched_at=response.fetched_at,
                    )
                )
                written += 1

    session.commit()
    return written
