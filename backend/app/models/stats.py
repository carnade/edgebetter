"""Season-level and game-level statistics."""

from datetime import date, datetime

from sqlalchemy import (
    Date,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import Sport, StatSource, TimestampMixin


class TeamSeasonStats(Base, TimestampMixin):
    """Team season aggregates, keyed by source so enrichment never clobbers the backbone.

    The same team/season can hold both an ESPN row and a stats.nba.com row. The ratings
    accessor in services/ratings.py picks between them; nothing else should read a
    specific source directly.
    """

    __tablename__ = "team_season_stats"
    __table_args__ = (
        UniqueConstraint("team_id", "season", "source", name="uq_teamstats_team_season_source"),
        Index("ix_teamstats_season_source", "season", "source"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id", ondelete="CASCADE"), nullable=False)
    sport: Mapped[Sport] = mapped_column(Enum(Sport, native_enum=False), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    source: Mapped[StatSource] = mapped_column(Enum(StatSource, native_enum=False), nullable=False)

    games_played: Mapped[int | None] = mapped_column(Integer)
    wins: Mapped[int | None] = mapped_column(Integer)
    losses: Mapped[int | None] = mapped_column(Integer)

    # --- NBA ---
    points_for: Mapped[float | None] = mapped_column(Float)
    points_against: Mapped[float | None] = mapped_column(Float)
    possessions: Mapped[float | None] = mapped_column(Float)
    pace: Mapped[float | None] = mapped_column(Float)
    off_rating: Mapped[float | None] = mapped_column(Float)
    def_rating: Mapped[float | None] = mapped_column(Float)

    # --- MLB ---
    runs_for: Mapped[float | None] = mapped_column(Float)
    runs_against: Mapped[float | None] = mapped_column(Float)
    team_era: Mapped[float | None] = mapped_column(Float)
    team_whip: Mapped[float | None] = mapped_column(Float)
    team_ops: Mapped[float | None] = mapped_column(Float)
    strikeout_rate: Mapped[float | None] = mapped_column(Float)
    walk_rate: Mapped[float | None] = mapped_column(Float)

    # Everything the upstream returned, for stats we have not promoted to columns yet.
    raw: Mapped[dict | None] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    team: Mapped["object"] = relationship("Team")


class PitcherSeasonStats(Base, TimestampMixin):
    """Season pitching line for a starter -- the core MLB model input."""

    __tablename__ = "pitcher_season_stats"
    __table_args__ = (
        UniqueConstraint("player_id", "season", name="uq_pitcherstats_player_season"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    season: Mapped[int] = mapped_column(Integer, nullable=False)

    games_started: Mapped[int | None] = mapped_column(Integer)
    innings_pitched: Mapped[float | None] = mapped_column(Float)
    era: Mapped[float | None] = mapped_column(Float)
    whip: Mapped[float | None] = mapped_column(Float)
    k_per_9: Mapped[float | None] = mapped_column(Float)
    bb_per_9: Mapped[float | None] = mapped_column(Float)
    hr_per_9: Mapped[float | None] = mapped_column(Float)
    wins: Mapped[int | None] = mapped_column(Integer)
    losses: Mapped[int | None] = mapped_column(Integer)
    earned_runs: Mapped[int | None] = mapped_column(Integer)
    strikeouts: Mapped[int | None] = mapped_column(Integer)
    walks: Mapped[int | None] = mapped_column(Integer)

    raw: Mapped[dict | None] = mapped_column(JSONB)
    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class PitcherGameLog(Base):
    """Per-start line. Drives recent form and expected innings per start."""

    __tablename__ = "pitcher_game_logs"
    __table_args__ = (
        UniqueConstraint("player_id", "game_date", "opponent_id", name="uq_pitcherlog_unique"),
        Index("ix_pitcherlog_player_date", "player_id", "game_date"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    player_id: Mapped[int] = mapped_column(
        ForeignKey("players.id", ondelete="CASCADE"), nullable=False
    )
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    game_date: Mapped[date] = mapped_column(Date, nullable=False)
    opponent_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id"))

    innings_pitched: Mapped[float | None] = mapped_column(Float)
    earned_runs: Mapped[int | None] = mapped_column(Integer)
    strikeouts: Mapped[int | None] = mapped_column(Integer)
    walks: Mapped[int | None] = mapped_column(Integer)
    hits: Mapped[int | None] = mapped_column(Integer)
    home_runs: Mapped[int | None] = mapped_column(Integer)
    decision: Mapped[str | None] = mapped_column(String(4))
