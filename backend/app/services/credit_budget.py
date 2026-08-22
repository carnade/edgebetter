"""Allocate a finite monthly credit quota across competing odds jobs.

The Odds API free tier is 500 credits a month. Game-level `/odds` covers a whole slate
for markets x regions, but every market in the props phase -- first-5-innings, team
totals, player props -- is per-event, so cost scales with the number of games polled.
One market across a 15-game slate is 15 credits.

Fixed cadences cannot survive that, and they especially cannot survive October, when the
MLB playoffs and the NBA season overlap and game-level polling alone would consume the
entire quota. So jobs ask this module what they can afford rather than assuming.

The allocator is advisory to the scheduler but *enforced* in the provider: a caller that
ignores it still cannot spend past the reserve floor.
"""

from __future__ import annotations

import calendar
import logging
from dataclasses import dataclass
from datetime import UTC, date, datetime

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.config import get_settings
from app.models import ApiUsage

log = logging.getLogger(__name__)

# The Odds API free tier. Used only as a fallback when the provider has not yet told us
# a real balance -- `x-requests-remaining` from any response always wins.
DEFAULT_MONTHLY_QUOTA = 500

# Priority order, funded top down. Game-level odds come first because they are cheap and
# cover every game; everything below competes for what is left.
#
# NFL props sit above MLB props deliberately, and the ordering is enforced rather than
# merely described. Player props are the only model in this project that beats its
# benchmark, and three of the four MLB per-event markets are in
# `markets.UNVALIDATED_MODELS` -- they failed their walk-forward gate. Without a
# reservation the daily MLB job would spend the allowance long before the weekly NFL poll
# runs on Thursday, and NFL would hit the reserve floor mid-slate.
PRIORITY_GAME_LEVEL = 0
PRIORITY_NFL_PROPS = 1
PRIORITY_MLB_PROPS = 2


@dataclass(frozen=True)
class Budget:
    """What we can afford today, and why."""

    remaining: int
    days_left: int
    daily_allowance: int
    reserve: int
    spent_today: int
    # After funding game-level polls for every active sport.
    game_level_cost_today: int
    # Held back for the weekly NFL prop poll before MLB props are funded. 0 out of season.
    nfl_props_reserved: int
    props_allowance: int
    props_markets_per_game: int
    props_games_today: int
    reason: str

    @property
    def exhausted(self) -> bool:
        return self.props_games_today <= 0


def days_left_in_period(today: date | None = None) -> int:
    """Days remaining in the billing month, including today.

    The Odds API resets monthly. Without an explicit reset date from the provider, the
    calendar month is the safe assumption: it can only make us more conservative near a
    month end, never less.
    """
    today = today or datetime.now(UTC).date()
    last_day = calendar.monthrange(today.year, today.month)[1]
    return max(1, last_day - today.day + 1)


def latest_remaining(session: Session) -> int | None:
    """Most recent balance the provider reported, or None on a fresh install."""
    return session.scalar(
        select(ApiUsage.requests_remaining)
        .where(ApiUsage.provider == "the_odds_api", ApiUsage.requests_remaining.is_not(None))
        .order_by(ApiUsage.called_at.desc())
        .limit(1)
    )


def spent_today(session: Session, today: date | None = None) -> int:
    """Credits already consumed today, from our own call log."""
    today = today or datetime.now(UTC).date()
    rows = session.scalars(
        select(ApiUsage.requests_last).where(
            ApiUsage.provider == "the_odds_api",
            ApiUsage.ok.is_(True),
            ApiUsage.requests_last.is_not(None),
        )
    ).all()
    # Filtering in Python keeps this portable across the date functions of any backend.
    stamps = session.scalars(
        select(ApiUsage.called_at).where(
            ApiUsage.provider == "the_odds_api",
            ApiUsage.ok.is_(True),
            ApiUsage.requests_last.is_not(None),
        )
    ).all()
    return sum(
        cost
        for cost, when in zip(rows, stamps, strict=True)
        if when and when.astimezone(UTC).date() == today
    )


def active_sports_game_level_cost(session: Session) -> int:
    """Daily cost of game-level polling for every sport that currently has games.

    A sport with no upcoming games costs nothing, which is what keeps the NBA offseason
    from consuming budget and what makes room for props during the summer.
    """
    from app.models import Sport
    from app.services.ingest_odds import SPORT_KEYS, _upcoming_game_count

    settings = get_settings()
    total = 0
    for sport in (Sport.MLB, Sport.NBA):
        if _upcoming_game_count(session, sport, settings.odds_lookahead_hours) == 0:
            continue
        total += settings.credit_cost(SPORT_KEYS[sport]) * settings.odds_polls_per_day
    return total


def nfl_props_reservation(session: Session) -> int:
    """Credits per day to hold back for the weekly NFL prop poll.

    Returns 0 out of season, when there are no upcoming games to poll, so this costs
    nothing during the summer -- the same principle that keeps the NBA offseason from
    consuming budget.

    The weekly cost is amortised across seven days rather than reserved in a lump, because
    the daily allowance is itself a daily figure. Holding back the full slate cost on one
    day would starve everything else that day and reserve nothing on the other six.
    """
    from datetime import timedelta

    from app.models import NflGame

    settings = get_settings()
    markets = len(settings.nfl_prop_markets_list)
    if markets == 0:
        return 0
    regions = max(1, len([r for r in settings.odds_regions.split(",") if r.strip()]))

    now = datetime.now(UTC)
    games = session.scalars(
        select(NflGame).where(
            NflGame.home_score.is_(None),
            NflGame.gameday >= now.date(),
            NflGame.gameday <= (now + timedelta(days=7)).date(),
        )
    ).all()
    if not games:
        return 0
    weekly = len(games) * markets * regions
    return -(-weekly // 7)  # ceil, so rounding never under-reserves


def compute(
    session: Session,
    *,
    props_markets: list[str] | None = None,
    today: date | None = None,
) -> Budget:
    """Work out how many games can afford per-event props polling today."""
    settings = get_settings()
    markets = props_markets if props_markets is not None else settings.props_markets_list
    regions = max(1, len([r for r in settings.odds_regions.split(",") if r.strip()]))
    per_game = max(1, len(markets)) * regions

    remaining = latest_remaining(session)
    if remaining is None:
        remaining = DEFAULT_MONTHLY_QUOTA
        source = "no balance reported yet, assuming free-tier quota"
    else:
        source = "provider-reported balance"

    days = days_left_in_period(today)
    reserve = settings.odds_credit_reserve
    usable = max(0, remaining - reserve)
    daily = usable // days

    game_level = active_sports_game_level_cost(session)
    already = spent_today(session, today)

    # Game-level polling is funded first, then NFL props are reserved, and MLB props get
    # whatever is left. That ordering is the whole point: MLB polls daily and NFL weekly,
    # so without the reservation MLB would always get there first.
    nfl_reserved = nfl_props_reservation(session)
    props_allowance = max(0, daily - max(game_level - already, 0) - nfl_reserved)
    games = props_allowance // per_game

    cap = settings.props_max_games_per_day
    if cap > 0:
        games = min(games, cap)

    if remaining <= reserve:
        reason = f"at reserve floor ({remaining} left, floor {reserve})"
    elif games <= 0:
        reason = (
            f"daily allowance {daily} is consumed by game-level polling ({game_level}/day); "
            f"props need {per_game} per game"
        )
    else:
        nfl_note = f", {nfl_reserved}/day held for NFL props" if nfl_reserved else ""
        reason = (
            f"{remaining} credits over {days} days = {daily}/day ({source}); "
            f"{game_level}/day to game-level odds{nfl_note} leaves {props_allowance} "
            f"for MLB props at {per_game} per game"
        )

    return Budget(
        remaining=remaining,
        days_left=days,
        daily_allowance=daily,
        reserve=reserve,
        spent_today=already,
        game_level_cost_today=game_level,
        nfl_props_reserved=nfl_reserved,
        props_allowance=props_allowance,
        props_markets_per_game=per_game,
        props_games_today=games,
        reason=reason,
    )


def can_spend(session: Session, cost: int) -> bool:
    """Hard gate, called in the provider so no job can bypass the reserve floor."""
    settings = get_settings()
    remaining = latest_remaining(session)
    if remaining is None:
        return True  # nothing spent yet; the first call establishes the balance
    return remaining - cost >= settings.odds_credit_reserve
