"""Poll player prop lines for the NFL slate.

Player props are only on the per-event endpoint, so cost scales with games: three markets
across a 16-game week is 48 credits, about 206 a month. Affordable precisely because the
NFL plays one slate a week rather than one a day -- the same three markets on an MLB slate
would cost 45 credits every single day.

Unlike the MLB props job, this does not need many books per line. The projection supplies
the probability; a book only has to supply the number. One book is enough to grade a prop.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ApiUsage, NflGame, NflPlayerGame, NflPropLine
from app.providers import oddsapi
from app.providers.http import ProviderError
from app.services.credit_budget import can_spend
from app.services.ingest_nfl_odds import SPORT_KEY

log = logging.getLogger(__name__)

# Odds API market keys mapped to our internal markets.
PROP_MARKETS = {
    "player_reception_yds": "recv_yds",
    "player_rush_yds": "rush_yds",
    "player_pass_yds": "pass_yds",
}


@dataclass
class PropIngestResult:
    polled_games: int = 0
    lines_written: int = 0
    credits_used: int = 0
    credits_remaining: int | None = None
    skipped: list[str] = field(default_factory=list)
    dry_run: bool = False

    def summary(self) -> str:
        if self.dry_run:
            return f"DRY RUN: would poll {self.polled_games} games for {self.credits_used} credits"
        return (
            f"{self.lines_written} lines from {self.polled_games} games, "
            f"{self.credits_used} credits used, {self.credits_remaining} remaining"
        )


def _resolve_player_ids(session: Session, season: int) -> dict[str, str]:
    """Map lowercased player name to our player id.

    Books spell names their own way, so a miss leaves `player_id` null rather than
    guessing -- the projection can still be found by name, and a wrong id would attach
    one player's history to another's line.
    """
    rows = session.execute(
        select(NflPlayerGame.player_name, NflPlayerGame.player_id)
        .where(NflPlayerGame.season >= season - 1)
        .distinct()
    ).all()
    return {name.strip().lower(): pid for name, pid in rows if name}


def poll_nfl_props(
    session: Session,
    *,
    season: int | None = None,
    week: int | None = None,
    limit: int | None = None,
    dry_run: bool = False,
    lookahead_days: int = 10,
) -> PropIngestResult:
    """Fetch player prop lines for upcoming NFL games."""
    settings = get_settings()
    result = PropIngestResult(dry_run=dry_run)

    if not settings.odds_enabled:
        result.skipped.append("no API key")
        return result

    now = datetime.now(UTC)
    stmt = select(NflGame).where(
        NflGame.home_score.is_(None),
        NflGame.gameday >= now.date(),
        NflGame.gameday <= (now + timedelta(days=lookahead_days)).date(),
    )
    if season:
        stmt = stmt.where(NflGame.season == season)
    if week:
        stmt = stmt.where(NflGame.week == week)
    games = session.scalars(stmt.order_by(NflGame.gameday)).all()

    if not games:
        result.skipped.append("no upcoming NFL games in the window")
        return result

    if limit:
        games = games[:limit]

    markets = list(PROP_MARKETS)
    regions = max(1, len([r for r in settings.odds_regions.split(",") if r.strip()]))
    cost_per_game = len(markets) * regions

    # Event ids come from the free /events listing rather than being guessed.
    events = {}
    try:
        listing = oddsapi.events(settings.the_odds_api_key, SPORT_KEY)
        for event in listing.data or []:
            events[(event.get("home_team"), event.get("away_team"))] = event.get("id")
    except ProviderError as exc:
        result.skipped.append(f"event listing failed: {exc}")
        return result

    from app.services.ingest_nfl_odds import TEAM_BY_NAME

    by_abbrev = {}
    for (home_name, away_name), event_id in events.items():
        if not home_name or not away_name:
            continue
        home = TEAM_BY_NAME.get(str(home_name).strip().lower())
        away = TEAM_BY_NAME.get(str(away_name).strip().lower())
        if home and away:
            by_abbrev[(home, away)] = event_id

    name_to_id = _resolve_player_ids(session, season or now.year)

    for game in games:
        # Event ids come from the free /events listing, matched on our team crosswalk.
        event_id = by_abbrev.get((game.home_team, game.away_team))
        if not event_id:
            continue

        if dry_run:
            result.polled_games += 1
            result.credits_used += cost_per_game
            continue

        # Re-checked immediately before each call so no caller can outrun the floor.
        if not can_spend(session, cost_per_game):
            result.skipped.append("credit reserve reached mid-run")
            break

        started = datetime.now(UTC)
        try:
            response = oddsapi.event_odds(
                settings.the_odds_api_key,
                SPORT_KEY,
                event_id,
                regions=settings.odds_regions,
                markets=markets,
                odds_format=settings.odds_format,
            )
        except ProviderError as exc:
            session.add(
                ApiUsage(
                    provider="the_odds_api",
                    endpoint=f"/events/{event_id}/odds",
                    sport_key=SPORT_KEY,
                    ok=False,
                    note=str(exc)[:500],
                    called_at=started,
                )
            )
            session.commit()
            result.skipped.append(f"{game.game_id}: {exc}")
            continue

        session.add(
            ApiUsage(
                provider="the_odds_api",
                endpoint=f"/events/{event_id}/odds",
                sport_key=SPORT_KEY,
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
        result.credits_used += response.requests_last or cost_per_game
        result.credits_remaining = response.requests_remaining
        result.lines_written += _store(session, game, response, name_to_id)

    log.info("nfl props: %s", result.summary())
    return result


def _store(
    session: Session,
    game: NflGame,
    response: oddsapi.OddsResponse,
    name_to_id: dict[str, str],
) -> int:
    written = 0
    for book in response.data.get("bookmakers", []) or []:
        book_key = book.get("key") or "unknown"
        for market in book.get("markets", []) or []:
            internal = PROP_MARKETS.get(market.get("key") or "")
            if not internal:
                continue
            for outcome in market.get("outcomes", []) or []:
                # Player props carry the person in `description` and Over/Under in `name`.
                player = outcome.get("description")
                side = outcome.get("name")
                point = outcome.get("point")
                price = outcome.get("price")
                if not player or not side or point is None or price is None:
                    continue

                existing = session.scalar(
                    select(NflPropLine).where(
                        NflPropLine.game_id == game.game_id,
                        NflPropLine.market == internal,
                        NflPropLine.player_name == player,
                        NflPropLine.bookmaker == book_key,
                        NflPropLine.outcome == side,
                        NflPropLine.fetched_at == response.fetched_at,
                    )
                )
                if existing is not None:
                    continue

                session.add(
                    NflPropLine(
                        game_id=game.game_id,
                        season=game.season,
                        week=game.week,
                        market=internal,
                        player_name=str(player),
                        player_id=name_to_id.get(str(player).strip().lower()),
                        bookmaker=book_key,
                        outcome=str(side),
                        point=float(point),
                        price_american=int(price),
                        fetched_at=response.fetched_at,
                    )
                )
                written += 1

    session.commit()
    return written
