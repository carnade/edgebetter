"""Response models for the REST API."""

from __future__ import annotations

from datetime import datetime

from pydantic import BaseModel


class TeamOut(BaseModel):
    id: int
    abbrev: str
    name: str
    location: str | None = None
    conference: str | None = None
    division: str | None = None


class PitcherOut(BaseModel):
    id: int
    name: str
    era: float | None = None
    whip: float | None = None
    k_per_9: float | None = None
    bb_per_9: float | None = None
    wins: int | None = None
    losses: int | None = None
    innings_pitched: float | None = None
    games_started: int | None = None
    innings_per_start: float | None = None
    recent_form: list[float] = []


class ProjectionOut(BaseModel):
    """Model estimate, not a prediction. The UI must label it as such."""

    home_score: float
    away_score: float
    total: float
    margin: float
    prob_home_win: float
    # True when a rating leaned on the prior season because the sample was small.
    blended: bool = False
    possessions: float | None = None


class PriceOut(BaseModel):
    bookmaker: str
    market: str
    outcome: str
    american: int
    decimal: float
    point: float | None = None
    fetched_at: datetime


class EdgeOut(BaseModel):
    id: int
    game_id: int
    sport: str
    market: str
    selection: str
    point: float | None = None
    best_book: str
    best_price_american: int
    best_price_decimal: float
    fair_prob: float
    book_count: int
    ev: float
    kelly_quarter: float
    model_prob: float | None = None
    model_ev: float | None = None
    model_line: float | None = None
    signals_agree: bool | None = None
    matchup: str | None = None
    start_time: datetime | None = None


class GameOut(BaseModel):
    id: int
    sport: str
    external_id: str
    start_time: datetime
    status: str
    is_final: bool
    home: TeamOut
    away: TeamOut
    home_score: int | None = None
    away_score: int | None = None
    home_pitcher: PitcherOut | None = None
    away_pitcher: PitcherOut | None = None
    projection: ProjectionOut | None = None
    best_total: float | None = None
    best_total_book: str | None = None
    top_edge_ev: float | None = None
    edge_count: int = 0


class GameDetailOut(GameOut):
    prices: list[PriceOut] = []
    edges: list[EdgeOut] = []
    line_history: list[PriceOut] = []


class TeamStatsOut(BaseModel):
    team: TeamOut
    season: int
    games_played: int | None = None
    # NBA
    points_for: float | None = None
    points_against: float | None = None
    off_rating: float | None = None
    def_rating: float | None = None
    net_rating: float | None = None
    pace: float | None = None
    # MLB
    runs_for: float | None = None
    runs_against: float | None = None
    team_era: float | None = None
    team_whip: float | None = None
    team_ops: float | None = None
    source: str | None = None


class RotationSlotOut(BaseModel):
    player_id: int
    name: str
    rank: int
    rotation_size: int
    era: float | None = None
    regressed_era: float
    whip: float | None = None
    k_per_9: float | None = None
    games_started: int
    is_top_two: bool
    is_bottom_two: bool


class GateCheckOut(BaseModel):
    key: str
    label: str
    passed: bool
    detail: str


class MismatchOut(BaseModel):
    """A lopsided pitching matchup.

    `score` measures how one-sided the matchup is. `verdict` measures whether the price
    is any good. They are separate on purpose -- a game can be very likely to be won by
    the favourite and still be a bad bet.

    `grade` combines the four checks into one word, but `checks` is always sent with it
    so the UI can show why. A verdict without its reasoning invites autopilot betting.
    """

    game_id: int
    start_time: datetime
    matchup: str
    favourite: str
    underdog: str
    favourite_is_home: bool

    score: float
    strict: bool
    team_gap: float
    era_gap: float
    favourite_team_rank: int
    underdog_team_rank: int
    favourite_team_tier: str
    underdog_team_tier: str
    favourite_pythagorean: float
    underdog_pythagorean: float

    favourite_pitcher: RotationSlotOut | None = None
    underdog_pitcher: RotationSlotOut | None = None

    model_win_prob: float | None = None
    market_fair_prob: float | None = None
    best_american: int | None = None
    best_book: str | None = None
    risk_to_win_one: float | None = None
    ev: float | None = None
    model_ev: float | None = None
    kelly_quarter: float | None = None
    book_count: int = 0
    verdict: str
    model_disagrees: bool = False

    # Combined judgement plus the evidence behind it.
    grade: str                       # bet | near miss | pass | unpriced
    checks: list[GateCheckOut] = []
    passed_checks: int = 0
    blocking_reason: str | None = None
    break_even_prob: float | None = None
    band_label: str | None = None
    band_win_rate: float | None = None
    band_break_even: int | None = None
    band_sample: int | None = None


class MismatchBandOut(BaseModel):
    label: str
    wins: int
    games: int
    win_rate: float
    # The price at which this win rate exactly breaks even. Compare with what books
    # actually offer: that comparison is the whole question.
    break_even_american: int | None = None


class MismatchEvidenceOut(BaseModel):
    """How the mismatch score has actually performed this season."""

    bands: list[MismatchBandOut]
    strict_wins: int
    strict_games: int
    strict_win_rate: float | None = None
    strict_break_even_american: int | None = None
    baseline_home_win_rate: float
    caveat: str


class MarketRowOut(BaseModel):
    game_id: int
    matchup: str
    start_time: datetime
    market: str
    market_label: str
    subject: str | None = None
    selection: str
    point: float | None = None
    best_book: str
    best_american: int
    fair_prob: float
    break_even_prob: float
    book_count: int
    ev: float
    kelly_quarter: float
    outliers: list[str] = []
    # Projection shown for context only. The F5 and strikeout models both failed their
    # walk-forward gate, so this must never be presented as a reason to bet.
    model_value: float | None = None
    model_unvalidated: bool = False


class BudgetOut(BaseModel):
    remaining: int
    days_left: int
    daily_allowance: int
    reserve: int
    game_level_cost_today: int
    props_allowance: int
    props_markets_per_game: int
    props_games_today: int
    reason: str


class OddsStatusOut(BaseModel):
    """Surfaced in the UI so a blank odds panel is never a mystery."""

    enabled: bool
    reason: str
    credits_remaining: int | None = None
    credits_used: int | None = None
    last_poll: datetime | None = None
    last_poll_ok: bool | None = None


class SportStatusOut(BaseModel):
    sport: str
    season: int
    season_display: str
    season_started: bool
    prior_season: int
    upcoming_games: int
    teams_with_stats: int


class HealthOut(BaseModel):
    status: str
    database: bool
    odds_configured: bool
    nba_enrich_enabled: bool
