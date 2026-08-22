"""NBA game projection: pace and efficiency.

Estimate how many possessions the two teams will play, convert each side's efficiency
against the other's defence into points, and treat the total and margin as Normal.

These are model estimates, not predictions. Constants below are calibrated against the
completed 2025-26 season by `cli backtest nba`; if that command reports a worse MAE
after a change, the change is wrong.
"""

from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.services.devig import normal_cdf, prob_over
from app.services.ratings import TeamRating, league_averages, team_rating

# Home advantage in points of margin. The modern NBA sits near 2.0-2.5.
HOME_ADVANTAGE = 2.2

# Predictive standard deviations, MEASURED by the walk-forward backtest over the full
# 2025-26 season (`cli backtest nba`), not assumed.
#
# These were originally set to 11.5 / 12.5 on the common claim that NBA totals have a
# sigma around 11-13. That is wrong, and badly so: the observed sigma of game totals is
# 20.2, and the model's own forecast RMSE is 19.3. Using 11.5 made every over/under
# probability wildly overconfident -- the calibration curve was off by up to 14
# percentage points in the tails. With these values the curve tracks to within ~0.03.
#
# The right number is forecast RMSE, not the raw spread of totals: it is the width of
# the distribution of what we do not know, given the projection.
TOTAL_SIGMA = 19.3
MARGIN_SIGMA = 14.8

# The ratio form over-projects slightly, because scaling an above-average offence by an
# above-average defensive ratio compounds. Measured at +1.21 points of total and +0.67
# of margin across 1075 scored games; removed here as an explicit, re-derivable
# correction rather than by fudging the ratings.
TOTAL_BIAS_CORRECTION = 1.21
MARGIN_BIAS_CORRECTION = 0.67


@dataclass(frozen=True)
class NbaProjection:
    home_points: float
    away_points: float
    possessions: float
    home_rating: TeamRating
    away_rating: TeamRating

    @property
    def total(self) -> float:
        return self.home_points + self.away_points

    @property
    def margin(self) -> float:
        """Positive means the home team is favoured."""
        return self.home_points - self.away_points

    @property
    def blended(self) -> bool:
        """True when either side leaned on the prior season for a small sample."""
        return self.home_rating.blended or self.away_rating.blended

    def prob_over(self, line: float) -> float:
        return prob_over(line, self.total, TOTAL_SIGMA)

    def prob_home_cover(self, spread: float) -> float:
        """P(home covers). `spread` is the home handicap, negative when favoured."""
        return 1.0 - normal_cdf(-spread, self.margin, MARGIN_SIGMA)

    def prob_home_win(self) -> float:
        return 1.0 - normal_cdf(0.0, self.margin, MARGIN_SIGMA)


def expected_possessions(home_pace: float, away_pace: float, league_pace: float) -> float:
    """Two teams meeting play at roughly the product of their paces over the league's.

    Both teams share nearly the same possession count in a basketball game, so this is
    a single number for the game rather than one per side.
    """
    if league_pace <= 0:
        raise ValueError("league pace must be positive")
    return home_pace * away_pace / league_pace


def project(
    session: Session, home_team_id: int, away_team_id: int, season: int
) -> NbaProjection | None:
    """Project one matchup, or None when either side lacks ratings."""
    home = team_rating(session, home_team_id, season)
    away = team_rating(session, away_team_id, season)
    if home is None or away is None:
        return None

    league_rating, league_pace = league_averages(session, season)
    return project_from_ratings(home, away, league_rating, league_pace)


def project_from_ratings(
    home: TeamRating, away: TeamRating, league_rating: float, league_pace: float
) -> NbaProjection:
    """Pure projection maths, separated so the backtest can drive it directly."""
    poss = expected_possessions(home.pace, away.pace, league_pace)

    # A team's efficiency in this matchup: its own offence adjusted by how the
    # opponent's defence compares to league average.
    home_eff = home.off_rating * away.def_rating / league_rating
    away_eff = away.off_rating * home.def_rating / league_rating

    home_points = poss * home_eff / 100.0 + HOME_ADVANTAGE / 2.0
    away_points = poss * away_eff / 100.0 - HOME_ADVANTAGE / 2.0

    # Remove the measured biases: half the total correction from each side, and the
    # margin correction split symmetrically so it cancels out of the total.
    home_points -= TOTAL_BIAS_CORRECTION / 2.0 + MARGIN_BIAS_CORRECTION / 2.0
    away_points -= TOTAL_BIAS_CORRECTION / 2.0 - MARGIN_BIAS_CORRECTION / 2.0

    return NbaProjection(
        home_points=home_points,
        away_points=away_points,
        possessions=poss,
        home_rating=home,
        away_rating=away,
    )
