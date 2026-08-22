"""Ingest NBA data from ESPN.

ESPN is the backbone: free, unthrottled, and complete enough to run the whole model.
It exposes team totals but not opponent totals, so points-allowed and defensive rating
are derived here from ingested game logs rather than fetched from a second upstream.
"""

from __future__ import annotations

import logging
import time
from datetime import UTC, date, datetime, timedelta

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.models import Game, GameTeamLog, Sport, StatSource, Team, TeamSeasonStats
from app.providers import espn
from app.providers.http import ProviderError
from app.services.team_map import team_by_espn_id

log = logging.getLogger(__name__)

# ESPN labels a season by its end year: season=2026 is the 2025-26 season.
# Regular seasons run roughly October through mid-April.
SEASON_START = (10, 1)
SEASON_END = (4, 20)

BACKFILL_DELAY_SECONDS = 0.4

# ESPN tags the NBA Cup championship as season type 2 (regular season), but the game
# does not count toward official regular-season team statistics -- only the two
# finalists would get an 83rd game, quietly skewing their ratings. Competition type
# 39 / "CC" is the marker.
EXCLUDED_COMPETITION_TYPES = {"39", "CC"}


def season_date_range(season: int) -> tuple[date, date]:
    """Calendar span to sweep for a given ESPN season year."""
    return date(season - 1, *SEASON_START), date(season, *SEASON_END)


def ingest_scoreboard(session: Session, day: date, season: int) -> int:
    """Upsert every game on a date. Idempotent, so re-running is safe."""
    try:
        events = espn.scoreboard(day, "nba")
    except ProviderError as exc:
        log.warning("nba scoreboard %s failed: %s", day, exc)
        raise

    written = 0
    for event in events:
        competitions = event.get("competitions") or []
        if not competitions:
            continue
        comp = competitions[0]

        # Only regular season (type 2). Preseason and All-Star games would poison ratings.
        if int((event.get("season") or {}).get("type") or 0) != 2:
            continue

        comp_type = comp.get("type") or {}
        if {str(comp_type.get("id")), str(comp_type.get("abbreviation"))} & EXCLUDED_COMPETITION_TYPES:
            log.debug("skipping non-counting event %s (%s)", event.get("id"), comp_type)
            continue

        home_raw = away_raw = None
        for c in comp.get("competitors", []):
            if c.get("homeAway") == "home":
                home_raw = c
            elif c.get("homeAway") == "away":
                away_raw = c
        if not home_raw or not away_raw:
            continue

        home = team_by_espn_id(session, int(home_raw["team"]["id"]))
        away = team_by_espn_id(session, int(away_raw["team"]["id"]))
        if home is None or away is None:
            log.debug("skipping event %s: team not in crosswalk", event.get("id"))
            continue

        external_id = str(event["id"])
        game = session.scalar(
            select(Game).where(Game.sport == Sport.NBA, Game.external_id == external_id)
        )
        if game is None:
            game = Game(sport=Sport.NBA, external_id=external_id)
            session.add(game)

        status = (comp.get("status") or event.get("status") or {}).get("type", {})
        game.season = season
        game.game_date = day
        game.start_time = datetime.fromisoformat(event["date"].replace("Z", "+00:00"))
        game.home_team_id = home.id
        game.away_team_id = away.id
        game.status = status.get("name", "STATUS_SCHEDULED")
        game.is_final = bool(status.get("completed"))

        # ESPN also reports 0-0 for scheduled games; only record a score once the
        # game is under way, so an unplayed fixture never looks like a 0-0 result.
        started = bool(status.get("completed")) or status.get("state") == "in"
        if started:
            try:
                game.home_score = int(home_raw["score"]) if home_raw.get("score") is not None else None
                game.away_score = int(away_raw["score"]) if away_raw.get("score") is not None else None
            except (TypeError, ValueError):
                game.home_score = game.away_score = None
        else:
            game.home_score = game.away_score = None

        session.flush()
        if game.is_final and game.home_score is not None and game.away_score is not None:
            _write_team_logs(session, game)
        written += 1

    session.commit()
    return written


def _write_team_logs(session: Session, game: Game) -> None:
    for team_id, opp_id, is_home, pf, pa in (
        (game.home_team_id, game.away_team_id, True, game.home_score, game.away_score),
        (game.away_team_id, game.home_team_id, False, game.away_score, game.home_score),
    ):
        row = session.scalar(
            select(GameTeamLog).where(
                GameTeamLog.game_id == game.id, GameTeamLog.team_id == team_id
            )
        )
        if row is None:
            row = GameTeamLog(game_id=game.id, team_id=team_id)
            session.add(row)
        row.opponent_id = opp_id
        row.season = game.season
        row.game_date = game.game_date
        row.is_home = is_home
        row.points_for = pf
        row.points_against = pa
        row.won = pf > pa


def backfill_season(
    session: Session, season: int, *, force: bool = False, delay: float = BACKFILL_DELAY_SECONDS
) -> int:
    """Sweep a whole season's scoreboards.

    Rate-limited and resumable: a past date that already holds final games is skipped
    unless forced, so an interrupted run picks up where it stopped instead of redoing
    ~250 requests.
    """
    start, end = season_date_range(season)
    today = datetime.now(UTC).date()
    end = min(end, today)

    total = 0
    skipped = 0
    day = start
    while day <= end:
        if not force and _date_already_complete(session, day, season):
            skipped += 1
            day += timedelta(days=1)
            continue
        try:
            total += ingest_scoreboard(session, day, season)
        except ProviderError:
            log.warning("backfill: giving up on %s, continuing", day)
        time.sleep(delay)
        day += timedelta(days=1)

    log.info("nba backfill %d: %d games written, %d dates skipped as already done", season, total, skipped)
    return total


def _date_already_complete(session: Session, day: date, season: int) -> bool:
    """True when this past date already has only-final games recorded."""
    if day >= datetime.now(UTC).date():
        return False
    total = session.scalar(
        select(func.count())
        .select_from(Game)
        .where(Game.sport == Sport.NBA, Game.game_date == day, Game.season == season)
    )
    if not total:
        return False
    pending = session.scalar(
        select(func.count())
        .select_from(Game)
        .where(
            Game.sport == Sport.NBA,
            Game.game_date == day,
            Game.season == season,
            Game.is_final.is_(False),
        )
    )
    return not pending


def ingest_team_stats(session: Session, season: int) -> int:
    """Season team aggregates from ESPN, plus opponent points derived from game logs.

    ESPN publishes points and possessions for a team but never for its opponents, so
    def_rating is computed from the game logs we already store. Possessions are close
    to equal for both teams in a basketball game, which makes team possessions a sound
    denominator for both ratings.
    """
    teams = session.scalars(select(Team).where(Team.sport == Sport.NBA)).all()
    now = datetime.now(UTC)
    written = 0

    for team in teams:
        if team.espn_id is None:
            continue
        try:
            stats = espn.team_season_statistics(team.espn_id, season, "nba")
        except ProviderError as exc:
            log.warning("nba team stats %s season %d failed: %s", team.abbrev, season, exc)
            continue
        if not stats:
            continue

        allowed_total, games_logged = _points_allowed(session, team.id, season)

        row = session.scalar(
            select(TeamSeasonStats).where(
                TeamSeasonStats.team_id == team.id,
                TeamSeasonStats.season == season,
                TeamSeasonStats.source == StatSource.ESPN,
            )
        )
        if row is None:
            row = TeamSeasonStats(
                team_id=team.id, sport=Sport.NBA, season=season, source=StatSource.ESPN
            )
            session.add(row)

        games_played = int(stats.get("gamesPlayed") or games_logged or 0) or None
        possessions = stats.get("possessions") or stats.get("estimatedPossessions")

        row.games_played = games_played
        row.points_for = stats.get("avgPoints")
        row.pace = stats.get("paceFactor")
        row.possessions = possessions

        if possessions:
            points = stats.get("points")
            if points:
                row.off_rating = 100.0 * points / possessions
            if allowed_total and games_logged and games_played:
                # Scale logged points-allowed to the full season in case the log is partial.
                scaled = allowed_total * (games_played / games_logged)
                row.points_against = allowed_total / games_logged
                row.def_rating = 100.0 * scaled / possessions

        row.raw = stats
        row.fetched_at = now
        written += 1

    session.commit()
    log.info("nba team stats %d: %d teams", season, written)
    return written


def _points_allowed(session: Session, team_id: int, season: int) -> tuple[float, int]:
    result = session.execute(
        select(func.sum(GameTeamLog.points_against), func.count())
        .select_from(GameTeamLog)
        .where(GameTeamLog.team_id == team_id, GameTeamLog.season == season)
    ).one()
    return float(result[0] or 0.0), int(result[1] or 0)
