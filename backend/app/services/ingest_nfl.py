"""Ingest NFL data from nflverse and resolve market outcomes.

Market outcomes (covered, went over, pushed) are resolved once here rather than in every
query. That keeps the splits engine honest: there is exactly one definition of "covered"
in the codebase, so two different views cannot quietly disagree.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NflGame, NflInjury, NflTeamGame
from app.providers import nflverse as nv

log = logging.getLogger(__name__)

DEFAULT_START_SEASON = 2020


def normalise_surface(value: str | None) -> str | None:
    """Collapse nflverse surface values to a clean set.

    The raw data contains both 'grass' and 'grass ' (93 rows with a trailing space),
    which would silently become two separate categories in any group-by and split every
    grass-field sample in half.
    """
    text = nv.to_str(value)
    if not text:
        return None
    return text.strip().lower().replace(" ", "_")


def normalise_roof(value: str | None) -> str | None:
    text = nv.to_str(value)
    return text.strip().lower() if text else None


@dataclass
class IngestSummary:
    games: int = 0
    team_games: int = 0
    skipped: int = 0

    def __str__(self) -> str:
        return f"{self.games} games, {self.team_games} team-games, {self.skipped} skipped"


def ingest_games(
    session: Session, *, start_season: int = DEFAULT_START_SEASON, regular_season_only: bool = True
) -> IngestSummary:
    """Load games and derive one row per team per game."""
    summary = IngestSummary()

    for row in nv.read_games():
        season = nv.to_int(row.get("season"))
        if season is None or season < start_season:
            continue
        game_type = nv.to_str(row.get("game_type")) or "REG"
        if regular_season_only and game_type != "REG":
            continue

        game_id = nv.to_str(row.get("game_id"))
        gameday_raw = nv.to_str(row.get("gameday"))
        if not game_id or not gameday_raw:
            summary.skipped += 1
            continue
        try:
            gameday = date.fromisoformat(gameday_raw)
        except ValueError:
            summary.skipped += 1
            continue

        game = session.scalar(select(NflGame).where(NflGame.game_id == game_id))
        if game is None:
            game = NflGame(game_id=game_id)
            session.add(game)

        game.season = season
        game.game_type = game_type
        game.week = nv.to_int(row.get("week")) or 0
        game.gameday = gameday
        game.weekday = nv.to_str(row.get("weekday"))
        game.gametime = nv.to_str(row.get("gametime"))
        game.home_team = nv.to_str(row.get("home_team")) or ""
        game.away_team = nv.to_str(row.get("away_team")) or ""
        game.home_score = nv.to_int(row.get("home_score"))
        game.away_score = nv.to_int(row.get("away_score"))
        game.total_points = nv.to_int(row.get("total"))
        game.result = nv.to_int(row.get("result"))
        game.overtime = nv.to_bool(row.get("overtime"))

        game.spread_line = nv.to_float(row.get("spread_line"))
        game.total_line = nv.to_float(row.get("total_line"))
        game.home_moneyline = nv.to_int(row.get("home_moneyline"))
        game.away_moneyline = nv.to_int(row.get("away_moneyline"))
        game.over_odds = nv.to_int(row.get("over_odds"))
        game.under_odds = nv.to_int(row.get("under_odds"))

        game.roof = normalise_roof(row.get("roof"))
        game.surface = normalise_surface(row.get("surface"))
        game.temp = nv.to_float(row.get("temp"))
        game.wind = nv.to_float(row.get("wind"))
        game.home_rest = nv.to_int(row.get("home_rest"))
        game.away_rest = nv.to_int(row.get("away_rest"))
        game.div_game = nv.to_bool(row.get("div_game"))
        game.stadium = nv.to_str(row.get("stadium"))
        game.referee = nv.to_str(row.get("referee"))
        game.home_qb_name = nv.to_str(row.get("home_qb_name"))
        game.away_qb_name = nv.to_str(row.get("away_qb_name"))

        session.flush()
        summary.team_games += _write_team_rows(session, game)
        summary.games += 1

    session.commit()
    log.info("nfl games: %s", summary)
    return summary


def _write_team_rows(session: Session, game: NflGame) -> int:
    """One row per side, with every market outcome resolved.

    `spread_line` in nflverse is stated from the home team's perspective and is positive
    when the home side is favoured, so the away team's handicap is its negation.
    """
    written = 0
    for team, opponent, is_home in (
        (game.home_team, game.away_team, True),
        (game.away_team, game.home_team, False),
    ):
        row = session.scalar(
            select(NflTeamGame).where(
                NflTeamGame.game_id == game.game_id, NflTeamGame.team == team
            )
        )
        if row is None:
            row = NflTeamGame(game_id=game.game_id, team=team)
            session.add(row)

        points_for = game.home_score if is_home else game.away_score
        points_against = game.away_score if is_home else game.home_score

        row.game_pk = game.id
        row.season = game.season
        row.week = game.week
        row.gameday = game.gameday
        row.opponent = opponent
        row.is_home = is_home
        row.points_for = points_for
        row.points_against = points_against

        # Handicap from this team's perspective: negative when favoured.
        spread = game.spread_line
        team_spread = None
        if spread is not None:
            team_spread = -spread if is_home else spread
        row.team_spread = team_spread

        row.covered = None
        row.push_spread = False
        row.won = None
        if points_for is not None and points_against is not None:
            margin = points_for - points_against
            row.won = margin > 0
            if team_spread is not None:
                adjusted = margin + team_spread
                if abs(adjusted) < 1e-9:
                    row.push_spread = True
                    row.covered = None
                else:
                    row.covered = adjusted > 0

        row.total_line = game.total_line
        row.went_over = None
        row.push_total = False
        if game.total_points is not None and game.total_line is not None:
            if abs(game.total_points - game.total_line) < 1e-9:
                row.push_total = True
            else:
                row.went_over = game.total_points > game.total_line

        row.is_favourite = None if team_spread is None else team_spread < 0

        row.rest = game.home_rest if is_home else game.away_rest
        other_rest = game.away_rest if is_home else game.home_rest
        row.rest_advantage = (
            None if row.rest is None or other_rest is None else row.rest - other_rest
        )
        row.roof = game.roof
        row.surface = game.surface
        row.temp = game.temp
        row.wind = game.wind
        row.div_game = game.div_game
        row.qb_name = game.home_qb_name if is_home else game.away_qb_name
        row.referee = game.referee
        written += 1

    return written


def ingest_injuries(session: Session, season: int) -> int:
    """Weekly injury reports for one season."""
    try:
        rows = nv.read_injuries(season)
    except nv.NflverseError as exc:
        log.warning("nfl injuries %d unavailable: %s", season, exc)
        return 0

    written = 0
    for row in rows:
        week = nv.to_int(row.get("week"))
        team = nv.to_str(row.get("team"))
        gsis = nv.to_str(row.get("gsis_id"))
        if week is None or not team:
            continue

        existing = session.scalar(
            select(NflInjury).where(
                NflInjury.season == season,
                NflInjury.week == week,
                NflInjury.team == team,
                NflInjury.gsis_id == gsis,
            )
        )
        injury = existing or NflInjury(season=season, week=week, team=team, gsis_id=gsis)
        if existing is None:
            session.add(injury)

        injury.player_name = nv.to_str(row.get("full_name")) or nv.to_str(row.get("player_name"))
        injury.position = nv.to_str(row.get("position"))
        injury.report_status = nv.to_str(row.get("report_status"))
        injury.practice_status = nv.to_str(row.get("practice_status"))
        injury.injury = nv.to_str(row.get("report_primary_injury")) or nv.to_str(
            row.get("primary_injury")
        )
        written += 1

    session.commit()
    log.info("nfl injuries %d: %d rows", season, written)
    return written


def sanity_check(session: Session, season: int) -> list[str]:
    """Structural checks. Returns human-readable problems, empty when clean.

    Run after ingest: a silent mis-mapping here would corrupt every base rate, and the
    symptom would look like an interesting finding rather than a bug.
    """
    problems: list[str] = []

    games = session.scalars(
        select(NflGame).where(NflGame.season == season, NflGame.game_type == "REG")
    ).all()
    if not games:
        return [f"no regular-season games ingested for {season}"]

    finished = [g for g in games if g.is_final]

    # Every team plays 17 regular-season games in a completed season -- with one real
    # exception. The Week 17 Bills-Bengals game of 2022 (2023-01-02) was abandoned after
    # Damar Hamlin's cardiac arrest and never replayed, so both clubs finished on 16.
    # The data is correct; only the expectation needs the caveat.
    KNOWN_SHORT_SEASONS = {(2022, "BUF"), (2022, "CIN")}

    counts: dict[str, int] = {}
    for g in finished:
        counts[g.home_team] = counts.get(g.home_team, 0) + 1
        counts[g.away_team] = counts.get(g.away_team, 0) + 1
    if finished and len(finished) >= 250:  # only meaningful once a season is complete
        odd = {
            t: c
            for t, c in counts.items()
            if c != 17 and not ((season, t) in KNOWN_SHORT_SEASONS and c == 16)
        }
        if odd:
            problems.append(f"{season}: teams without 17 games: {odd}")
        if len(counts) != 32:
            problems.append(f"{season}: {len(counts)} teams seen, expected 32")

    # Scores must reconcile with the stored total.
    for g in finished:
        if g.total_points is not None and g.home_score + g.away_score != g.total_points:
            problems.append(f"{g.game_id}: scores do not sum to total_points")
            break

    # Derived partial scores can never exceed the final score.
    bad_half = session.scalars(
        select(NflTeamGame).where(
            NflTeamGame.season == season,
            NflTeamGame.first_half_points.is_not(None),
            NflTeamGame.points_for.is_not(None),
        )
    ).all()
    for row in bad_half:
        if row.first_half_points > row.points_for:
            problems.append(
                f"{row.game_id}/{row.team}: first-half points exceed final score"
            )
            break

    return problems


# ------------------------------------------------------------------ play-by-play
def ingest_pbp_season(session: Session, season: int) -> int:
    """Derive per-team efficiency and partial-game scoring from play-by-play.

    Two things come out of this pass:

    - **EPA per play**, offensive and defensive. Expected points added is used rather
      than raw yardage because it weights plays by how much they actually change scoring
      expectation -- a 3-yard gain on 3rd-and-2 and on 3rd-and-8 are not the same event.
    - **First-half and first-quarter scores**, which `games.csv` does not carry, taken
      from the running score at the moment each period ends.
    """
    from collections import defaultdict

    # game_id -> team -> [epa, plays, successes, passes]
    off: dict[str, dict[str, list[float]]] = defaultdict(
        lambda: defaultdict(lambda: [0.0, 0.0, 0.0, 0.0])
    )
    # game_id -> [q1_home, q1_away, h1_home, h1_away]
    periods: dict[str, list[int]] = defaultdict(lambda: [0, 0, 0, 0])

    rows = 0
    for play in nv.iter_pbp(season):
        rows += 1
        game_id = nv.to_str(play.get("game_id"))
        if not game_id:
            continue

        # The running score rides on every play, so the last play of a period carries
        # the score at the end of that period.
        qtr = nv.to_int(play.get("qtr"))
        home_running = nv.to_int(play.get("total_home_score"))
        away_running = nv.to_int(play.get("total_away_score"))
        if qtr is not None and home_running is not None and away_running is not None:
            bucket = periods[game_id]
            if qtr <= 1:
                bucket[0], bucket[1] = home_running, away_running
            if qtr <= 2:
                bucket[2], bucket[3] = home_running, away_running

        posteam = nv.to_str(play.get("posteam"))
        epa = nv.to_float(play.get("epa"))
        play_type = nv.to_str(play.get("play_type"))
        # Only scrimmage plays carry meaningful EPA; kneels, spikes, and special teams
        # would distort a per-play efficiency rate.
        if not posteam or epa is None or play_type not in {"pass", "run"}:
            continue

        acc = off[game_id][posteam]
        acc[0] += epa
        acc[1] += 1
        acc[2] += 1.0 if nv.to_float(play.get("success")) else 0.0
        acc[3] += 1.0 if play_type == "pass" else 0.0

    updated = 0
    team_rows = session.scalars(select(NflTeamGame).where(NflTeamGame.season == season)).all()
    by_game: dict[str, list[NflTeamGame]] = defaultdict(list)
    for row in team_rows:
        by_game[row.game_id].append(row)

    for game_id, sides in by_game.items():
        stats = off.get(game_id)
        period = periods.get(game_id)

        for row in sides:
            if stats:
                own = stats.get(row.team)
                opp = stats.get(row.opponent)
                if own and own[1] > 0:
                    row.off_epa_per_play = own[0] / own[1]
                    row.off_success_rate = own[2] / own[1]
                    row.plays = int(own[1])
                    row.pass_rate = own[3] / own[1]
                # Defensive EPA is what the opponent's offence managed against us.
                if opp and opp[1] > 0:
                    row.def_epa_per_play = opp[0] / opp[1]

            if period:
                q1_home, q1_away, h1_home, h1_away = period
                row.first_quarter_points = q1_home if row.is_home else q1_away
                row.first_half_points = h1_home if row.is_home else h1_away
            updated += 1

        game = session.scalar(select(NflGame).where(NflGame.game_id == game_id))
        if game is not None and period:
            game.home_first_quarter, game.away_first_quarter = period[0], period[1]
            game.home_first_half, game.away_first_half = period[2], period[3]

    session.commit()
    log.info("nfl pbp %d: %d plays read, %d team-games updated", season, rows, updated)
    return updated


# ------------------------------------------------------------------- players
def ingest_player_weeks(session: Session, season: int) -> int:
    """Per-player weekly rows, joined to game context and snap participation.

    Snap counts key on player *name* rather than id, so they are matched on
    (name, team, week). Names collide rarely enough within a single team-week for this
    to be safe, and a miss leaves snap_pct null rather than attaching the wrong player's
    usage -- which would be worse than having none.
    """
    from app.models import NflPlayerGame

    try:
        rows = nv.read_player_weeks(season)
    except nv.NflverseError as exc:
        log.warning("nfl player weeks %d unavailable: %s", season, exc)
        return 0

    snaps: dict[tuple[str, str, int], dict] = {}
    try:
        for snap in nv.read_snap_counts(season):
            name = nv.to_str(snap.get("player"))
            team = nv.to_str(snap.get("team"))
            week = nv.to_int(snap.get("week"))
            if name and team and week is not None:
                snaps[(name.lower(), team, week)] = snap
    except nv.NflverseError as exc:
        log.warning("nfl snap counts %d unavailable: %s", season, exc)

    # Game context, keyed both ways so either side of a matchup resolves.
    games = session.scalars(
        select(NflGame).where(NflGame.season == season, NflGame.game_type == "REG")
    ).all()
    by_team_week: dict[tuple[str, int], NflGame] = {}
    for g in games:
        by_team_week[(g.home_team, g.week)] = g
        by_team_week[(g.away_team, g.week)] = g

    written = 0
    for row in rows:
        player_id = nv.to_str(row.get("player_id"))
        team = nv.to_str(row.get("team"))
        week = nv.to_int(row.get("week"))
        if not player_id or not team or week is None:
            continue

        game = by_team_week.get((team, week))
        if game is None:
            continue

        existing = session.scalar(
            select(NflPlayerGame).where(
                NflPlayerGame.player_id == player_id,
                NflPlayerGame.game_id == game.game_id,
            )
        )
        entry = existing or NflPlayerGame(player_id=player_id, game_id=game.game_id)
        if existing is None:
            session.add(entry)

        is_home = game.home_team == team
        entry.player_name = nv.to_str(row.get("player_display_name")) or ""
        entry.position = nv.to_str(row.get("position"))
        entry.team = team
        entry.opponent = game.away_team if is_home else game.home_team
        entry.season = season
        entry.week = week
        entry.is_home = is_home

        entry.passing_yards = nv.to_float(row.get("passing_yards"))
        entry.rushing_yards = nv.to_float(row.get("rushing_yards"))
        entry.receiving_yards = nv.to_float(row.get("receiving_yards"))
        entry.attempts = nv.to_int(row.get("attempts"))
        entry.completions = nv.to_int(row.get("completions"))
        entry.carries = nv.to_int(row.get("carries"))
        entry.targets = nv.to_int(row.get("targets"))
        entry.receptions = nv.to_int(row.get("receptions"))
        entry.target_share = nv.to_float(row.get("target_share"))
        entry.air_yards_share = nv.to_float(row.get("air_yards_share"))

        snap = snaps.get(((entry.player_name or "").lower(), team, week))
        if snap:
            entry.offense_snaps = nv.to_int(snap.get("offense_snaps"))
            entry.snap_pct = nv.to_float(snap.get("offense_pct"))

        entry.roof = game.roof
        entry.temp = game.temp
        entry.wind = game.wind
        entry.total_line = game.total_line
        if game.spread_line is not None:
            entry.team_spread = -game.spread_line if is_home else game.spread_line

        written += 1

    session.commit()
    log.info("nfl player weeks %d: %d rows", season, written)
    return written
