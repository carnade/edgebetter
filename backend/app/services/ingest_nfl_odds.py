"""Live NFL lines, stored as a consensus snapshot on each game.

The NFL side of this tool is a history tool: the question is what has happened under
these conditions, not which book is a half-point off. So this stores one consensus number
per game rather than per-book rows, which keeps it cheap and keeps the UI honest about
what it is for.

Cost is the reason it can exist at all. NFL runs ~16 games a week on the bulk `/odds`
endpoint, so one poll covers the whole slate for `markets x regions` -- 2 credits for
h2h+totals. That is a weekly cost of roughly 6 credits at three polls, against MLB's 186
a month, which is why it fits alongside everything else.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from statistics import median

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ApiUsage, NflGame, NflLineHistory
from app.providers import oddsapi
from app.providers.http import ProviderError
from app.services.credit_budget import can_spend

log = logging.getLogger(__name__)

SPORT_KEY = "americanfootball_nfl"
MARKETS = ("h2h", "spreads", "totals")

# nflverse abbreviations differ from the full names the odds feed uses.
TEAM_BY_NAME = {
    "arizona cardinals": "ARI", "atlanta falcons": "ATL", "baltimore ravens": "BAL",
    "buffalo bills": "BUF", "carolina panthers": "CAR", "chicago bears": "CHI",
    "cincinnati bengals": "CIN", "cleveland browns": "CLE", "dallas cowboys": "DAL",
    "denver broncos": "DEN", "detroit lions": "DET", "green bay packers": "GB",
    "houston texans": "HOU", "indianapolis colts": "IND", "jacksonville jaguars": "JAX",
    "kansas city chiefs": "KC", "las vegas raiders": "LV", "los angeles chargers": "LAC",
    # nflverse uses LA for the Rams, not LAR.
    "los angeles rams": "LA", "miami dolphins": "MIA", "minnesota vikings": "MIN",
    "new england patriots": "NE", "new orleans saints": "NO", "new york giants": "NYG",
    "new york jets": "NYJ", "philadelphia eagles": "PHI", "pittsburgh steelers": "PIT",
    "san francisco 49ers": "SF", "seattle seahawks": "SEA", "tampa bay buccaneers": "TB",
    "tennessee titans": "TEN", "washington commanders": "WAS",
}


class UnknownNflTeamError(LookupError):
    """The odds feed named a team we cannot map. Fatal rather than skipped."""


def abbrev(name: str) -> str:
    key = " ".join(str(name).strip().lower().split())
    try:
        return TEAM_BY_NAME[key]
    except KeyError as exc:
        raise UnknownNflTeamError(
            f"odds feed returned unmapped NFL team {name!r}; add it to TEAM_BY_NAME "
            f"rather than dropping the game silently"
        ) from exc


@dataclass
class NflOddsResult:
    polled: bool
    reason: str
    games_updated: int = 0
    credits_used: int | None = None
    credits_remaining: int | None = None

    def summary(self) -> str:
        if not self.polled:
            return f"skipped: {self.reason}"
        return (
            f"{self.games_updated} games updated, {self.credits_used} credits used, "
            f"{self.credits_remaining} remaining"
        )


def _record_observation(
    session: Session,
    game: NflGame,
    *,
    fetched_at: datetime,
    total_line: float | None,
    spread_line: float | None,
    home_ml: int | None,
    away_ml: int | None,
    book_count: int | None,
    source: str,
) -> None:
    """Append one line observation, with the model's view at that moment.

    The model projection is stored alongside rather than recomputed later, because
    recomputing it after the fact would use ratings that include games played since --
    the same look-ahead that inflated the NBA mismatch result from 58.7% to 89.5%.
    """
    existing = session.scalar(
        select(NflLineHistory).where(
            NflLineHistory.game_id == game.game_id,
            NflLineHistory.fetched_at == fetched_at,
        )
    )
    if existing is not None:
        return

    model_total = model_margin = None
    try:
        from app.services.nfl_projections import project_game

        projection = project_game(session, game)
        if projection is not None and not projection.thin_sample:
            model_total = round(projection.total, 2)
            model_margin = round(projection.margin, 2)
    except Exception as exc:  # noqa: BLE001 - a missing projection must not lose the line
        log.debug("no projection for %s: %s", game.game_id, exc)

    kickoff = datetime.combine(game.gameday, datetime.min.time()).replace(tzinfo=UTC)
    session.add(
        NflLineHistory(
            game_id=game.game_id,
            season=game.season,
            week=game.week,
            fetched_at=fetched_at,
            hours_to_kickoff=round((kickoff - fetched_at).total_seconds() / 3600.0, 1),
            total_line=total_line,
            spread_line=spread_line,
            home_moneyline=home_ml,
            away_moneyline=away_ml,
            book_count=book_count,
            model_total=model_total,
            model_margin=model_margin,
            source=source,
        )
    )


def seed_openers(session: Session, season: int) -> int:
    """Seed the opening baseline from the lines nflverse already carries.

    Without this the first live poll would be treated as the opener, and any movement
    that happened before we started watching would be invisible.
    """
    games = session.scalars(
        select(NflGame).where(
            NflGame.season == season,
            NflGame.home_score.is_(None),
            NflGame.total_line.is_not(None),
        )
    ).all()

    written = 0
    for game in games:
        already = session.scalar(
            select(NflLineHistory).where(
                NflLineHistory.game_id == game.game_id,
                NflLineHistory.source == "nflverse",
            )
        )
        if already is not None:
            continue
        _record_observation(
            session,
            game,
            # Dated to the load rather than invented; what matters is that it precedes
            # every live observation.
            fetched_at=datetime.now(UTC).replace(microsecond=0),
            total_line=game.total_line,
            spread_line=game.spread_line,
            home_ml=game.home_moneyline,
            away_ml=game.away_moneyline,
            book_count=None,
            source="nflverse",
        )
        written += 1

    session.commit()
    log.info("nfl openers seeded for %d: %d games", season, written)
    return written


def poll_nfl_odds(session: Session, *, lookahead_days: int = 10) -> NflOddsResult:
    """Refresh consensus lines for upcoming NFL games."""
    settings = get_settings()
    if not settings.odds_enabled:
        return NflOddsResult(polled=False, reason="no API key")

    now = datetime.now(UTC)
    upcoming = session.scalars(
        select(NflGame).where(
            NflGame.home_score.is_(None),
            NflGame.gameday >= now.date(),
            NflGame.gameday <= (now + timedelta(days=lookahead_days)).date(),
        )
    ).all()
    if not upcoming:
        return NflOddsResult(polled=False, reason="no NFL games within the lookahead window")

    cost = len(MARKETS) * max(
        1, len([r for r in settings.odds_regions.split(",") if r.strip()])
    )
    if not can_spend(session, cost):
        return NflOddsResult(polled=False, reason="credit reserve floor reached")

    started = datetime.now(UTC)
    try:
        response = oddsapi.odds(
            settings.the_odds_api_key,
            SPORT_KEY,
            regions=settings.odds_regions,
            markets=list(MARKETS),
            odds_format=settings.odds_format,
        )
    except ProviderError as exc:
        session.add(
            ApiUsage(
                provider="the_odds_api",
                endpoint=f"/sports/{SPORT_KEY}/odds",
                sport_key=SPORT_KEY,
                ok=False,
                note=str(exc)[:500],
                called_at=started,
            )
        )
        session.commit()
        log.error("nfl odds poll failed: %s", exc)
        return NflOddsResult(polled=False, reason=str(exc))

    session.add(
        ApiUsage(
            provider="the_odds_api",
            endpoint=f"/sports/{SPORT_KEY}/odds",
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

    by_teams = {(g.home_team, g.away_team): g for g in upcoming}
    updated = 0

    for event in response.data or []:
        home_name, away_name = event.get("home_team"), event.get("away_team")
        if not home_name or not away_name:
            continue
        game = by_teams.get((abbrev(home_name), abbrev(away_name)))
        if game is None:
            continue

        totals: list[float] = []
        spreads: list[float] = []
        home_ml: list[int] = []
        away_ml: list[int] = []

        for book in event.get("bookmakers", []) or []:
            for market in book.get("markets", []) or []:
                key = market.get("key")
                for outcome in market.get("outcomes", []) or []:
                    name = outcome.get("name")
                    point = outcome.get("point")
                    price = outcome.get("price")
                    if key == "totals" and name == "Over" and point is not None:
                        totals.append(float(point))
                    elif key == "spreads" and point is not None and name == home_name:
                        # Store from the home perspective, matching nflverse's convention
                        # where a positive spread_line means the home side is favoured.
                        spreads.append(-float(point))
                    elif key == "h2h" and price is not None:
                        if name == home_name:
                            home_ml.append(int(price))
                        elif name == away_name:
                            away_ml.append(int(price))

        # Median across books: one stale line should not move the consensus.
        total_line = median(totals) if totals else None
        spread_line = median(spreads) if spreads else None
        game.live_total_line = total_line
        game.live_spread_line = spread_line
        game.live_home_moneyline = int(median(home_ml)) if home_ml else None
        game.live_away_moneyline = int(median(away_ml)) if away_ml else None
        game.live_book_count = len(event.get("bookmakers", []) or [])
        game.odds_fetched_at = response.fetched_at

        _record_observation(
            session,
            game,
            fetched_at=response.fetched_at,
            total_line=total_line,
            spread_line=spread_line,
            home_ml=game.live_home_moneyline,
            away_ml=game.live_away_moneyline,
            book_count=game.live_book_count,
            source="odds_api",
        )
        updated += 1

    session.commit()
    result = NflOddsResult(
        polled=True,
        reason="ok",
        games_updated=updated,
        credits_used=response.requests_last,
        credits_remaining=response.requests_remaining,
    )
    log.info("nfl odds: %s", result.summary())
    return result
