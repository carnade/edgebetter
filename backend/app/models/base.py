"""Shared enums and column helpers."""

import enum
from datetime import UTC, datetime

from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column


class Sport(str, enum.Enum):
    MLB = "mlb"
    NBA = "nba"
    NFL = "nfl"


class StatSource(str, enum.Enum):
    """Which upstream produced a stat row.

    ESPN is the backbone and always present. NBA_STATS is optional enrichment from
    stats.nba.com, which is bot-gated and throttles hard -- see providers/nba_stats.py.
    """

    MLB_STATSAPI = "mlb_statsapi"
    ESPN = "espn"
    NBA_STATS = "nba_stats"


def utcnow() -> datetime:
    return datetime.now(UTC)


class TimestampMixin:
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, nullable=False
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), default=utcnow, onupdate=utcnow, nullable=False
    )
