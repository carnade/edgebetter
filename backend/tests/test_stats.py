"""Statistical guards. These are what stop small-sample splits from looking like edges.

Wilson interval values below are checked against the closed form and against published
worked examples, because every conditional split in the NFL tool depends on them.
"""

import pytest

from app.services.stats import (
    BREAK_EVEN_110,
    Band,
    holdout_compare,
    mean_and_interval,
    rate,
    required_sample,
    sample_band,
    wilson_interval,
)


class TestWilsonInterval:
    def test_symmetric_at_half(self):
        lo, hi = wilson_interval(50, 100)
        assert lo == pytest.approx(1 - hi, abs=1e-9)
        assert lo < 0.5 < hi

    def test_known_value(self):
        # 50/100 at z=1.96: Wilson gives approximately [0.404, 0.596].
        lo, hi = wilson_interval(50, 100)
        assert lo == pytest.approx(0.4038, abs=1e-3)
        assert hi == pytest.approx(0.5962, abs=1e-3)

    def test_narrows_as_sample_grows(self):
        widths = [wilson_interval(n // 2, n)[1] - wilson_interval(n // 2, n)[0]
                  for n in (20, 100, 500, 2000)]
        assert all(a > b for a, b in zip(widths, widths[1:]))

    def test_stays_inside_zero_one_at_extremes(self):
        """The normal approximation would exceed 1 here; Wilson must not."""
        lo, hi = wilson_interval(12, 12)
        assert 0.0 <= lo <= 1.0
        assert 0.0 <= hi <= 1.0
        assert hi <= 1.0

    def test_zero_hits_has_lower_bound_of_zero(self):
        lo, hi = wilson_interval(0, 30)
        assert lo == 0.0
        assert hi > 0.0

    def test_small_sample_interval_is_uselessly_wide(self):
        """The 9-3 case from the plan: it must not look like a finding."""
        lo, hi = wilson_interval(9, 12)
        assert lo < BREAK_EVEN_110 < hi
        assert hi - lo > 0.4

    def test_empty_sample(self):
        assert wilson_interval(0, 0) == (0.0, 1.0)

    def test_rejects_impossible_input(self):
        with pytest.raises(ValueError):
            wilson_interval(11, 10)
        with pytest.raises(ValueError):
            wilson_interval(-1, 10)


class TestSampleBand:
    def test_boundaries(self):
        assert sample_band(29) is Band.NOISE
        assert sample_band(30) is Band.SUGGESTIVE
        assert sample_band(99) is Band.SUGGESTIVE
        assert sample_band(100) is Band.MODERATE
        assert sample_band(299) is Band.MODERATE
        assert sample_band(300) is Band.MEANINGFUL

    def test_only_larger_bands_are_trustworthy(self):
        assert not sample_band(12).trustworthy
        assert not sample_band(50).trustworthy
        assert sample_band(150).trustworthy
        assert sample_band(1000).trustworthy


class TestRate:
    def test_break_even_reference_is_minus_110_not_half(self):
        assert BREAK_EVEN_110 == pytest.approx(0.5238, abs=1e-4)

    def test_point_estimate_above_break_even_is_not_enough(self):
        """55% on 40 games looks good and means nothing -- the interval straddles."""
        r = rate(22, 40)
        assert r.rate > BREAK_EVEN_110
        assert not r.beats_break_even
        assert r.verdict == "indistinguishable from break-even"

    def test_large_clear_edge_is_recognised(self):
        r = rate(360, 600)  # 60% on 600 games
        assert r.beats_break_even
        assert r.verdict == "clears break-even"
        assert r.band is Band.MEANINGFUL

    def test_clearly_losing_split(self):
        r = rate(180, 500)  # 36%
        assert r.upper < BREAK_EVEN_110
        assert r.verdict == "below break-even"

    def test_tiny_sample_is_called_out_regardless_of_rate(self):
        r = rate(9, 12)
        assert r.band is Band.NOISE
        assert r.verdict == "too few games to say anything"

    def test_format_includes_interval_and_band(self):
        text = rate(55, 100).format()
        assert "55.0%" in text and "CI [" in text and "moderate" in text


class TestHoldout:
    def test_pattern_that_reverses_is_flagged(self):
        result = holdout_compare(explore_hits=120, explore_n=200, holdout_hits=20, holdout_n=60)
        assert result.explore.rate > BREAK_EVEN_110
        assert result.holdout.rate < BREAK_EVEN_110
        assert not result.direction_held
        assert result.status == "reversed out of sample"
        assert not result.survives

    def test_pattern_that_holds_survives(self):
        result = holdout_compare(explore_hits=120, explore_n=200, holdout_hits=36, holdout_n=60)
        assert result.direction_held
        assert result.survives
        assert result.status == "held"

    def test_no_holdout_sample_never_survives(self):
        result = holdout_compare(explore_hits=120, explore_n=200, holdout_hits=8, holdout_n=10)
        assert result.status == "no holdout sample"
        assert not result.survives

    def test_untrustworthy_explore_sample_never_survives(self):
        """Even a matching holdout cannot rescue a 12-game discovery."""
        result = holdout_compare(explore_hits=9, explore_n=12, holdout_hits=40, holdout_n=60)
        assert result.direction_held
        assert not result.survives

    def test_sharp_move_is_reported_even_when_direction_holds(self):
        result = holdout_compare(explore_hits=180, explore_n=200, holdout_hits=33, holdout_n=60)
        assert result.direction_held
        assert result.status == "held direction but moved sharply"

    def test_gap_sign(self):
        result = holdout_compare(100, 200, 40, 60)
        assert result.gap > 0


class TestRequiredSample:
    def test_smaller_effects_need_more_games(self):
        assert required_sample(0.10) < required_sample(0.05) < required_sample(0.02)

    def test_matches_the_rule_of_thumb_in_the_plan(self):
        # ~100 games to detect 60% vs 50%; ~400 to detect 55%.
        assert 80 <= required_sample(0.10) <= 130
        assert 350 <= required_sample(0.05) <= 450

    def test_rejects_zero_effect(self):
        with pytest.raises(ValueError):
            required_sample(0.0)


class TestMeanAndInterval:
    def test_known_mean(self):
        mean, lo, hi = mean_and_interval([10.0, 20.0, 30.0])
        assert mean == pytest.approx(20.0)
        assert lo < 20.0 < hi

    def test_empty(self):
        assert mean_and_interval([]) == (0.0, 0.0, 0.0)

    def test_single_value_has_no_spread(self):
        assert mean_and_interval([21.0]) == (21.0, 21.0, 21.0)

    def test_interval_narrows_with_more_data(self):
        few = mean_and_interval([20.0, 24.0, 28.0] * 3)
        many = mean_and_interval([20.0, 24.0, 28.0] * 100)
        assert (few[2] - few[1]) > (many[2] - many[1])
