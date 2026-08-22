"""Optional nightly enrichment of NBA ratings from stats.nba.com.

Failure is an expected outcome here, not an exception path. When the upstream
throttles, this writes nothing and the ESPN-derived ratings continue to serve. The
one rule it must never break: never null out or overwrite good ESPN data on failure.
"""

from __future__ import annotations

import logging
from datetime import UTC, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import IngestRun, Sport, StatSource, Team, TeamSeasonStats
from app.providers import nba_stats
from app.providers.http import ProviderError

log = logging.getLogger(__name__)


def enrich_team_ratings(session: Session, season: int) -> int:
    """Fetch the Advanced table and upsert it as a separate, clearly-labelled source.

    Returns the number of rows written; 0 means the upstream was unavailable, which is
    a normal and non-fatal result.
    """
    settings = get_settings()
    started = datetime.now(UTC)

    if not settings.enable_nba_stats_enrich:
        log.info("nba_stats enrichment disabled (ENABLE_NBA_STATS_ENRICH=false); skipping")
        return 0

    try:
        rows = nba_stats.league_dash_team_stats(season, "Advanced")
    except (ProviderError, ValueError) as exc:
        # Expected often enough that it is logged as info, not error. ESPN data stands.
        log.info("nba_stats enrichment unavailable for %d (%s); ESPN ratings stand", season, exc)
        _record(session, started, ok=False, rows=0, detail=str(exc)[:500])
        return 0

    by_nba_id = {int(r["TEAM_ID"]): r for r in rows if r.get("TEAM_ID")}
    if not by_nba_id:
        log.info("nba_stats returned an empty table for %d; ESPN ratings stand", season)
        _record(session, started, ok=False, rows=0, detail="empty table")
        return 0

    written = 0
    now = datetime.now(UTC)
    teams = session.scalars(select(Team).where(Team.sport == Sport.NBA)).all()
    by_abbrev = {t.abbrev: t for t in teams}

    for payload in by_nba_id.values():
        team = _match_team(payload, by_abbrev, teams)
        if team is None:
            log.debug("nba_stats team %r not matched to crosswalk", payload.get("TEAM_NAME"))
            continue

        off = payload.get("OFF_RATING")
        dfn = payload.get("DEF_RATING")
        pace = payload.get("PACE")
        # Partial rows are worse than none: the accessor requires all three.
        if off is None or dfn is None or pace is None:
            continue

        row = session.scalar(
            select(TeamSeasonStats).where(
                TeamSeasonStats.team_id == team.id,
                TeamSeasonStats.season == season,
                TeamSeasonStats.source == StatSource.NBA_STATS,
            )
        )
        if row is None:
            row = TeamSeasonStats(
                team_id=team.id, sport=Sport.NBA, season=season, source=StatSource.NBA_STATS
            )
            session.add(row)

        row.games_played = _as_int(payload.get("GP"))
        row.wins = _as_int(payload.get("W"))
        row.losses = _as_int(payload.get("L"))
        row.off_rating = float(off)
        row.def_rating = float(dfn)
        row.pace = float(pace)
        row.raw = {k: v for k, v in payload.items() if isinstance(v, int | float | str)}
        row.fetched_at = now
        written += 1

    session.commit()
    log.info("nba_stats enrichment %d: %d teams upgraded", season, written)
    _record(session, started, ok=True, rows=written, detail=None)
    return written


def _match_team(payload: dict, by_abbrev: dict[str, Team], teams: list[Team]) -> Team | None:
    """stats.nba.com uses its own team ids, so match on the full display name."""
    name = (payload.get("TEAM_NAME") or "").strip().lower()
    if not name:
        return None
    for team in teams:
        if team.display_name.strip().lower() == name:
            return team
    # LA Clippers vs Los Angeles Clippers and similar.
    for team in teams:
        if name.endswith(team.display_name.split()[-1].lower()) and team.abbrev in by_abbrev:
            return team
    return None


def _as_int(value: object) -> int | None:
    try:
        return int(value)  # type: ignore[arg-type]
    except (TypeError, ValueError):
        return None


def _record(session: Session, started: datetime, *, ok: bool, rows: int, detail: str | None) -> None:
    session.add(
        IngestRun(
            job="enrich_nba_stats",
            ok=ok,
            rows_written=rows,
            detail=detail,
            started_at=started,
            finished_at=datetime.now(UTC),
        )
    )
    session.commit()
