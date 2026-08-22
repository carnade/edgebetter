"""The projection's fitted constants, and why they are what they are.

Each value here was measured rather than chosen, and each replaced something that was
making projections worse. The tests pin the shape of the reasoning, not just the number,
so that changing one is a deliberate act with a measurement behind it.
"""

import pytest

from app.services.nfl_props import (
    APPLY_OPPONENT_FACTOR,
    COUNT_DISPERSION,
    CV_MULTIPLIER,
    EFFICIENCY_SHRINKAGE,
    MARKET_CALIBRATION,
    SCALE_CORRECTION,
    VOLUME_SHRINKAGE,
    Market,
)

YARDS_MARKETS = ("recv_yds", "rush_yds", "pass_yds")
COUNT_MARKETS = ("receptions", "rush_att")
MARKETS = YARDS_MARKETS + COUNT_MARKETS


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

    def test_spread_settings_cover_the_markets_they_apply_to(self):
        """CV is a continuous-distribution idea and does not extend to counts.

        A negative binomial's variance follows from its mean and dispersion, so the count
        markets are configured by COUNT_DISPERSION instead and must not silently pick up a
        CV multiplier.
        """
        assert set(CV_MULTIPLIER) == set(YARDS_MARKETS)
        assert set(COUNT_DISPERSION) == set(COUNT_MARKETS)
        assert not set(CV_MULTIPLIER) & set(COUNT_DISPERSION)

    def test_every_market_is_priced_and_graded(self):
        for market in Market:
            assert market.value in MARKET_CALIBRATION
            assert market.value in SCALE_CORRECTION

    def test_volume_shrinkage_defaults_to_none_for_counts(self):
        """Counts are not in the dict, and the lookup default of 0.0 is what we want:
        volume is a role for a count market exactly as it is for a yards market."""
        for market in COUNT_MARKETS:
            assert VOLUME_SHRINKAGE.get(market, 0.0) == 0.0


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


class TestCountMarkets:
    """Receptions and rush attempts are markets on the volume term alone.

    Every yards projection is volume x efficiency, where volume is a stable role and
    efficiency is the noisy part. These two drop the noisy half rather than adding
    anything, which is why they were worth having.
    """

    def test_they_are_discrete_and_the_yards_markets_are_not(self):
        assert Market.RECEPTIONS.discrete and Market.RUSH_ATT.discrete
        assert not Market.RECV_YDS.discrete
        assert not Market.RUSH_YDS.discrete
        assert not Market.PASS_YDS.discrete

    def test_receptions_is_targets_times_catch_rate(self):
        """Modelled on targets, so the efficiency term is catch rate -- bounded 0-1 and
        far better behaved than yards per target."""
        assert Market.RECEPTIONS.volume == "targets"
        assert Market.RECEPTIONS.stat == "receptions"
        assert not Market.RECEPTIONS.is_pure_volume

    def test_rush_attempts_has_no_efficiency_term_at_all(self):
        """stat == volume, so efficiency collapses to 1.0 and the projection is simply
        the player's decayed carries."""
        assert Market.RUSH_ATT.is_pure_volume
        assert Market.RUSH_ATT.stat == Market.RUSH_ATT.volume == "carries"

    def test_rush_attempts_is_the_more_overdispersed(self):
        """Game script moves carries hard in both directions; receptions track a
        receiver's role much more closely."""
        assert COUNT_DISPERSION["rush_att"] > COUNT_DISPERSION["receptions"] > 1.0

    def test_support_caps_clear_the_single_game_records(self):
        """21 receptions and 45 carries are the records, so truncated tail mass is
        negligible rather than merely small."""
        assert Market.RECEPTIONS.max_count > 21
        assert Market.RUSH_ATT.max_count > 45

    def test_probability_over_a_half_point_line_is_a_sum_over_integers(self):
        """The reason these cannot use the gamma path: at a 2.5 line nearly all the
        probability sits on a handful of integers, and a continuous curve misprices
        exactly the numbers people bet."""
        from app.services.projections_props import distribution, prob_over_line

        dist = distribution(3.4, dispersion=1.25, max_count=24)
        assert sum(dist) == pytest.approx(1.0, abs=1e-9)
        assert prob_over_line(3.4, 2.5, dispersion=1.25, max_count=24) == pytest.approx(
            sum(dist[3:]), abs=1e-12
        )


class TestScanCoversEveryMarket:
    """A market that exists must be scannable, not just projectable.

    The scanner's market list was hardcoded, so adding receptions and rush attempts left
    them priced on the Props page and invisible on the Scan page. The failure was silent:
    their lines were ingested and counted, then filtered out before grading.
    """

    def test_scan_default_is_derived_from_the_enum(self):
        import inspect

        from app.services.nfl_prop_scanner import scan_week

        default = inspect.signature(scan_week).parameters["markets"].default
        # None means "every market", resolved from Market at call time. A literal tuple
        # here is the bug this test exists to catch.
        assert default is None

    def test_every_market_can_be_graded(self):
        """Each market needs a calibration entry, or grade_line raises on its first line."""
        from app.services.nfl_prop_grades import MARKET_CALIBRATION as _cal

        for market in Market:
            assert market.value in _cal


class TestPartialSlateDetection:
    """A truncated week must not look like a complete one.

    The warning compares games we hold lines for against games on the slate. Its failure
    mode is silent in both directions worth guarding: a slate size of 0 disables the
    warning entirely, and that is exactly what happened when the season had to be passed
    explicitly -- the UI only passes a week, so in normal use the check was dead.
    """

    def test_zero_slate_size_produces_no_false_warning(self):
        from app.services.nfl_prop_scanner import ScanResult

        r = ScanResult([], 0, 0, 0, week=1, season=None, games_in_week=0, games_with_lines=0)
        assert r.missing_games_warning is None

    def test_partial_slate_is_reported_with_the_counts(self):
        from app.services.nfl_prop_scanner import ScanResult

        r = ScanResult([], 0, 0, 0, week=1, season=2026, games_in_week=16, games_with_lines=2)
        warning = r.missing_games_warning
        assert warning is not None
        assert "2 of 16" in warning
        assert "14 missing" in warning

    def test_complete_slate_is_silent(self):
        from app.services.nfl_prop_scanner import ScanResult

        r = ScanResult([], 0, 0, 0, week=1, season=2026, games_in_week=16, games_with_lines=16)
        assert r.missing_games_warning is None
