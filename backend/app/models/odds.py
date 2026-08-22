"""Odds snapshots, computed edges, and API credit accounting."""

from datetime import datetime

from sqlalchemy import (
    Boolean,
    DateTime,
    Enum,
    Float,
    ForeignKey,
    Index,
    Integer,
    String,
    Text,
)
from sqlalchemy.orm import Mapped, mapped_column, relationship

from app.db import Base
from app.models.base import Sport, TimestampMixin


class OddsSnapshot(Base):
    """One immutable row per bookmaker outcome per poll.

    Never updated in place: accumulating snapshots is how we build line-movement
    history, since historical odds cost 10x on The Odds API and are unaffordable
    on the free tier.
    """

    __tablename__ = "odds_snapshots"
    __table_args__ = (
        Index("ix_odds_game_market_fetched", "game_id", "market", "fetched_at"),
        Index("ix_odds_fetched", "fetched_at"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), nullable=False)
    bookmaker: Mapped[str] = mapped_column(String(48), nullable=False)
    # Wide enough for per-event market keys: "totals_1st_5_innings" is 20 chars and
    # "pitcher_earned_runs" 19, versus 3-7 for the game-level keys.
    market: Mapped[str] = mapped_column(String(48), nullable=False)

    # For h2h: team name. For totals: "Over"/"Under". For spreads: team name.
    outcome: Mapped[str] = mapped_column(String(64), nullable=False)
    # Player props name a person; the market key becomes (market, player, point).
    player_name: Mapped[str | None] = mapped_column(String(96))
    price_american: Mapped[int] = mapped_column(Integer, nullable=False)
    # Total line or spread handicap; null for h2h.
    point: Mapped[float | None] = mapped_column(Float)

    fetched_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    game: Mapped["object"] = relationship("Game")


class Edge(Base, TimestampMixin):
    """A computed betting edge, carrying both signals side by side.

    fair_prob comes from devigged market consensus and needs no model to be correct.
    model_prob comes from the projection layer and is unvalidated until it has a
    track record -- the UI must label it as an estimate, not a prediction.
    """

    __tablename__ = "edges"
    __table_args__ = (
        Index("ix_edge_sport_ev", "sport", "ev"),
        Index("ix_edge_game", "game_id"),
    )

    id: Mapped[int] = mapped_column(primary_key=True)
    game_id: Mapped[int] = mapped_column(ForeignKey("games.id", ondelete="CASCADE"), nullable=False)
    sport: Mapped[Sport] = mapped_column(Enum(Sport, native_enum=False), nullable=False)

    market: Mapped[str] = mapped_column(String(48), nullable=False)
    selection: Mapped[str] = mapped_column(String(96), nullable=False)
    point: Mapped[float | None] = mapped_column(Float)

    best_book: Mapped[str] = mapped_column(String(48), nullable=False)
    best_price_american: Mapped[int] = mapped_column(Integer, nullable=False)
    best_price_decimal: Mapped[float] = mapped_column(Float, nullable=False)

    # Layer 1: devigged consensus across books.
    fair_prob: Mapped[float] = mapped_column(Float, nullable=False)
    book_count: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    ev: Mapped[float] = mapped_column(Float, nullable=False)
    kelly_full: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)
    kelly_quarter: Mapped[float] = mapped_column(Float, nullable=False, default=0.0)

    # Layer 2: independent projection. Null when the model lacks inputs.
    model_prob: Mapped[float | None] = mapped_column(Float)
    model_ev: Mapped[float | None] = mapped_column(Float)
    model_line: Mapped[float | None] = mapped_column(Float)
    # True when both layers point the same way -- a much stronger tell than either alone.
    signals_agree: Mapped[bool | None] = mapped_column(Boolean)

    computed_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)

    game: Mapped["object"] = relationship("Game")


class ApiUsage(Base):
    """Credit accounting for The Odds API, read back by the credit guard.

    Free tier is 500 credits/month and a call costs markets x regions, so this table
    is what keeps the scheduler inside budget.
    """

    __tablename__ = "api_usage"
    __table_args__ = (Index("ix_apiusage_called_at", "called_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    provider: Mapped[str] = mapped_column(String(32), nullable=False, default="the_odds_api")
    endpoint: Mapped[str] = mapped_column(String(128), nullable=False)
    sport_key: Mapped[str | None] = mapped_column(String(32))

    requests_last: Mapped[int | None] = mapped_column(Integer)
    requests_used: Mapped[int | None] = mapped_column(Integer)
    requests_remaining: Mapped[int | None] = mapped_column(Integer)

    status_code: Mapped[int | None] = mapped_column(Integer)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    note: Mapped[str | None] = mapped_column(Text)
    called_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)


class IngestRun(Base):
    """Audit trail for ingest jobs, including the deliberately-tolerated failures.

    The stats.nba.com enricher is expected to fail often; recording that here keeps
    those failures visible without letting them look like data problems.
    """

    __tablename__ = "ingest_runs"
    __table_args__ = (Index("ix_ingestrun_job_started", "job", "started_at"),)

    id: Mapped[int] = mapped_column(primary_key=True)
    job: Mapped[str] = mapped_column(String(64), nullable=False)
    ok: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    rows_written: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    detail: Mapped[str | None] = mapped_column(Text)
    started_at: Mapped[datetime] = mapped_column(DateTime(timezone=True), nullable=False)
    finished_at: Mapped[datetime | None] = mapped_column(DateTime(timezone=True))
