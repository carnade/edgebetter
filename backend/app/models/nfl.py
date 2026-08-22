"""NFL tables.

Structured around the question the tool actually answers: "under these conditions, what
has historically happened to this market?" So `nfl_team_games` stores the *outcome* of
each market alongside the conditions that applied, rather than making every query
recompute them from raw scores and lines.
"""

from __future__ import annotations

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
    Date,
    DateTime,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.orm import Mapped, mapped_column

from app.db import Base
from app.models.base import TimestampMixin


class NflGame(Base, TimestampMixin):
    """One row per game, mirroring nflverse `games.csv`.

    Carries both the closing line and the result, which is what makes historical prop
    outcomes computable without any odds provider.
    """

    __tablename__ = "nfl_games"
    __table_args__ = (
        Index("ix_nflgame_season_week", "season", "week"),
        Index("ix_nflgame_gameday", "gameday"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[str] = mapped_column(String(24), unique=True, nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    game_type: Mapped[str] = mapped_column(String(8), nullable=False)  # REG, WC, DIV, CON, SB
    week: Mapped[int] = mapped_column(Integer, nullable=False)
    gameday: Mapped[date] = mapped_column(Date, nullable=False)
    weekday: Mapped[str | None] = mapped_column(String(12))
    gametime: Mapped[str | None] = mapped_column(String(8))

    home_team: Mapped[str] = mapped_column(String(4), nullable=False, index=True)
    away_team: Mapped[str] = mapped_column(String(4), nullable=False, index=True)
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)
    total_points: Mapped[int | None] = mapped_column(Integer)
    # nflverse `result` is home_score - away_score.
    result: Mapped[int | None] = mapped_column(Integer)
    overtime: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)

    # --- closing lines ---
    spread_line: Mapped[float | None] = mapped_column(Float)   # positive favours home
    total_line: Mapped[float | None] = mapped_column(Float)
    home_moneyline: Mapped[int | None] = mapped_column(Integer)
    away_moneyline: Mapped[int | None] = mapped_column(Integer)
    over_odds: Mapped[int | None] = mapped_column(Integer)
    under_odds: Mapped[int | None] = mapped_column(Integer)

    # --- conditions ---
    roof: Mapped[str | None] = mapped_column(String(12), index=True)      # outdoors/dome/closed/open
    surface: Mapped[str | None] = mapped_column(String(16))
    temp: Mapped[float | None] = mapped_column(Float)   # null indoors, not missing
    wind: Mapped[float | None] = mapped_column(Float)
    home_rest: Mapped[int | None] = mapped_column(Integer)
    away_rest: Mapped[int | None] = mapped_column(Integer)
    div_game: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    stadium: Mapped[str | None] = mapped_column(String(64))
    referee: Mapped[str | None] = mapped_column(String(64))
    home_qb_name: Mapped[str | None] = mapped_column(String(64))
    away_qb_name: Mapped[str | None] = mapped_column(String(64))

    # --- live market, refreshed during the season ---
    # Deliberately a consensus snapshot rather than per-book rows: the NFL side of this
    # tool answers "what does history say about this matchup", so the live number only
    # needs to sit next to the base rate, not be shopped across books.
    live_total_line: Mapped[float | None] = mapped_column(Float)
    live_spread_line: Mapped[float | None] = mapped_column(Float)
    live_home_moneyline: Mapped[int | None] = mapped_column(Integer)
    live_away_moneyline: Mapped[int | None] = mapped_column(Integer)
    live_book_count: Mapped[int | None] = mapped_column(Integer)
    odds_fetched_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))

    # --- derived from play-by-play ---
    home_first_half: Mapped[int | None] = mapped_column(Integer)
    away_first_half: Mapped[int | None] = mapped_column(Integer)
    home_first_quarter: Mapped[int | None] = mapped_column(Integer)
    away_first_quarter: Mapped[int | None] = mapped_column(Integer)

    @property
    def is_final(self) -> bool:
        return self.home_score is not None and self.away_score is not None

    @property
    def outdoor(self) -> bool:
        return self.roof == "outdoors"


class NflTeamGame(Base, TimestampMixin):
    """One row per team per game: the conditions that team faced and how each market resolved.

    Storing resolved outcomes rather than recomputing them keeps the splits engine simple
    and makes it impossible for two queries to disagree about what "covered" means.
    """

    __tablename__ = "nfl_team_games"
    __table_args__ = (
        UniqueConstraint("game_id", "team", name="uq_nflteamgame_game_team"),
        Index("ix_nflteamgame_team_season", "team", "season"),
        Index("ix_nflteamgame_season_week", "season", "week"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_pk: Mapped[int] = mapped_column(
        ForeignKey("nfl_games.id", ondelete="CASCADE"), nullable=False
    )
    game_id: Mapped[str] = mapped_column(String(24), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    week: Mapped[int] = mapped_column(Integer, nullable=False)
    gameday: Mapped[date] = mapped_column(Date, nullable=False)

    team: Mapped[str] = mapped_column(String(4), nullable=False)
    opponent: Mapped[str] = mapped_column(String(4), nullable=False, index=True)
    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False)

    points_for: Mapped[int | None] = mapped_column(Integer)
    points_against: Mapped[int | None] = mapped_column(Integer)
    first_half_points: Mapped[int | None] = mapped_column(Integer)
    first_quarter_points: Mapped[int | None] = mapped_column(Integer)

    # --- market outcomes, resolved once ---
    # Spread from this team's perspective: negative when favoured.
    team_spread: Mapped[float | None] = mapped_column(Float)
    covered: Mapped[bool | None] = mapped_column(Boolean)
    push_spread: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    won: Mapped[bool | None] = mapped_column(Boolean)
    total_line: Mapped[float | None] = mapped_column(Float)
    went_over: Mapped[bool | None] = mapped_column(Boolean)
    push_total: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    is_favourite: Mapped[bool | None] = mapped_column(Boolean)

    # --- conditions, denormalised so splits need no join ---
    rest: Mapped[int | None] = mapped_column(Integer)
    rest_advantage: Mapped[int | None] = mapped_column(Integer)
    roof: Mapped[str | None] = mapped_column(String(12))
    surface: Mapped[str | None] = mapped_column(String(16))
    temp: Mapped[float | None] = mapped_column(Float)
    wind: Mapped[float | None] = mapped_column(Float)
    div_game: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    qb_name: Mapped[str | None] = mapped_column(String(64))
    referee: Mapped[str | None] = mapped_column(String(64))

    # --- efficiency, from play-by-play ---
    off_epa_per_play: Mapped[float | None] = mapped_column(Float)
    def_epa_per_play: Mapped[float | None] = mapped_column(Float)
    off_success_rate: Mapped[float | None] = mapped_column(Float)
    plays: Mapped[int | None] = mapped_column(Integer)
    pass_rate: Mapped[float | None] = mapped_column(Float)


class NflInjury(Base, TimestampMixin):
    """Weekly injury report rows."""

    __tablename__ = "nfl_injuries"
    __table_args__ = (
        Index("ix_nflinjury_season_week_team", "season", "week", "team"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    week: Mapped[int] = mapped_column(Integer, nullable=False)
    team: Mapped[str] = mapped_column(String(4), nullable=False)
    player_name: Mapped[str | None] = mapped_column(String(128))
    gsis_id: Mapped[str | None] = mapped_column(String(24), index=True)
    position: Mapped[str | None] = mapped_column(String(8))
    report_status: Mapped[str | None] = mapped_column(String(64))  # Out, Doubtful, Questionable
    practice_status: Mapped[str | None] = mapped_column(String(256))
    injury: Mapped[str | None] = mapped_column(String(256))


class NflLineHistory(Base):
    """Every line observation for a game, so movement can be measured.

    The single-snapshot columns on `nfl_games` answer "what is the line now". This table
    answers the more useful question: which way did it move, and were we on the right
    side of that move before it happened.

    Closing line value -- did the market drift toward the number you took -- is the
    standard way to tell whether you hold an edge without waiting on results or risking
    money. It is impossible to compute from a snapshot, which is why observations are
    appended rather than overwritten.
    """

    __tablename__ = "nfl_line_history"
    __table_args__ = (
        UniqueConstraint("game_id", "fetched_at", name="uq_nfllinehist_game_time"),
        Index("ix_nfllinehist_game", "game_id", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[str] = mapped_column(String(24), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    week: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    # Hours until kickoff at the moment of observation; negative once a game has started.
    hours_to_kickoff: Mapped[float | None] = mapped_column(Float)

    total_line: Mapped[float | None] = mapped_column(Float)
    spread_line: Mapped[float | None] = mapped_column(Float)
    home_moneyline: Mapped[int | None] = mapped_column(Integer)
    away_moneyline: Mapped[int | None] = mapped_column(Integer)
    book_count: Mapped[int | None] = mapped_column(Integer)

    # What our model said at the time, stored alongside so the comparison is never
    # recomputed with hindsight ratings.
    model_total: Mapped[float | None] = mapped_column(Float)
    model_margin: Mapped[float | None] = mapped_column(Float)
    # Where the observation came from: "nflverse" for the seeded opener, "odds_api" for
    # a live poll.
    source: Mapped[str] = mapped_column(String(16), nullable=False, default="odds_api")


class NflPlayerGame(Base):
    """One row per player per game: what they did, and the context they did it in.

    Built for projecting props rather than for browsing box scores, so volume and usage
    sit alongside the game conditions. Usage is stored because it is the stable part --
    snap share and target share carry far more week-to-week signal than yards, which are
    volume times a noisy efficiency.
    """

    __tablename__ = "nfl_player_games"
    __table_args__ = (
        UniqueConstraint("player_id", "game_id", name="uq_nflplayergame_player_game"),
        Index("ix_nflplayergame_player_season", "player_id", "season", "week"),
        Index("ix_nflplayergame_pos_season", "position", "season"),
        Index("ix_nflplayergame_opponent", "opponent", "season"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[str] = mapped_column(String(24), nullable=False)
    player_name: Mapped[str] = mapped_column(String(96), nullable=False)
    position: Mapped[str | None] = mapped_column(String(8))
    team: Mapped[str] = mapped_column(String(4), nullable=False)
    opponent: Mapped[str] = mapped_column(String(4), nullable=False)

    game_id: Mapped[str] = mapped_column(String(24), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    week: Mapped[int] = mapped_column(Integer, nullable=False)
    is_home: Mapped[bool | None] = mapped_column(Boolean)

    # --- the three markets we project ---
    passing_yards: Mapped[float | None] = mapped_column(Float)
    rushing_yards: Mapped[float | None] = mapped_column(Float)
    receiving_yards: Mapped[float | None] = mapped_column(Float)

    # --- volume, the predictable half ---
    attempts: Mapped[int | None] = mapped_column(Integer)
    completions: Mapped[int | None] = mapped_column(Integer)
    carries: Mapped[int | None] = mapped_column(Integer)
    targets: Mapped[int | None] = mapped_column(Integer)
    receptions: Mapped[int | None] = mapped_column(Integer)

    # --- usage share ---
    target_share: Mapped[float | None] = mapped_column(Float)
    air_yards_share: Mapped[float | None] = mapped_column(Float)
    offense_snaps: Mapped[int | None] = mapped_column(Integer)
    snap_pct: Mapped[float | None] = mapped_column(Float)

    # --- context, denormalised so projections need no join ---
    roof: Mapped[str | None] = mapped_column(String(12))
    temp: Mapped[float | None] = mapped_column(Float)
    wind: Mapped[float | None] = mapped_column(Float)
    total_line: Mapped[float | None] = mapped_column(Float)
    team_spread: Mapped[float | None] = mapped_column(Float)


class NflPropLine(Base):
    """A player prop line as offered by a book.

    Kept separate from `odds_snapshots`, which keys to the MLB/NBA `games` table. Rows
    are appended rather than updated so prop line movement is measurable the same way
    game lines are.
    """

    __tablename__ = "nfl_prop_lines"
    __table_args__ = (
        Index("ix_nflpropline_game_market", "game_id", "market"),
        Index("ix_nflpropline_player", "player_name", "season", "week"),
        UniqueConstraint(
            "game_id", "market", "player_name", "bookmaker", "outcome", "fetched_at",
            name="uq_nflpropline_obs",
        ),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[str] = mapped_column(String(24), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    week: Mapped[int] = mapped_column(Integer, nullable=False)

    market: Mapped[str] = mapped_column(String(32), nullable=False)
    player_name: Mapped[str] = mapped_column(String(96), nullable=False)
    # Resolved against our player table where possible; books spell names their own way.
    player_id: Mapped[str | None] = mapped_column(String(24), index=True)
    bookmaker: Mapped[str] = mapped_column(String(48), nullable=False)
    outcome: Mapped[str] = mapped_column(String(16), nullable=False)  # Over / Under
    point: Mapped[float] = mapped_column(Float, nullable=False)
    price_american: Mapped[int] = mapped_column(Integer, nullable=False)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
