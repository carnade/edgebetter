"""Background job runner (the `worker` container).

Cadences are chosen around one hard constraint: The Odds API's free tier is 500
credits a month. Stats upstreams are free and polled generously; odds are polled
exactly `ODDS_POLLS_PER_DAY` times per sport and guarded on top of that.

At the default 3 polls/day: MLB costs 2 credits a call and NBA 3, so both sports
active is 15/day (~465 in a 31-day month). Only MLB is in season for now, which is
about 186/month.
"""

from __future__ import annotations

import logging
import signal
from datetime import UTC, datetime, timedelta

from apscheduler.schedulers.blocking import BlockingScheduler
from apscheduler.triggers.cron import CronTrigger

from app.config import get_settings
from app.db import SessionLocal
from app.models import IngestRun, Sport

log = logging.getLogger("scheduler")


def _run(job_name: str, fn) -> None:
    """Run a job, recording the outcome and never letting an exception kill the loop."""
    started = datetime.now(UTC)
    rows, ok, detail = 0, True, None
    try:
        with SessionLocal() as session:
            rows = fn(session) or 0
    except Exception as exc:  # noqa: BLE001 - a failed job must not stop the scheduler
        ok, detail = False, f"{type(exc).__name__}: {exc}"[:500]
        log.exception("job %s failed", job_name)
    finally:
        try:
            with SessionLocal() as session:
                session.add(
                    IngestRun(
                        job=job_name,
                        ok=ok,
                        rows_written=rows,
                        detail=detail,
                        started_at=started,
                        finished_at=datetime.now(UTC),
                    )
                )
                session.commit()
        except Exception:  # noqa: BLE001
            log.warning("could not record run for %s", job_name)
    if ok:
        log.info("job %s ok (%d rows)", job_name, rows)


# ------------------------------------------------------------------ job bodies
def job_mlb_schedule(session) -> int:
    from app.services.ingest_mlb import ingest_schedule
    from app.services.season_resolver import mlb_season

    info = mlb_season()
    today = datetime.now(UTC).date()
    total = 0
    # Yesterday catches late finals; today and tomorrow keep probables current.
    for offset in (-1, 0, 1):
        total += ingest_schedule(session, today + timedelta(days=offset), info.season)
    return total


def job_mlb_stats(session) -> int:
    from app.services.ingest_mlb import (
        ingest_probable_pitcher_details,
        ingest_qualified_pitchers,
        ingest_team_stats,
    )
    from app.services.season_resolver import mlb_season

    season = mlb_season().season
    return (
        ingest_team_stats(session, season)
        + ingest_qualified_pitchers(session, season)
        + ingest_probable_pitcher_details(session, season)
    )


def job_nba_schedule(session) -> int:
    from app.services.ingest_nba import ingest_scoreboard
    from app.services.season_resolver import nba_season

    info = nba_season()
    if not info.started:
        log.info("nba season %s has not started; nothing to poll", info.display)
        return 0
    today = datetime.now(UTC).date()
    return sum(
        ingest_scoreboard(session, today + timedelta(days=offset), info.season)
        for offset in (-1, 0, 1)
    )


def job_nba_stats(session) -> int:
    from app.services.ingest_nba import ingest_team_stats
    from app.services.season_resolver import nba_season

    info = nba_season()
    season = info.season if info.started else info.prior_season
    return ingest_team_stats(session, season)


def job_nba_enrich(session) -> int:
    """Optional stats.nba.com upgrade. Returns 0 when unavailable, which is fine."""
    from app.services.enrich_nba import enrich_team_ratings
    from app.services.season_resolver import nba_season

    info = nba_season()
    season = info.season if info.started else info.prior_season
    return enrich_team_ratings(session, season)


def job_odds(sport: Sport):
    def run(session) -> int:
        from app.services.ingest_odds import poll_odds

        result = poll_odds(session, sport)
        return result.snapshots_written

    return run


def job_edges(sport: Sport):
    def run(session) -> int:
        from app.services.edges import recompute_edges

        return recompute_edges(session, sport)

    return run


def job_event_map(session) -> int:
    """Attach Odds API event ids. Free -- the /events endpoint costs no quota."""
    from app.services.event_map import refresh_event_ids

    return refresh_event_ids(session, Sport.MLB)


def job_props(session) -> int:
    """Per-event markets for the games the allocator can afford.

    Cost scales with the number of games, unlike game-level odds, so this job asks
    services/credit_budget.py how many it may poll rather than sweeping the slate.
    """
    from app.services.ingest_props import poll_props

    return poll_props(session, Sport.MLB).snapshots_written


def job_nfl_refresh(session) -> int:
    """Refresh the NFL schedule from nflverse. Free -- no key, no credits."""
    from app.services.ingest_nfl import ingest_games

    return ingest_games(session).games


def job_nfl_odds(session) -> int:
    """Consensus NFL lines, appended to the movement history.

    Seeds openers first so a game we have never seen gets a baseline before its first
    live observation; otherwise the first poll would look like the opening line and any
    earlier movement would be invisible.
    """
    from datetime import datetime as _dt

    from app.services.ingest_nfl_odds import poll_nfl_odds, seed_openers

    seed_openers(session, _dt.now(UTC).year)
    return poll_nfl_odds(session, lookahead_days=14).games_updated


def job_nfl_props(session) -> int:
    """Weekly player prop lines, polled a whole week at a time.

    Five markets across ~16 games is 80 credits, so this runs once a week. It targets the
    next unplayed week explicitly rather than a rolling date window, because no window
    anchored to a single weekday covers a Wednesday-to-Monday slate without either
    dropping a game or spilling into the following week.
    """
    from app.services.ingest_nfl_props import next_unplayed_week, poll_nfl_props

    target = next_unplayed_week(session)
    if target is None:
        log.info("nfl props: no unplayed games, nothing to poll")
        return 0
    season, week = target
    result = poll_nfl_props(session, season=season, week=week, dry_run=False)
    if result.truncated:
        log.warning("nfl props: %s", result.summary())
    return result.lines_written


def job_seed(session) -> int:
    from app.services.team_map import seed_teams

    return seed_teams(session)


# ---------------------------------------------------------------------- wiring
def build_scheduler() -> BlockingScheduler:
    settings = get_settings()
    sched = BlockingScheduler(timezone="UTC")

    # Odds polls are spread across the day rather than bunched, so a slate is priced
    # both well before and close to first pitch.
    poll_hours = {1: [18], 2: [15, 22], 3: [13, 18, 23], 4: [12, 16, 20, 23]}.get(
        settings.odds_polls_per_day, [13, 18, 23]
    )

    sched.add_job(lambda: _run("seed", job_seed), CronTrigger(hour=4, minute=0), id="seed")

    sched.add_job(
        lambda: _run("mlb_schedule", job_mlb_schedule),
        CronTrigger(minute="*/30"),
        id="mlb_schedule",
    )
    sched.add_job(
        lambda: _run("mlb_stats", job_mlb_stats), CronTrigger(hour=9, minute=10), id="mlb_stats"
    )

    sched.add_job(
        lambda: _run("nba_schedule", job_nba_schedule),
        CronTrigger(minute="*/30"),
        id="nba_schedule",
    )
    sched.add_job(
        lambda: _run("nba_stats", job_nba_stats), CronTrigger(hour=9, minute=25), id="nba_stats"
    )

    if settings.enable_nba_stats_enrich:
        # Once a night, single shot. Hammering this upstream is what gets it throttled.
        sched.add_job(
            lambda: _run("nba_enrich", job_nba_enrich),
            CronTrigger(hour=9, minute=40),
            id="nba_enrich",
        )

    # Player props once a week, on Tuesday. 80 credits a week at five markets.
    #
    # Tuesday because an NFL week runs Thursday to Monday, so it is the one day that sits
    # cleanly between slates: the previous week has finished and the coming one has not
    # started, which is what lets the job poll a whole week in a single pass. A Thursday
    # poll would arrive after that week's Thursday-night game -- and after the Wednesday
    # opener in week 1 -- and those games would never get prop lines at all, with nothing
    # to show they were missing.
    sched.add_job(
        lambda: _run("nfl_props", job_nfl_props),
        CronTrigger(day_of_week="tue", hour=16, minute=0),
        id="nfl_props",
    )

    # nflverse refreshes results and lines through the week; free, so run it daily.
    sched.add_job(
        lambda: _run("nfl_refresh", job_nfl_refresh),
        CronTrigger(hour=8, minute=20),
        id="nfl_refresh",
    )
    # NFL plays weekly, so three polls a week is ample: Tuesday (openers), Friday, and
    # Sunday morning before the early kickoffs.
    # Daily rather than three times a week: measuring line movement needs observations
    # spread through the week, and at 3 credits a poll a daily cadence is ~90/month --
    # affordable because NFL runs one slate a week rather than one a day.
    sched.add_job(
        lambda: _run("nfl_odds", job_nfl_odds),
        CronTrigger(hour="15", minute=10),
        id="nfl_odds",
    )

    # Free, so run it often enough that props always have an id to work with.
    sched.add_job(
        lambda: _run("event_map", job_event_map),
        CronTrigger(minute="15,45"),
        id="event_map",
    )

    # Once a day. Per-event markets are the expensive ones and the allocator decides
    # how many games are affordable; polling them more often would simply cost more
    # for prices that move little before first pitch.
    sched.add_job(
        lambda: _run("props_mlb", job_props),
        CronTrigger(hour=14, minute=40),
        id="props_mlb",
    )

    for sport in (Sport.MLB, Sport.NBA):
        sched.add_job(
            lambda s=sport: _run(f"odds_{s.value}", job_odds(s)),
            CronTrigger(hour=",".join(str(h) for h in poll_hours), minute=5 if sport is Sport.MLB else 20),
            id=f"odds_{sport.value}",
        )
        # Recompute shortly after each poll so edges reflect the newest prices.
        sched.add_job(
            lambda s=sport: _run(f"edges_{s.value}", job_edges(s)),
            CronTrigger(hour=",".join(str(h) for h in poll_hours), minute=35 if sport is Sport.MLB else 50),
            id=f"edges_{sport.value}",
        )

    return sched


def main() -> None:
    settings = get_settings()
    logging.basicConfig(
        level=settings.log_level.upper(),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("apscheduler").setLevel(logging.WARNING)

    if not settings.odds_enabled:
        log.warning("THE_ODDS_API_KEY not set: odds jobs will no-op, stats jobs run normally")

    sched = build_scheduler()
    for sig in (signal.SIGINT, signal.SIGTERM):
        signal.signal(sig, lambda *_: sched.shutdown(wait=False))

    log.info("scheduler starting with %d jobs", len(sched.get_jobs()))
    for job in sched.get_jobs():
        log.info("  %-16s %s", job.id, job.trigger)

    # Seed immediately so a fresh database is usable without waiting for 04:00.
    _run("seed", job_seed)
    sched.start()


if __name__ == "__main__":
    main()
