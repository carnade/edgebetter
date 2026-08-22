import math

import pytest

from app.services.devig import (
    BookPrice,
    american_to_decimal,
    american_to_implied,
    consensus,
    decimal_to_american,
    expected_value,
    kelly_fraction,
    multiplicative_devig,
    no_vig_fair_american,
    normal_cdf,
    overround,
    power_devig,
    prob_over,
    quarter_kelly,
)


class TestOddsConversion:
    def test_known_american_to_decimal(self):
        # Textbook values.
        assert american_to_decimal(100) == pytest.approx(2.0)
        assert american_to_decimal(-110) == pytest.approx(1.909090909)
        assert american_to_decimal(150) == pytest.approx(2.5)
        assert american_to_decimal(-200) == pytest.approx(1.5)

    def test_implied_probability(self):
        assert american_to_implied(100) == pytest.approx(0.5)
        assert american_to_implied(-110) == pytest.approx(0.5238095, abs=1e-6)
        assert american_to_implied(-200) == pytest.approx(2 / 3)

    def test_round_trips(self):
        for a in (-500, -200, -110, 100, 150, 300, 1000):
            assert decimal_to_american(american_to_decimal(a)) == a

    def test_zero_is_not_a_price(self):
        with pytest.raises(ValueError):
            american_to_decimal(0)


class TestDevig:
    def test_standard_minus_110_market_has_about_4_8_percent_vig(self):
        implied = [american_to_implied(-110), american_to_implied(-110)]
        assert overround(implied) == pytest.approx(0.047619, abs=1e-5)

    def test_multiplicative_devig_sums_to_one(self):
        for market in (
            [american_to_implied(-110), american_to_implied(-110)],
            [american_to_implied(-250), american_to_implied(200)],
            [american_to_implied(-140), american_to_implied(120)],
        ):
            assert sum(multiplicative_devig(market)) == pytest.approx(1.0)

    def test_symmetric_market_devigs_to_even(self):
        fair = multiplicative_devig([american_to_implied(-110), american_to_implied(-110)])
        assert fair[0] == pytest.approx(0.5)
        assert fair[1] == pytest.approx(0.5)

    def test_devig_preserves_favourite_ordering(self):
        fair = multiplicative_devig([american_to_implied(-250), american_to_implied(200)])
        assert fair[0] > fair[1]
        assert fair[0] == pytest.approx(0.6818, abs=1e-3)

    def test_three_way_market_sums_to_one(self):
        implied = [american_to_implied(x) for x in (150, 250, -120)]
        assert sum(multiplicative_devig(implied)) == pytest.approx(1.0)

    def test_power_devig_also_sums_to_one(self):
        implied = [american_to_implied(-250), american_to_implied(200)]
        assert sum(power_devig(implied)) == pytest.approx(1.0, abs=1e-6)

    def test_power_devig_shrinks_longshot_more_than_multiplicative(self):
        implied = [american_to_implied(-400), american_to_implied(320)]
        mult = multiplicative_devig(implied)
        powr = power_devig(implied)
        # The longshot is the second outcome; power devig should price it lower.
        assert powr[1] < mult[1]

    def test_no_vig_fair_american_on_symmetric_market(self):
        fair = no_vig_fair_american([american_to_implied(-110), american_to_implied(-110)])
        assert fair == [100, 100]

    def test_rejects_nonpositive(self):
        with pytest.raises(ValueError):
            multiplicative_devig([0.0, 0.5])


class TestConsensus:
    def _market(self, quotes):
        return [
            BookPrice(bookmaker=b, outcome=o, american=a)
            for b, o, a in quotes
        ]

    def test_median_across_books(self):
        prices = self._market([
            ("bookA", "Yankees", -150), ("bookA", "Red Sox", 130),
            ("bookB", "Yankees", -155), ("bookB", "Red Sox", 135),
            ("bookC", "Yankees", -145), ("bookC", "Red Sox", 125),
        ])
        result = {c.outcome: c for c in consensus(prices)}
        assert result["Yankees"].book_count == 3
        assert result["Yankees"].fair_prob + result["Red Sox"].fair_prob == pytest.approx(1.0, abs=1e-6)
        # Best price for the underdog is the biggest plus number.
        assert result["Red Sox"].best_american == 135
        assert result["Red Sox"].best_book == "bookB"

    def test_best_price_is_highest_decimal_for_favourite(self):
        prices = self._market([
            ("bookA", "Lakers", -200), ("bookA", "Celtics", 170),
            ("bookB", "Lakers", -180), ("bookB", "Celtics", 155),
        ])
        result = {c.outcome: c for c in consensus(prices)}
        # -180 pays better than -200.
        assert result["Lakers"].best_american == -180
        assert result["Lakers"].best_book == "bookB"

    def test_one_outlier_book_does_not_drag_consensus(self):
        tight = [("book%d" % i, o, a) for i in range(4) for o, a in (("Over", -110), ("Under", -110))]
        outlier = [("stale", "Over", -400), ("stale", "Under", 320)]
        result = {c.outcome: c for c in consensus(self._market(tight + outlier))}
        # Median of five books, four of which are even, stays at even money.
        assert result["Over"].fair_prob == pytest.approx(0.5, abs=1e-6)

    def test_incomplete_market_at_a_book_is_skipped(self):
        prices = self._market([
            ("bookA", "Over", -110), ("bookA", "Under", -110),
            ("bookB", "Over", -105),  # no Under quoted
        ])
        result = {c.outcome: c for c in consensus(prices)}
        # Only bookA could be devigged, but bookB's price still counts for line shopping.
        assert result["Over"].book_count == 1
        assert result["Over"].best_american == -105

    def test_empty_input(self):
        assert consensus([]) == []


class TestExpectedValueAndKelly:
    def test_ev_is_zero_at_fair_odds(self):
        assert expected_value(0.5, 2.0) == pytest.approx(0.0)
        assert expected_value(0.25, 4.0) == pytest.approx(0.0)

    def test_ev_positive_when_price_beats_fair(self):
        # True 50%, getting +120.
        assert expected_value(0.5, american_to_decimal(120)) == pytest.approx(0.1)

    def test_ev_negative_when_price_is_worse_than_fair(self):
        assert expected_value(0.5, american_to_decimal(-120)) < 0

    def test_kelly_zero_at_fair_odds(self):
        assert kelly_fraction(0.5, 2.0) == pytest.approx(0.0)

    def test_kelly_negative_on_bad_bet(self):
        assert kelly_fraction(0.4, 2.0) < 0

    def test_kelly_known_value(self):
        # p=0.6 at even money: f* = (1*0.6 - 0.4)/1 = 0.2
        assert kelly_fraction(0.6, 2.0) == pytest.approx(0.2)

    def test_quarter_kelly_is_a_quarter_and_never_negative(self):
        assert quarter_kelly(0.6, 2.0) == pytest.approx(0.05)
        assert quarter_kelly(0.4, 2.0) == 0.0

    def test_kelly_scales_with_edge(self):
        assert kelly_fraction(0.7, 2.0) > kelly_fraction(0.6, 2.0)


class TestDistributions:
    def test_normal_cdf_center_and_tails(self):
        assert normal_cdf(0.0) == pytest.approx(0.5)
        assert normal_cdf(1.96) == pytest.approx(0.975, abs=1e-3)
        assert normal_cdf(-1.96) == pytest.approx(0.025, abs=1e-3)

    def test_prob_over_at_the_line_is_half(self):
        assert prob_over(220.0, mu=220.0, sigma=11.5) == pytest.approx(0.5)

    def test_prob_over_moves_the_right_way(self):
        assert prob_over(210.0, mu=220.0, sigma=11.5) > 0.5
        assert prob_over(230.0, mu=220.0, sigma=11.5) < 0.5

    def test_prob_over_is_monotonic_in_line(self):
        probs = [prob_over(x, 220.0, 11.5) for x in range(200, 241, 5)]
        assert all(a > b for a, b in zip(probs, probs[1:]))

    def test_sigma_must_be_positive(self):
        with pytest.raises(ValueError):
            normal_cdf(1.0, 0.0, 0.0)


class TestOutlierQuarantine:
    """One inverted book must not become the 'best price'.

    Drawn from a real F5 moneyline: seven books had Houston -175 to -186 while betmgm
    reported +135 with the sides inverted. Median devig was unaffected, but best-price
    selection grabbed the bad number and reported a +43% edge.
    """

    def _houston_market(self):
        quotes = [
            ("betonlineag", -175, 153), ("betrivers", -186, 138), ("betus", -175, 155),
            ("bovada", -185, 140), ("draftkings", -175, 135), ("fanduel", -182, 144),
            ("mybookieag", -180, 145),
            ("betmgm", 135, -170),  # inverted
        ]
        prices = []
        for book, hou, laa in quotes:
            prices.append(BookPrice(bookmaker=book, outcome="Houston Astros", american=hou))
            prices.append(BookPrice(bookmaker=book, outcome="Los Angeles Angels", american=laa))
        return prices

    def test_inverted_book_is_not_chosen_as_best_price(self):
        result = {c.outcome: c for c in consensus(self._houston_market())}
        hou = result["Houston Astros"]
        assert hou.best_book != "betmgm"
        assert hou.best_american < 0  # still priced as the favourite
        assert "betmgm" in hou.outliers

    def test_edge_collapses_to_something_realistic(self):
        from app.services.devig import expected_value

        hou = {c.outcome: c for c in consensus(self._houston_market())}["Houston Astros"]
        ev = expected_value(hou.fair_prob, hou.best_decimal)
        # The bogus number was +43%; a real line-shopping edge is small.
        assert -0.10 < ev < 0.05

    def test_normal_market_keeps_every_book(self):
        prices = [
            BookPrice(bookmaker=f"book{i}", outcome=o, american=a)
            for i in range(5)
            for o, a in (("Over", -110), ("Under", -110))
        ]
        for c in consensus(prices):
            assert c.outliers == ()

    def test_mild_disagreement_is_not_quarantined(self):
        """Books legitimately differ; only contradictions are discarded."""
        prices = []
        for book, over in (("a", -110), ("b", -105), ("c", -115), ("d", 100)):
            prices.append(BookPrice(bookmaker=book, outcome="Over", american=over))
            prices.append(BookPrice(bookmaker=book, outcome="Under", american=-110))
        result = {c.outcome: c for c in consensus(prices)}
        assert result["Over"].outliers == ()
        assert result["Over"].best_american == 100  # genuine best price survives

    def test_all_books_outlying_falls_back_rather_than_returning_nothing(self):
        # Two books, wildly apart: neither is "the consensus", so keep both.
        prices = [
            BookPrice(bookmaker="a", outcome="X", american=-400),
            BookPrice(bookmaker="a", outcome="Y", american=320),
            BookPrice(bookmaker="b", outcome="X", american=300),
            BookPrice(bookmaker="b", outcome="Y", american=-380),
        ]
        result = {c.outcome: c for c in consensus(prices)}
        assert result["X"].best_book in {"a", "b"}
