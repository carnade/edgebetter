"""Team strength tiers and per-team rotation rankings.

"Good team" and "bad team" are graded on a curve rather than against fixed numbers:
tiers are percentile ranks within the league, so they stay meaningful in April when
everyone's rate stats are noisy and in September when the table has spread out.
"""

from __future__ import annotations

from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import PitcherSeasonStats, Player, Sport, StatSource, Team, TeamSeasonStats
from app.services.projections_mlb import PitcherInput

# Pythagorean exponent for baseball. Run differential predicts future wins better than
# actual win-loss record does, because it is less hostage to one-run luck.
PYTHAGOREAN_EXPONENT = 1.83

# League is split into thirds by default: the top 10 clubs are good, the bottom 10 bad.
GOOD_TIER_SIZE = 10
BAD_TIER_SIZE = 10

# A pitcher needs this many starts before their rotation slot means anything.
MIN_STARTS_FOR_RANK = 3


class Tier(str, Enum):
    GOOD = "good"
    MIDDLE = "middle"
    BAD = "bad"


@dataclass(frozen=True)
class TeamStrength:
    team_id: int
    abbrev: str
    name: str
    pythagorean: float
    runs_per_game: float
    runs_allowed_per_game: float
    games_played: int
    rank: int  # 1 = strongest
    tier: Tier


@dataclass(frozen=True)
class RotationSlot:
    player_id: int
    name: str
    rank: int  # 1 = best starter on this team
    rotation_size: int
    era: float | None
    regressed_era: float
    whip: float | None
    k_per_9: float | None
    games_started: int
    innings_pitched: float | None

    @property
    def is_top_two(self) -> bool:
        return self.rank <= 2

    @property
    def is_bottom_two(self) -> bool:
        return self.rank > self.rotation_size - 2

    def label(self) -> str:
        return f"#{self.rank} of {self.rotation_size}"


def team_strengths(session: Session, season: int) -> dict[int, TeamStrength]:
    """Rank all 30 clubs by Pythagorean expectation and assign tiers."""
    rows = session.execute(
        select(Team, TeamSeasonStats)
        .join(TeamSeasonStats, TeamSeasonStats.team_id == Team.id)
        .where(
            Team.sport == Sport.MLB,
            TeamSeasonStats.season == season,
            TeamSeasonStats.source == StatSource.MLB_STATSAPI,
        )
    ).all()

    scored: list[tuple[float, Team, TeamSeasonStats]] = []
    for team, stats in rows:
        if not (stats.runs_for and stats.runs_against and stats.games_played):
            continue
        rf = float(stats.runs_for)
        ra = float(stats.runs_against)
        pyth = rf**PYTHAGOREAN_EXPONENT / (
            rf**PYTHAGOREAN_EXPONENT + ra**PYTHAGOREAN_EXPONENT
        )
        scored.append((pyth, team, stats))

    scored.sort(key=lambda x: x[0], reverse=True)

    out: dict[int, TeamStrength] = {}
    total = len(scored)
    for index, (pyth, team, stats) in enumerate(scored):
        rank = index + 1
        if rank <= GOOD_TIER_SIZE:
            tier = Tier.GOOD
        elif rank > total - BAD_TIER_SIZE:
            tier = Tier.BAD
        else:
            tier = Tier.MIDDLE
        out[team.id] = TeamStrength(
            team_id=team.id,
            abbrev=team.abbrev,
            name=team.display_name,
            pythagorean=pyth,
            runs_per_game=float(stats.runs_for) / stats.games_played,
            runs_allowed_per_game=float(stats.runs_against) / stats.games_played,
            games_played=stats.games_played,
            rank=rank,
            tier=tier,
        )
    return out


def team_rotation(session: Session, team_id: int, season: int) -> list[RotationSlot]:
    """A team's starters, best first.

    Ranked on ERA regressed toward league average by innings, so a pitcher with four
    good starts does not outrank an established ace on a small sample.
    """
    rows = session.execute(
        select(Player, PitcherSeasonStats)
        .join(PitcherSeasonStats, PitcherSeasonStats.player_id == Player.id)
        .where(
            Player.team_id == team_id,
            PitcherSeasonStats.season == season,
            PitcherSeasonStats.games_started >= MIN_STARTS_FOR_RANK,
        )
    ).all()

    graded: list[tuple[float, Player, PitcherSeasonStats]] = []
    for player, stats in rows:
        regressed = PitcherInput(
            name=player.full_name,
            era=stats.era,
            innings_pitched=stats.innings_pitched,
            innings_per_start=None,
        ).regressed_era
        graded.append((regressed, player, stats))

    graded.sort(key=lambda x: x[0])
    size = len(graded)

    return [
        RotationSlot(
            player_id=player.id,
            name=player.full_name,
            rank=index + 1,
            rotation_size=size,
            era=stats.era,
            regressed_era=regressed,
            whip=stats.whip,
            k_per_9=stats.k_per_9,
            games_started=stats.games_started or 0,
            innings_pitched=stats.innings_pitched,
        )
        for index, (regressed, player, stats) in enumerate(graded)
    ]


def rotation_slot(
    session: Session, team_id: int, player_id: int | None, season: int
) -> RotationSlot | None:
    """Where one pitcher sits in their team's rotation, or None if unranked."""
    if not player_id:
        return None
    for slot in team_rotation(session, team_id, season):
        if slot.player_id == player_id:
            return slot
    return None
