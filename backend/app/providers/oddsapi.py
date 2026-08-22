"""The Odds API v4 client.

The free tier is 500 credits/month and an /odds call costs markets x regions, so this
client reports the quota headers on every response and the caller is responsible for
staying inside budget. See services/ingest_odds.py for the guard.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any

import httpx

from app.providers.http import DEFAULT_TIMEOUT, ProviderError

log = logging.getLogger(__name__)

BASE = "https://api.the-odds-api.com/v4"

SPORT_KEYS = {"mlb": "baseball_mlb", "nba": "basketball_nba"}


@dataclass(frozen=True)
class OddsResponse:
    """Payload plus the quota accounting headers that come with every reply."""

    data: Any
    requests_last: int | None
    requests_used: int | None
    requests_remaining: int | None
    status_code: int
    fetched_at: datetime


def _int_header(resp: httpx.Response, name: str) -> int | None:
    raw = resp.headers.get(name)
    try:
        return int(raw) if raw is not None else None
    except ValueError:
        return None


def _request(path: str, params: dict[str, Any]) -> OddsResponse:
    url = f"{BASE}{path}"
    try:
        resp = httpx.get(url, params=params, timeout=DEFAULT_TIMEOUT)
    except httpx.HTTPError as exc:
        raise ProviderError(f"odds api request failed: {exc}") from exc

    if resp.status_code == 401:
        raise ProviderError("odds api rejected the key (401) -- check THE_ODDS_API_KEY")
    if resp.status_code == 429:
        raise ProviderError("odds api quota exhausted (429)")
    if resp.status_code >= 400:
        raise ProviderError(f"odds api returned {resp.status_code}: {resp.text[:200]}")

    return OddsResponse(
        data=resp.json(),
        requests_last=_int_header(resp, "x-requests-last"),
        requests_used=_int_header(resp, "x-requests-used"),
        requests_remaining=_int_header(resp, "x-requests-remaining"),
        status_code=resp.status_code,
        fetched_at=datetime.now(UTC),
    )


def odds(
    api_key: str,
    sport_key: str,
    *,
    regions: str = "us",
    markets: list[str] | None = None,
    odds_format: str = "american",
) -> OddsResponse:
    """Upcoming games with bookmaker prices. Costs len(markets) x len(regions) credits."""
    return _request(
        f"/sports/{sport_key}/odds",
        {
            "apiKey": api_key,
            "regions": regions,
            "markets": ",".join(markets or ["h2h", "totals"]),
            "oddsFormat": odds_format,
        },
    )


def events(api_key: str, sport_key: str) -> OddsResponse:
    """Scheduled events with their ids. Free -- costs no credits.

    This is what makes selective per-event polling affordable: we can discover every
    event id for nothing, then spend credits only on the games worth pricing.
    """
    return _request(f"/sports/{sport_key}/events", {"apiKey": api_key})


def event_odds(
    api_key: str,
    sport_key: str,
    event_id: str,
    *,
    regions: str = "us",
    markets: list[str] | None = None,
    odds_format: str = "american",
) -> OddsResponse:
    """Odds for ONE event, including markets unavailable on the bulk endpoint.

    Cost is [unique markets returned] x [regions] for this single event. Sweeping a
    15-game slate for one market therefore costs 15 credits, which is why callers must
    go through services/credit_budget.py rather than looping freely.
    """
    return _request(
        f"/sports/{sport_key}/events/{event_id}/odds",
        {
            "apiKey": api_key,
            "regions": regions,
            "markets": ",".join(markets or []),
            "oddsFormat": odds_format,
        },
    )


def sports(api_key: str) -> OddsResponse:
    """Available sports. Free -- costs no credits."""
    return _request("/sports", {"apiKey": api_key})
