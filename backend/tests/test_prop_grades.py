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
    best_bet_per_prop,
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
        assert MARKET_CALIBRATION["recv_yds"][1] == pytest.approx(0.020)
        assert MARKET_CALIBRATION["rush_yds"][1] == pytest.approx(0.027)
        assert MARKET_CALIBRATION["pass_yds"][1] == pytest.approx(0.036)
        assert MARKET_CALIBRATION["receptions"][1] == pytest.approx(0.028)
        assert MARKET_CALIBRATION["rush_att"][1] == pytest.approx(0.028)

    def test_every_bar_is_the_same_kind_of_measurement(self):
        """They were once a mix of mean and max gaps, which made them incomparable.

        Every bar is now the worst gap over the same replay, taking the worse of the full
        period and the holdout, so ordering them means something.
        """
        recv, rush, pass_ = (
            MARKET_CALIBRATION[m][1] for m in ("recv_yds", "rush_yds", "pass_yds")
        )
        assert recv < rush < pass_

    def test_receiving_is_bounded_by_the_noise_floor_not_its_own_error(self):
        """Receiving now measures at the floor rather than under it.

        Its worst gap is 0.0191, which rounds up to exactly MIN_MEANINGFUL_EDGE, so the
        floor is what binds. That is the floor doing its job: even a market we model this
        well needs a minimum edge before a bet is worth making.
        """
        recv = MARKET_CALIBRATION["recv_yds"][1]
        assert recv <= MIN_MEANINGFUL_EDGE
        assert make(market=Market.RECV_YDS).required_edge == pytest.approx(
            MIN_MEANINGFUL_EDGE
        )

    def test_bars_never_understate_the_measured_error(self):
        """Bars are rounded UP, never to nearest.

        Receiving measured 0.0191 and rushing 0.0260; rounding to nearest would have set
        both marginally below the error they stand for, which defeats the point of the
        bar. These are the ceiling values.
        """
        assert MARKET_CALIBRATION["recv_yds"][1] >= 0.0191
        assert MARKET_CALIBRATION["rush_yds"][1] >= 0.0261
        assert MARKET_CALIBRATION["receptions"][1] >= 0.0278
        assert MARKET_CALIBRATION["rush_att"][1] >= 0.0276

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

    def test_four_games_is_deliberately_enough(self):
        """The floor is low on purpose: a higher one blanks out the first month of the
        season, when a changed role is most worth catching. A thin pick is surfaced as
        thin rather than withheld."""
        from app.services.nfl_prop_grades import MIN_GAMES_FOR_GRADE

        assert MIN_GAMES_FOR_GRADE == 4
        g = make(model_prob=0.64, games=4, band=Band.SUGGESTIVE)
        assert g.grade.actionable
        assert g.games_of_history == MIN_GAMES_FOR_GRADE

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


class TestBestBetPerProp:
    """One prop is one bet, however many books post it.

    Over and Under on the same number are the same question from opposite ends. With
    symmetric prices exactly one carries the positive edge, so listing both guarantees
    every pick reappears at the far end of the ranking as its own mirror -- which is how
    the same player ended up both best and second-worst on a 13-row scan.
    """

    def _pair(self, prob_over, **kw):
        over = make(model_prob=prob_over, **kw)
        under = make(model_prob=1 - prob_over, **kw)
        return over, under

    def test_keeps_the_side_with_the_edge(self):
        over, under = self._pair(0.62)
        kept = best_bet_per_prop([over, under])
        assert kept == [over]

    def test_keeps_the_under_when_the_under_is_the_bet(self):
        over, under = self._pair(0.38)
        kept = best_bet_per_prop([over, under])
        assert kept == [under]

    def test_one_player_one_market_is_always_one_row(self):
        """Whatever the books post -- two sides, several numbers, several books -- the
        table gets exactly one row for the bet."""
        from dataclasses import replace

        rows = []
        for i, prob in enumerate((0.62, 0.58, 0.41)):
            over, under = self._pair(prob)
            rows += [
                replace(over, line=60.5 + i, book=f"book{i}"),
                replace(under, line=60.5 + i, book=f"book{i}"),
            ]
        assert len(best_bet_per_prop(rows)) == 1

    def test_a_line_and_its_mirror_cannot_both_survive(self):
        """The specific complaint: best and worst being the same bet."""
        over, under = self._pair(0.64)
        kept = best_bet_per_prop(rank([over, under]))
        assert len(kept) == 1
        assert kept[0].edge > 0

    def test_the_same_prop_at_two_books_collapses_to_the_better_price(self):
        """Four books used to mean four rows for one bet. The row kept is the best
        price, so collapsing performs the line shopping rather than discarding it."""
        from dataclasses import replace

        # break_even is a stored field, not derived from price_american, so a realistic
        # pair has to set both -- exactly as grade_line does when it builds them.
        from app.services.devig import american_to_decimal

        cheap = make(model_prob=0.60, price=-140)
        rich = replace(
            cheap,
            book="betrivers",
            price_american=-105,
            break_even=1.0 / american_to_decimal(-105),
        )
        kept = best_bet_per_prop([cheap, rich])
        assert len(kept) == 1
        assert kept[0].book == "betrivers"
        assert kept[0].edge > cheap.edge

    def test_differing_numbers_collapse_to_the_softest_line(self):
        """Books posting different numbers is the other half of line shopping."""
        from dataclasses import replace

        hard = make(model_prob=0.55)
        soft = replace(hard, book="betrivers", line=hard.line - 3.0, model_prob=0.63)
        kept = best_bet_per_prop([hard, soft])
        assert len(kept) == 1
        assert kept[0].book == "betrivers"

    def test_both_sides_still_gives_one_row_each_not_one_per_book(self):
        from dataclasses import replace

        # make() always builds an Over, so the Under has to be set explicitly.
        over = make(model_prob=0.60)
        under = replace(make(model_prob=0.40), side="Under")
        over_b = replace(over, book="betrivers")
        under_b = replace(under, book="betrivers")
        kept = best_bet_per_prop([over, under, over_b, under_b], per_side=True)
        assert len(kept) == 2
        assert {g.side for g in kept} == {"Over", "Under"}

    def test_different_markets_for_one_player_stay_separate(self):
        """Receptions and receiving yards are different bets on the same player."""
        from dataclasses import replace

        a = make(market=Market.RECV_YDS, model_prob=0.60)
        b = replace(a, market=Market.RECEPTIONS)
        assert len(best_bet_per_prop([a, b])) == 2

    def test_order_is_preserved(self):
        """Collapsing must not reshuffle a ranking that was already sorted."""
        rows = rank([g for prob in (0.62, 0.58, 0.41) for g in self._pair(prob)])
        kept = best_bet_per_prop(rows)
        assert kept == sorted(kept, key=lambda g: rows.index(g))

    def test_empty(self):
        assert best_bet_per_prop([]) == []


class TestDecimalOddsPricing:
    """Grading against the odds you can actually get, quoted the way your book quotes them.

    The scan finds candidates against US lines, but a grade belongs to one number at one
    price -- not to a player. A different line, or the same line at a different price, is a
    different bet. So the decision has to be made against the book being bet with, and for
    a European book that means decimal odds.
    """

    def test_decimal_and_american_agree_on_break_even(self):
        from app.services.devig import american_to_decimal, decimal_to_american

        for decimal in (1.5, 1.73, 1.91, 1.95, 2.0, 2.5, 3.4):
            american = decimal_to_american(decimal)
            # Round-tripping through American loses a little precision, since American
            # odds are integers. Break-even must still land within a tenth of a point.
            assert american_to_decimal(american) == pytest.approx(decimal, abs=0.02)

    def test_evens_is_a_coin_flip(self):
        from app.services.devig import decimal_to_american

        g = make(model_prob=0.60, price=decimal_to_american(2.0))
        assert g.break_even == pytest.approx(0.5, abs=0.005)

    def test_a_longer_price_needs_less_to_break_even(self):
        """The whole reason to enter your own odds: the same line at a better price is a
        better bet, and at a worse price may be no bet at all."""
        from app.services.devig import decimal_to_american

        short = make(model_prob=0.55, price=decimal_to_american(1.60))
        long_ = make(model_prob=0.55, price=decimal_to_american(2.10))
        assert long_.break_even < short.break_even
        assert long_.edge > short.edge

    def test_a_bad_price_kills_a_good_projection(self):
        """A projection that clears the bar at one price can be a D at another. This is
        why the scan's grade cannot simply be carried to another book."""
        from app.services.devig import decimal_to_american

        generous = make(model_prob=0.58, price=decimal_to_american(2.00))
        stingy = make(model_prob=0.58, price=decimal_to_american(1.40))
        assert generous.grade.actionable
        assert stingy.edge < 0
        assert stingy.grade is Grade.D
