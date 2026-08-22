"""Map our games to The Odds API's event ids using the free /events endpoint.

Discovery costs nothing, which is the whole reason selective per-event polling is
viable: we learn every event id for free, then spend credits only on the games the
model says are worth pricing.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import Game, Sport
from app.providers import oddsapi
from app.providers.http import ProviderError
from app.services.ingest_odds import SPORT_KEYS
from app.services.team_map import UnknownTeamError, abbrev_for_odds_name

log = logging.getLogger(__name__)


def refresh_event_ids(session: Session, sport: Sport, *, hours_ahead: int = 72) -> int:
    """Attach Odds API event ids to upcoming games. Free -- no quota consumed."""
    settings = get_settings()
    if not settings.odds_enabled:
        log.info("event map: no API key; skipping")
        return 0

    try:
        response = oddsapi.events(settings.the_odds_api_key, SPORT_KEYS[sport])
    except ProviderError as exc:
        log.warning("event listing for %s failed: %s", sport.value, exc)
        return 0

    now = datetime.now(UTC)
    games = session.scalars(
        select(Game).where(
            Game.sport == sport,
            Game.is_final.is_(False),
            Game.start_time >= now,
            Game.start_time <= now + timedelta(hours=hours_ahead),
        )
    ).all()

    # Index our games by (home, away, date) for a cheap lookup.
    index: dict[tuple[str, str], list[Game]] = {}
    for game in games:
        index.setdefault((game.home_team.abbrev, game.away_team.abbrev), []).append(game)

    matched = 0
    for event in response.data or []:
        home_name, away_name = event.get("home_team"), event.get("away_team")
        event_id, commence = event.get("id"), event.get("commence_time")
        if not (home_name and away_name and event_id and commence):
            continue

        try:
            home = abbrev_for_odds_name(sport, home_name)
            away = abbrev_for_odds_name(sport, away_name)
        except UnknownTeamError as exc:
            # Fatal by design elsewhere; here it only costs us one game's props, so log
            # loudly and continue rather than abandoning the whole refresh.
            log.error("event map: %s", exc)
            continue

        start = datetime.fromisoformat(str(commence).replace("Z", "+00:00"))
        candidates = index.get((home, away), [])
        if not candidates:
            continue

        # Doubleheaders share teams and date, so pick the closest start time.
        best = min(candidates, key=lambda g: abs(g.start_time - start))
        if abs(best.start_time - start) > timedelta(hours=24):
            continue
        if best.odds_event_id != event_id:
            best.odds_event_id = event_id
            matched += 1

    session.commit()
    log.info("event map %s: %d ids attached (free call)", sport.value, matched)
    return matched


def games_missing_event_id(session: Session, sport: Sport, *, hours_ahead: int = 48) -> int:
    now = datetime.now(UTC)
    return len(
        session.scalars(
            select(Game.id).where(
                Game.sport == sport,
                Game.is_final.is_(False),
                Game.start_time >= now,
                Game.start_time <= now + timedelta(hours=hours_ahead),
                Game.odds_event_id.is_(None),
            )
        ).all()
    )
