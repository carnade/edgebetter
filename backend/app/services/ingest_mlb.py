"""Ingest MLB data from the MLB Stats API into our schema.

The API is free and unthrottled, so these jobs poll generously. Failures raise rather
than writing partial rows -- a half-ingested slate is worse than a stale one.
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import (
    Game,
    GameInnings,
    GameTeamLog,
    PitcherGameLog,
    PitcherSeasonStats,
    Player,
    Sport,
    StatSource,
    Team,
    TeamSeasonStats,
)
from app.providers import mlb_statsapi as api
from app.services.parsing import innings_to_float, to_float, to_int
from app.services.team_map import team_by_mlb_id

log = logging.getLogger(__name__)

FINAL_STATES = {"Final", "Game Over", "Completed Early"}

# MLB reports `score: 0` for games that have not started yet (and omits the field
# entirely earlier still). Storing that zero would make an unplayed game look like a
# scoreless one, so scores are only recorded once play has actually begun.
LIVE_STATES = {"In Progress", "Manager Challenge", "Delayed", "Suspended", "Umpire Review"}


def _has_started(status: str) -> bool:
    return status in FINAL_STATES or status in LIVE_STATES


def _parse_start(game: dict[str, Any]) -> datetime:
    return datetime.fromisoformat(game["gameDate"].replace("Z", "+00:00"))


def _upsert_player(session: Session, payload: dict[str, Any], team: Team | None) -> Player | None:
    """Create or update a pitcher from a probablePitcher block."""
    mlb_id = payload.get("id")
    if not mlb_id:
        return None
    player = session.scalar(select(Player).where(Player.mlb_id == mlb_id))
    if player is None:
        player = Player(sport=Sport.MLB, mlb_id=mlb_id, full_name=payload.get("fullName", ""))
        session.add(player)
    if payload.get("fullName"):
        player.full_name = payload["fullName"]
    if team is not None:
        player.team_id = team.id
    player.primary_position = (payload.get("primaryPosition") or {}).get("abbreviation") or "P"
    return player


def ingest_schedule(session: Session, game_date: date, season: int | None = None) -> int:
    """Upsert one day's games, probable pitchers, and final scores."""
    games = api.schedule(game_date)
    season = season or game_date.year
    written = 0

    for payload in games:
        home_raw = payload["teams"]["home"]
        away_raw = payload["teams"]["away"]
        home = team_by_mlb_id(session, home_raw["team"]["id"])
        away = team_by_mlb_id(session, away_raw["team"]["id"])
        if home is None or away is None:
            # Spring-training and exhibition opponents are not in the crosswalk; skip.
            log.debug("skipping game %s: team not in crosswalk", payload.get("gamePk"))
            continue

        external_id = str(payload["gamePk"])
        game = session.scalar(
            select(Game).where(Game.sport == Sport.MLB, Game.external_id == external_id)
        )
        if game is None:
            game = Game(sport=Sport.MLB, external_id=external_id)
            session.add(game)

        status = payload.get("status", {}).get("detailedState", "Scheduled")
        game.season = season
        game.game_date = game_date
        game.start_time = _parse_start(payload)
        game.home_team_id = home.id
        game.away_team_id = away.id
        game.status = status
        game.is_final = status in FINAL_STATES
        if _has_started(status):
            game.home_score = to_int(home_raw.get("score"))
            game.away_score = to_int(away_raw.get("score"))
        else:
            game.home_score = game.away_score = None

        home_p = _upsert_player(session, home_raw.get("probablePitcher") or {}, home)
        away_p = _upsert_player(session, away_raw.get("probablePitcher") or {}, away)
        session.flush()
        game.home_probable_pitcher_id = home_p.id if home_p else None
        game.away_probable_pitcher_id = away_p.id if away_p else None

        session.flush()
        if game.is_final and game.home_score is not None and game.away_score is not None:
            _write_team_logs(session, game)
            _write_innings(session, game, payload.get("linescore") or {})
        written += 1

    session.commit()
    log.info("mlb schedule %s: %d games", game_date, written)
    return written


def _write_team_logs(session: Session, game: Game) -> None:
    """Flatten a final game into one row per team. Idempotent."""
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


def _write_innings(session: Session, game: Game, linescore: dict[str, Any]) -> None:
    """Store runs by inning so first-5-innings outcomes can be measured.

    Without this the F5 model would have to assume what fraction of a game's runs land
    in the first five innings, and that fraction is not obvious: starters are better
    than relievers, so F5 scoring runs below a simple 5/9 of the full game.
    """
    innings = linescore.get("innings") or []
    if not innings:
        return
    for entry in innings:
        number = entry.get("num")
        if not number:
            continue
        row = session.scalar(
            select(GameInnings).where(
                GameInnings.game_id == game.id, GameInnings.inning == number
            )
        )
        if row is None:
            row = GameInnings(game_id=game.id, inning=number)
            session.add(row)
        row.home_runs = to_int((entry.get("home") or {}).get("runs")) or 0
        row.away_runs = to_int((entry.get("away") or {}).get("runs")) or 0


def ingest_team_stats(session: Session, season: int) -> int:
    """Season hitting and pitching aggregates for all 30 clubs."""
    teams = session.scalars(select(Team).where(Team.sport == Sport.MLB)).all()
    now = datetime.now(UTC)
    written = 0

    for team in teams:
        if team.mlb_id is None:
            continue
        groups = api.team_season_stats(team.mlb_id, season)
        hitting = groups.get("hitting", {})
        pitching = groups.get("pitching", {})
        if not hitting and not pitching:
            continue

        row = session.scalar(
            select(TeamSeasonStats).where(
                TeamSeasonStats.team_id == team.id,
                TeamSeasonStats.season == season,
                TeamSeasonStats.source == StatSource.MLB_STATSAPI,
            )
        )
        if row is None:
            row = TeamSeasonStats(
                team_id=team.id,
                sport=Sport.MLB,
                season=season,
                source=StatSource.MLB_STATSAPI,
            )
            session.add(row)

        gp = to_int(hitting.get("gamesPlayed")) or to_int(pitching.get("gamesPlayed"))
        row.games_played = gp
        row.runs_for = to_float(hitting.get("runs"))
        row.runs_against = to_float(pitching.get("runs"))
        row.team_era = to_float(pitching.get("era"))
        row.team_whip = to_float(pitching.get("whip"))
        row.team_ops = to_float(hitting.get("ops"))

        plate_appearances = to_float(hitting.get("plateAppearances")) or to_float(
            hitting.get("atBats")
        )
        if plate_appearances:
            k = to_float(hitting.get("strikeOuts")) or 0.0
            bb = to_float(hitting.get("baseOnBalls")) or 0.0
            row.strikeout_rate = k / plate_appearances
            row.walk_rate = bb / plate_appearances

        row.raw = {"hitting": hitting, "pitching": pitching}
        row.fetched_at = now
        written += 1

    session.commit()
    log.info("mlb team stats %d: %d teams", season, written)
    return written


def _write_pitcher_season(
    session: Session, player: Player, stat: dict[str, Any], season: int
) -> None:
    row = session.scalar(
        select(PitcherSeasonStats).where(
            PitcherSeasonStats.player_id == player.id, PitcherSeasonStats.season == season
        )
    )
    if row is None:
        row = PitcherSeasonStats(player_id=player.id, season=season)
        session.add(row)

    ip = innings_to_float(stat.get("inningsPitched"))
    row.innings_pitched = ip
    row.games_started = to_int(stat.get("gamesStarted"))
    row.era = to_float(stat.get("era"))
    row.whip = to_float(stat.get("whip"))
    row.k_per_9 = to_float(stat.get("strikeoutsPer9Inn"))
    row.bb_per_9 = to_float(stat.get("walksPer9Inn"))
    row.hr_per_9 = to_float(stat.get("homeRunsPer9"))
    row.wins = to_int(stat.get("wins"))
    row.losses = to_int(stat.get("losses"))
    row.earned_runs = to_int(stat.get("earnedRuns"))
    row.strikeouts = to_int(stat.get("strikeOuts"))
    row.walks = to_int(stat.get("baseOnBalls"))
    row.raw = stat
    row.fetched_at = datetime.now(UTC)


def ingest_qualified_pitchers(session: Session, season: int) -> int:
    """Season lines for every qualified starter."""
    written = 0
    for split in api.qualified_pitchers(season):
        person = split.get("player") or {}
        if not person.get("id"):
            continue
        team = team_by_mlb_id(session, (split.get("team") or {}).get("id", 0))
        player = _upsert_player(session, person, team)
        if player is None:
            continue
        session.flush()
        _write_pitcher_season(session, player, split.get("stat", {}), season)
        written += 1

    session.commit()
    log.info("mlb qualified pitchers %d: %d", season, written)
    return written


def ingest_all_pitchers(session: Session, season: int, min_starts: int = 1) -> int:
    """Season lines for every pitcher, so each team's rotation can be ranked.

    One API call. Pitchers with no starts are skipped -- they cannot be part of a
    rotation ranking and would only bloat the table.
    """
    written = 0
    for split in api.all_pitchers(season):
        person = split.get("player") or {}
        stat = split.get("stat") or {}
        if not person.get("id"):
            continue
        if (to_int(stat.get("gamesStarted")) or 0) < min_starts:
            continue

        team = team_by_mlb_id(session, (split.get("team") or {}).get("id", 0))
        player = _upsert_player(session, person, team)
        if player is None:
            continue
        session.flush()
        _write_pitcher_season(session, player, stat, season)
        written += 1

    session.commit()
    log.info("mlb all pitchers %d: %d with at least %d start(s)", season, written, min_starts)
    return written


def ingest_probable_pitcher_details(session: Session, season: int, days_ahead: int = 3) -> int:
    """Season line + game log for pitchers announced as upcoming starters.

    Many probables fall short of the qualified-innings cutoff, so the leaderboard
    alone leaves the model without inputs for exactly the games we care about.
    """
    now = datetime.now(UTC)
    horizon = now.timestamp() + days_ahead * 86400
    upcoming = session.scalars(
        select(Game).where(
            Game.sport == Sport.MLB,
            Game.is_final.is_(False),
            Game.start_time >= now,
        )
    ).all()

    player_ids: set[int] = set()
    for game in upcoming:
        if game.start_time.timestamp() > horizon:
            continue
        for pid in (game.home_probable_pitcher_id, game.away_probable_pitcher_id):
            if pid:
                player_ids.add(pid)

    written = 0
    for pid in player_ids:
        player = session.get(Player, pid)
        if player is None or player.mlb_id is None:
            continue
        stat = api.pitcher_season_line(player.mlb_id, season)
        if stat:
            _write_pitcher_season(session, player, stat, season)
        written += _ingest_one_game_log(session, player, season)

    session.commit()
    log.info("mlb probable pitcher details %d: %d pitchers", season, len(player_ids))
    return written


def _ingest_one_game_log(session: Session, player: Player, season: int) -> int:
    if player.mlb_id is None:
        return 0
    written = 0
    for split in api.pitcher_game_log(player.mlb_id, season):
        stat = split.get("stat", {})
        log_date = split.get("date")
        if not log_date:
            continue
        parsed_date = date.fromisoformat(log_date)
        opp = team_by_mlb_id(session, (split.get("opponent") or {}).get("id", 0))
        opp_id = opp.id if opp else None

        row = session.scalar(
            select(PitcherGameLog).where(
                PitcherGameLog.player_id == player.id,
                PitcherGameLog.game_date == parsed_date,
                PitcherGameLog.opponent_id == opp_id,
            )
        )
        if row is None:
            row = PitcherGameLog(
                player_id=player.id, game_date=parsed_date, opponent_id=opp_id, season=season
            )
            session.add(row)
        row.innings_pitched = innings_to_float(stat.get("inningsPitched"))
        row.earned_runs = to_int(stat.get("earnedRuns"))
        row.strikeouts = to_int(stat.get("strikeOuts"))
        row.walks = to_int(stat.get("baseOnBalls"))
        row.hits = to_int(stat.get("hits"))
        row.home_runs = to_int(stat.get("homeRuns"))
        written += 1
    return written


def ingest_starter_game_logs(session: Session, season: int, min_starts: int = 3) -> int:
    """Game logs for every pitcher with a real number of starts.

    One call per pitcher, so this is a slow job -- but it is what makes as-of-date
    pitcher form possible, and therefore what lets a backtest avoid grading a May game
    with September statistics.
    """
    from app.models import PitcherSeasonStats

    players = session.execute(
        select(Player, PitcherSeasonStats)
        .join(PitcherSeasonStats, PitcherSeasonStats.player_id == Player.id)
        .where(
            PitcherSeasonStats.season == season,
            PitcherSeasonStats.games_started >= min_starts,
        )
    ).all()

    written = 0
    for player, _ in players:
        try:
            written += _ingest_one_game_log(session, player, season)
        except Exception as exc:  # noqa: BLE001 - one bad pitcher must not stop the job
            log.warning("game log for %s failed: %s", player.full_name, exc)
        session.commit()

    log.info("mlb starter game logs %d: %d rows across %d pitchers", season, written, len(players))
    return written


def ingest_recent_results(session: Session, season: int, days_back: int = 7) -> int:
    """Backfill recent completed games so team logs stay current."""
    from datetime import timedelta

    today = datetime.now(UTC).date()
    total = 0
    for offset in range(days_back, -1, -1):
        total += ingest_schedule(session, today - timedelta(days=offset), season)
    return total
