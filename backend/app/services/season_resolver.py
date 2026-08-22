"""Resolve the active season per sport at job time.

Reading this from the upstreams rather than hardcoding is what lets the NBA roll from
2025-26 to 2026-27 on Sept 30 without a code change, while the finished season stays
in the database as an early-season prior.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime

from app.models import Sport
from app.providers import espn
from app.providers import mlb_statsapi as mlb
from app.providers.http import ProviderError

log = logging.getLogger(__name__)


@dataclass(frozen=True)
class SeasonInfo:
    sport: Sport
    season: int
    display: str
    started: bool
    prior_season: int

    @property
    def seasons_to_ingest(self) -> list[int]:
        """Current season first; the prior season stays available as a model prior."""
        if self.started:
            return [self.season, self.prior_season]
        return [self.prior_season]


def nba_season(now: datetime | None = None) -> SeasonInfo:
    now = now or datetime.now(UTC)
    try:
        current = espn.current_season("nba")
        year = int(current.get("year") or 0)
        display = str(current.get("displayName") or "")
        start_raw = current.get("startDate")
        started = True
        if start_raw:
            start = datetime.fromisoformat(str(start_raw).replace("Z", "+00:00"))
            started = now >= start
    except (ProviderError, ValueError) as exc:
        # Fall back to the calendar convention: ESPN labels a season by its end year.
        log.warning("espn currentSeason lookup failed (%s); falling back to calendar", exc)
        year = now.year + 1 if now.month >= 10 else now.year
        display = f"{year - 1}-{str(year)[2:]}"
        started = now.month >= 10

    if not year:
        year = now.year + 1 if now.month >= 10 else now.year

    return SeasonInfo(
        sport=Sport.NBA,
        season=year,
        display=display or f"{year - 1}-{str(year)[2:]}",
        started=started,
        prior_season=year - 1,
    )


def mlb_season(now: datetime | None = None) -> SeasonInfo:
    now = now or datetime.now(UTC)
    try:
        year = mlb.current_season()
    except (ProviderError, ValueError) as exc:
        log.warning("mlb currentSeason lookup failed (%s); falling back to calendar year", exc)
        year = now.year
    return SeasonInfo(
        sport=Sport.MLB, season=year, display=str(year), started=True, prior_season=year - 1
    )


def resolve(sport: Sport, now: datetime | None = None) -> SeasonInfo:
    return nba_season(now) if sport is Sport.NBA else mlb_season(now)
