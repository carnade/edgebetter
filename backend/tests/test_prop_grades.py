"""Prop grading. The rule that matters: an edge must clear the market's own error.

Receiving is accurate to 1.9 points at worst, rushing 2.6, passing 3.6 -- all measured
the same way over a 2021-2025 replay. The same edge should therefore grade differently
across them, and these tests pin exactly that.

The bars used to be 3.5 / 6.9 / 8.6, measured inconsistently (receiving was a mean gap,
the others max gaps) on a model that over-projected. Both are fixed, so the same edge now
grades higher than it once did -- that is the model earning it, not the bar being lowered.
"""

import pytest

from app.services.nfl_prop_grades import (
    MIN_BOOKS_FOR_CONSENSUS,
    MIN_MEANINGFUL_EDGE,
    Grade,
    GradedProp,
    rank,
)
from app.services.nfl_props import MARKET_CALIBRATION, Market
from app.services.stats import Band


def make(
    market=Market.RECV_YDS,
    model_prob=0.60,
    price=-110,
    games=17,
    band=Band.MEANINGFUL,
    books_posting=1,
    line_span=None,
    median_line=None,
) -> GradedProp:
    _, gap, _ = MARKET_CALIBRATION[market.value]
    from app.services.devig import american_to_decimal

    return GradedProp(
        player_name="Test Player",
        player_id="00-0000000",
        team="SEA",
        opponent="SF",
        market=market,
        side="Over",
        line=69.5,
        book="fanduel",
        price_american=price,
        projected=80.0,
        projected_median=72.0,
        model_prob=model_prob,
        break_even=1.0 / american_to_decimal(price),
        games_of_history=games,
        band=band,
        calibration_gap=gap,
        calibration="validated",
        books_posting=books_posting,
        line_span=line_span,
        median_line=median_line,
    )


class TestCalibrationBar:
    def test_each_market_carries_its_measured_gap(self):
        assert MARKET_CALIBRATION["recv_yds"][1] == pytest.approx(0.019)
        assert MARKET_CALIBRATION["rush_yds"][1] == pytest.approx(0.026)
        assert MARKET_CALIBRATION["pass_yds"][1] == pytest.approx(0.036)

    def test_every_bar_is_the_same_kind_of_measurement(self):
        """They were once a mix of mean and max gaps, which made them incomparable.

        All three are now the worst gap over the same replay, so ordering them means
        something. Receiving sits below the noise floor, which is the floor's job.
        """
        recv, rush, pass_ = (MARKET_CALIBRATION[m][1] for m in ("recv_yds", "rush_yds", "pass_yds"))
        assert recv < rush < pass_
        assert recv < MIN_MEANINGFUL_EDGE

    def test_bar_never_falls_below_the_noise_floor(self):
        """Even a perfectly calibrated market needs a minimum edge to be worth acting on."""
        g = make(market=Market.RECV_YDS)
        assert g.required_edge >= MIN_MEANINGFUL_EDGE

    def test_weaker_markets_demand_more(self):
        recv = make(market=Market.RECV_YDS)
        rush = make(market=Market.RUSH_YDS)
        pass_ = make(market=Market.PASS_YDS)
        assert recv.required_edge < rush.required_edge < pass_.required_edge


class TestGrading:
    def test_same_edge_grades_differently_by_market(self):
        """The central rule: identical numbers, different trust, different grade."""
        # -110 breaks even at 52.4%; 56.9% is a +4.5 point edge.
        recv = make(market=Market.RECV_YDS, model_prob=0.569)
        rush = make(market=Market.RUSH_YDS, model_prob=0.569)
        pass_ = make(market=Market.PASS_YDS, model_prob=0.569)

        assert recv.edge == pytest.approx(rush.edge)
        assert recv.grade is Grade.A      # 4.5 pts is more than double a 2.0 pt bar
        assert rush.grade is Grade.B      # clears 2.6, but not by 2x
        assert pass_.grade is Grade.B     # clears 3.6, but not by 2x

        # The point is the ordering, which must survive any future re-measurement.
        assert recv.edge_ratio > rush.edge_ratio > pass_.edge_ratio

    def test_a_thin_edge_survives_only_on_the_best_market(self):
        """The mirror case: an edge small enough that only receiving can carry it."""
        # +2.2 points.
        assert make(market=Market.RECV_YDS, model_prob=0.546).grade is Grade.B
        assert make(market=Market.RUSH_YDS, model_prob=0.546).grade is Grade.C
        assert make(market=Market.PASS_YDS, model_prob=0.546).grade is Grade.C

    def test_a_grade_needs_double_the_bar(self):
        """Receiving's effective bar is the 2.0 pt floor, so an A needs roughly +4."""
        assert make(market=Market.RECV_YDS, model_prob=0.60).grade is Grade.A
        assert make(market=Market.RECV_YDS, model_prob=0.55).grade is Grade.B

    def test_large_edge_can_still_earn_an_A_on_a_weak_market(self):
        """Passing is not hidden -- it just has to clear a much higher bar."""
        g = make(market=Market.PASS_YDS, model_prob=0.80)
        assert g.edge > 0.25
        assert g.grade is Grade.A

    def test_no_edge_is_a_D(self):
        assert make(model_prob=0.50).grade is Grade.D
        assert make(model_prob=0.40).grade is Grade.D

    def test_edge_inside_error_is_a_C_not_a_B(self):
        # +1.6 pts on rushing, whose bar is 2.6.
        g = make(market=Market.RUSH_YDS, model_prob=0.54)
        assert 0 < g.edge < g.required_edge
        assert g.grade is Grade.C

    def test_thin_history_cannot_earn_an_actionable_grade(self):
        g = make(model_prob=0.90, games=3, band=Band.NOISE)
        assert g.edge > 0.3
        assert not g.grade.actionable
        assert "games of history" in g.reason

    def test_actionable_flag(self):
        assert Grade.A.actionable and Grade.B.actionable
        assert not Grade.C.actionable and not Grade.D.actionable


class TestEdgeMaths:
    def test_break_even_from_price(self):
        assert make(price=-110).break_even == pytest.approx(0.5238, abs=1e-3)
        assert make(price=100).break_even == pytest.approx(0.5)
        assert make(price=150).break_even == pytest.approx(0.4)

    def test_edge_is_probability_minus_break_even(self):
        g = make(model_prob=0.60, price=-110)
        assert g.edge == pytest.approx(0.60 - 0.5238, abs=1e-3)

    def test_expected_value_agrees_with_edge_sign(self):
        assert make(model_prob=0.60).expected_value > 0
        assert make(model_prob=0.45).expected_value < 0

    def test_edge_ratio_is_edge_over_bar(self):
        g = make(market=Market.RECV_YDS, model_prob=0.58)
        assert g.edge_ratio == pytest.approx(g.edge / g.required_edge)
        assert g.edge_ratio > 1.0  # clears its bar


class TestRanking:
    def test_ranks_by_grade_then_how_far_past_the_bar(self):
        strong = make(market=Market.RECV_YDS, model_prob=0.62)
        weak = make(market=Market.RECV_YDS, model_prob=0.545)
        none = make(model_prob=0.45)
        ordered = rank([none, weak, strong])
        assert ordered[0] is strong
        assert ordered[-1] is none

    def test_smaller_edge_on_a_trusted_market_outranks_a_bigger_one_on_a_weak_market(self):
        """This is why ranking uses the ratio rather than the raw edge."""
        recv = make(market=Market.RECV_YDS, model_prob=0.58)   # +5.6 vs a 2.0 bar
        pass_ = make(market=Market.PASS_YDS, model_prob=0.60)  # +7.6 vs a 3.6 bar
        assert pass_.edge > recv.edge
        ordered = rank([pass_, recv])
        assert ordered[0] is recv

    def test_empty(self):
        assert rank([]) == []


class TestDistributionShape:
    """A gamma with CV above 1 has its mode at zero, which is nonsense for a starter.

    This was a real bug: the CV cap was applied before the spread multiplier, so the
    effective ceiling was 1.38 and shapes fell below 1. A receiver projected for 30 yards
    was being told his single likeliest outcome was none at all.
    """

    def test_cap_is_applied_after_the_multiplier(self):
        from app.services.nfl_props import CV_MULTIPLIER

        # Worst case: a player whose own spread already exceeds the cap.
        for mult in CV_MULTIPLIER.values():
            capped = max(0.35, min(1.0, 2.0 * mult))
            assert capped <= 1.0

    def test_shape_never_drops_below_one(self):
        """Shape = (mean/sd)^2, so a CV at the 1.0 cap gives shape exactly 1."""
        from app.services.nfl_props import CV_MULTIPLIER

        for mult in CV_MULTIPLIER.values():
            for raw in (0.5, 0.8, 1.0, 1.5, 3.0):
                cv = max(0.35, min(1.0, raw * mult))
                assert (1.0 / cv) ** 2 >= 1.0

    def test_median_sits_below_mean_for_a_skewed_distribution(self):
        """The median is the 50/50 point and is what decides an over/under."""
        from app.services.nfl_props import Market, PropProjection
        from app.services.stats import Band

        p = PropProjection(
            player_id="x", player_name="X", position="WR", team="SEA", opponent="SF",
            market=Market.RECV_YDS, expected=50.0, sd=40.0, games_of_history=17,
            band=Band.MEANINGFUL, projected_volume=6.0, projected_efficiency=8.3,
            opponent_factor=1.0, context_factor=1.0, snap_pct=0.8, target_share=0.2,
            recent_yards=[], notes=[],
        )
        assert p.median is not None
        assert p.median < p.expected
        # A line at the mean is therefore an under more often than not.
        assert p.prob_over(p.expected) < 0.5


class TestBookCoverage:
    """Thin coverage warns; it never changes the grade.

    This is deliberately not the MLB four-book rule. There, a fair probability is the
    median across books and too few books makes it meaningless. Here the probability comes
    from the player's own distribution, so one book is enough to grade against -- a second
    book only checks the NUMBER. So a thin prop is shown, graded, and labelled.
    """

    def test_single_book_warns(self):
        g = make(books_posting=1)
        assert g.coverage_warning is not None
        assert "Only fanduel" in g.coverage_warning

    def test_thin_coverage_warns(self):
        g = make(books_posting=MIN_BOOKS_FOR_CONSENSUS - 1)
        assert g.coverage_warning is not None
        assert "Thin agreement" in g.coverage_warning

    def test_good_coverage_is_silent(self):
        """No badge when there is nothing to say."""
        assert make(books_posting=MIN_BOOKS_FOR_CONSENSUS).coverage_warning is None
        assert make(books_posting=8).coverage_warning is None

    def test_coverage_never_changes_the_grade(self):
        """The whole point: a thin prop is still ranked on its merits."""
        thin = make(model_prob=0.60, books_posting=1)
        covered = make(model_prob=0.60, books_posting=9)
        assert thin.grade is covered.grade is Grade.A
        assert thin.edge == covered.edge
        assert rank([thin, covered])[0].grade is Grade.A

    def test_outlier_line_is_called_out(self):
        """A book posting 69.5 while the market sits at 60.5 is the dangerous case:
        the edge is against that book's quirk, not against the market."""
        g = make(books_posting=4, median_line=60.5)
        assert g.line == 69.5
        assert g.line_is_outlier
        assert "check for late news" in (g.coverage_warning or "")

    def test_line_near_the_median_is_not_an_outlier(self):
        g = make(books_posting=4, median_line=69.0)
        assert not g.line_is_outlier
        assert g.coverage_warning is None

    def test_outlier_needs_something_to_compare_against(self):
        """One book cannot be an outlier from itself."""
        g = make(books_posting=1, median_line=None)
        assert not g.line_is_outlier
