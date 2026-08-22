"""Teams, players, and games -- the entities every other table hangs off."""

from datetime import date, datetime

from sqlalchemy import (
    Boolean,
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
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import Sport, TimestampMixin


class Team(Base, TimestampMixin):
    """One row per team per sport, carrying the cross-source ID crosswalk.

    Three upstreams name teams three different ways: MLB uses numeric IDs, ESPN uses
    its own numeric IDs, and The Odds API uses full display strings. Every join in the
    app depends on this mapping, so it is seeded from data/team_map.yaml rather than
    inferred at runtime.
    """

    __tablename__ = "teams"
    __table_args__ = (
        UniqueConstraint("sport", "abbrev", name="uq_team_sport_abbrev"),
        Index("ix_team_odds_name", "odds_api_name"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sport: Mapped[Sport] = mapped_column(Enum(Sport, native_enum=False), nullable=False)

    mlb_id: Mapped[int | None] = mapped_column(Integer, unique=True)
    espn_id: Mapped[int | None] = mapped_column(Integer)
    odds_api_name: Mapped[str] = mapped_column(String(64), nullable=False)

    abbrev: Mapped[str] = mapped_column(String(8), nullable=False)
    display_name: Mapped[str] = mapped_column(String(64), nullable=False)
    location: Mapped[str | None] = mapped_column(String(64))
    conference: Mapped[str | None] = mapped_column(String(32))
    division: Mapped[str | None] = mapped_column(String(32))
    logo_url: Mapped[str | None] = mapped_column(String(255))

    def __repr__(self) -> str:
        return f"<Team {self.sport.value}:{self.abbrev}>"


class Player(Base, TimestampMixin):
    """Currently MLB pitchers only -- basketball is team-level in v1."""

    __tablename__ = "players"

    id: Mapped[int] = mapped_column(primary_key=True)
    sport: Mapped[Sport] = mapped_column(Enum(Sport, native_enum=False), nullable=False)
    mlb_id: Mapped[int | None] = mapped_column(Integer, unique=True, index=True)
    full_name: Mapped[str] = mapped_column(String(128), nullable=False)
    team_id: Mapped[int | None] = mapped_column(ForeignKey("teams.id", ondelete="SET NULL"))
    primary_position: Mapped[str | None] = mapped_column(String(16))
    throws: Mapped[str | None] = mapped_column(String(4))

    team: Mapped["Team | None"] = relationship()

    def __repr__(self) -> str:
        return f"<Player {self.full_name}>"


class Game(Base, TimestampMixin):
    """A scheduled or completed game. Final scores drive the over/under history."""

    __tablename__ = "games"
    __table_args__ = (
        UniqueConstraint("sport", "external_id", name="uq_game_sport_external"),
        Index("ix_game_sport_start", "sport", "start_time"),
        Index("ix_game_season", "sport", "season"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    sport: Mapped[Sport] = mapped_column(Enum(Sport, native_enum=False), nullable=False)

    # MLB gamePk, or ESPN event id.
    external_id: Mapped[str] = mapped_column(String(32), nullable=False)
    # The Odds API's own event id, needed for per-event markets. Cached from the free
    # /events endpoint so a props poll never spends a credit discovering it.
    odds_event_id: Mapped[str | None] = mapped_column(String(64), index=True)
    season: Mapped[int] = mapped_column(Integer, nullable=False)
    game_date: Mapped[date] = mapped_column(Date, nullable=False)
    start_time: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    home_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    away_team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)

    status: Mapped[str] = mapped_column(String(32), nullable=False, default="scheduled")
    is_final: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    home_score: Mapped[int | None] = mapped_column(Integer)
    away_score: Mapped[int | None] = mapped_column(Integer)

    # MLB only: the announced starters, which drive the run projection.
    home_probable_pitcher_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL")
    )
    away_probable_pitcher_id: Mapped[int | None] = mapped_column(
        ForeignKey("players.id", ondelete="SET NULL")
    )

    home_team: Mapped["Team"] = relationship(foreign_keys=[home_team_id])
    away_team: Mapped["Team"] = relationship(foreign_keys=[away_team_id])
    home_probable_pitcher: Mapped["Player | None"] = relationship(
        foreign_keys=[home_probable_pitcher_id]
    )
    away_probable_pitcher: Mapped["Player | None"] = relationship(
        foreign_keys=[away_probable_pitcher_id]
    )

    @property
    def total_score(self) -> int | None:
        if self.home_score is None or self.away_score is None:
            return None
        return self.home_score + self.away_score

    def __repr__(self) -> str:
        return f"<Game {self.sport.value} {self.external_id} {self.game_date}>"


class GameInnings(Base):
    """Runs by inning for a completed game.

    Exists so the first-5-innings market can be modelled against real F5 outcomes
    instead of an assumed fraction of the full-game total. MLB's schedule endpoint
    returns this under `hydrate=linescore`.
    """

    __tablename__ = "game_innings"
    __table_args__ = (UniqueConstraint("game_id", "inning", name="uq_inning_game_number"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), nullable=False)
    inning: Mapped[int] = mapped_column(Integer, nullable=False)
    home_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    away_runs: Mapped[int] = mapped_column(Integer, nullable=False, default=0)


class GameTeamLog(Base):
    """One row per team per completed game -- the flattened game log.

    Built from final scores so we can compute points/runs allowed and defensive
    ratings without a second upstream. ESPN does not expose opponent stats directly,
    and deriving them here keeps the NBA model free of stats.nba.com.
    """

    __tablename__ = "game_team_logs"
    __table_args__ = (UniqueConstraint("game_id", "team_id", name="uq_gamelog_game_team"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), nullable=False)
    team_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False, index=True)
    opponent_id: Mapped[int] = mapped_column(ForeignKey("teams.id"), nullable=False)
    season: Mapped[int] = mapped_column(Integer, nullable=False, index=True)
    game_date: Mapped[date] = mapped_column(Date, nullable=False)

    is_home: Mapped[bool] = mapped_column(Boolean, nullable=False)
    points_for: Mapped[int] = mapped_column(Integer, nullable=False)
    points_against: Mapped[int] = mapped_column(Integer, nullable=False)
    won: Mapped[bool] = mapped_column(Boolean, nullable=False)
    # Estimated possessions for the game; NBA only, null for MLB.
    possessions: Mapped[float | None] = mapped_column(Float)
