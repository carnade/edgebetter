"""The projection's fitted constants, and why they are what they are.

Each value here was measured rather than chosen, and each replaced something that was
making projections worse. The tests pin the shape of the reasoning, not just the number,
so that changing one is a deliberate act with a measurement behind it.
"""

from app.services.nfl_props import (
    APPLY_OPPONENT_FACTOR,
    CV_MULTIPLIER,
    EFFICIENCY_SHRINKAGE,
    MARKET_CALIBRATION,
    SCALE_CORRECTION,
    VOLUME_SHRINKAGE,
    Market,
)

MARKETS = ("recv_yds", "rush_yds", "pass_yds")


class TestVolumeIsNotShrunk:
    """Volume is a role, not a skill estimate.

    Shrinking toward a league mean only pays when players are alike. They are not: a
    three-target receiver and a ten-target receiver differ by role, and pulling the first
    toward the league average invents volume he has never had. The old flat 4.0 did
    exactly that, over-projecting small players by 27% while leaving starters alone.
    """

    def test_receiving_and_rushing_use_the_players_own_volume(self):
        assert VOLUME_SHRINKAGE["recv_yds"] == 0.0
        assert VOLUME_SHRINKAGE["rush_yds"] == 0.0

    def test_passing_keeps_a_little(self):
        """Starting quarterbacks nearly all throw 25-40 times, so the league mean is a
        fair guess for them in a way it never was for receivers."""
        assert VOLUME_SHRINKAGE["pass_yds"] > 0

    def test_efficiency_is_still_regressed_hard(self):
        """The counterpart: efficiency IS a noisy skill estimate, so it keeps its pull.

        Measured reliability backs the split -- a receiver's own yards-per-target
        correlates 0.41 with his next half-season at high volume and 0.03 at low.
        """
        assert EFFICIENCY_SHRINKAGE >= 12.0
        assert all(EFFICIENCY_SHRINKAGE > v for v in VOLUME_SHRINKAGE.values())


class TestOpponentIsMeasuredButNotApplied:
    """The opponent factor does not predict, in any construction tested.

    Raw yards allowed per game, yards allowed per attempt, sample-size shrunk, and
    residual-based (yards allowed relative to what those players normally do) were all
    replayed walk-forward over 2021-2025. Every one was worse than ignoring the opponent.
    The residual version looked like a 2% win until the global over-projection it was
    quietly absorbing got its own correction, at which point it went negative again.
    """

    def test_the_factor_is_not_applied(self):
        assert APPLY_OPPONENT_FACTOR is False

    def test_it_is_still_available_to_display(self):
        """Turning it off in the maths is not the same as hiding it from the reader."""
        assert hasattr(Market.RECV_YDS, "value")
        from app.services.nfl_props import MAX_OPPONENT_EFFECT, PropProjection

        assert "opponent_factor" in PropProjection.__dataclass_fields__
        assert 0 < MAX_OPPONENT_EFFECT < 1


class TestScaleCorrection:
    """A flat multiplier is only honest once the bias it corrects is actually flat.

    Before volume shrinkage was fixed, rushing ran at 0.73 actual/projected under 20 yards
    and 1.00 above 60. A single constant would have dragged the accurate projections down
    to patch the broken ones. Fixing the shape first is what earns the scalar.
    """

    def test_receiving_and_rushing_are_corrected_downward(self):
        assert 0.9 < SCALE_CORRECTION["recv_yds"] < 1.0
        assert 0.9 < SCALE_CORRECTION["rush_yds"] < 1.0

    def test_passing_is_left_alone(self):
        """Passing's mean runs hot but its quantiles do not, and it is the quantiles that
        decide an over/under. Correcting it nearly doubled its worst gap."""
        assert SCALE_CORRECTION["pass_yds"] == 1.0


class TestSpreadIsPerMarket:
    def test_receiving_and_rushing_are_widened(self):
        """Their outcomes are wider than the fitted gamma: too few clear a low line and
        too many clear a high one."""
        assert CV_MULTIPLIER["recv_yds"] > 1.0
        assert CV_MULTIPLIER["rush_yds"] > 1.0

    def test_passing_is_not(self):
        assert CV_MULTIPLIER["pass_yds"] == 1.0

    def test_every_market_has_a_value(self):
        assert set(CV_MULTIPLIER) == set(MARKETS)
        assert set(VOLUME_SHRINKAGE) == set(MARKETS)
        assert set(SCALE_CORRECTION) == set(MARKETS)


class TestBarsAreComparable:
    def test_all_three_bars_improved_on_what_they_replaced(self):
        """The old bars were 0.035 / 0.069 / 0.086, and two of them were not even the
        same kind of measurement. Every market is now tighter than all three."""
        for market in MARKETS:
            assert MARKET_CALIBRATION[market][1] <= 0.036

    def test_ordering_reflects_sample_size(self):
        """Passing is loosest because one quarterback covers a whole team, so it has the
        thinnest history of the three."""
        assert (
            MARKET_CALIBRATION["recv_yds"][1]
            < MARKET_CALIBRATION["rush_yds"][1]
            < MARKET_CALIBRATION["pass_yds"][1]
        )
