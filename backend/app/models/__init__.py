"""SQLAlchemy models. Import from here so Alembic sees every table."""

from app.models.base import Sport, StatSource, TimestampMixin, utcnow
from app.models.core import Game, GameInnings, GameTeamLog, Player, Team
from app.models.nfl import (
    NflGame,
    NflInjury,
    NflLineHistory,
    NflPlayerGame,
    NflPropLine,
    NflTeamGame,
)
from app.models.odds import ApiUsage, Edge, IngestRun, OddsSnapshot
from app.models.stats import PitcherGameLog, PitcherSeasonStats, TeamSeasonStats

__all__ = [
    "ApiUsage",
    "Edge",
    "Game",
    "GameInnings",
    "GameTeamLog",
    "IngestRun",
    "NflGame",
    "NflInjury",
    "NflLineHistory",
    "NflPlayerGame",
    "NflPropLine",
    "NflTeamGame",
    "OddsSnapshot",
    "PitcherGameLog",
    "PitcherSeasonStats",
    "Player",
    "Sport",
    "StatSource",
    "Team",
    "TeamSeasonStats",
    "TimestampMixin",
    "utcnow",
]
