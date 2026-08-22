"""The combined gate: four checks collapsed into one grade.

The grade is only trustworthy if it cannot silently pass something that fails a check,
so these tests pin each condition independently and then the conjunction.
"""

import pytest

from app.services.mismatches import (
    MIN_GATE_BOOKS,
    MIN_GATE_EV,
    MIN_GATE_SCORE,
    Mismatch,
    band_for,
)
from app.services.rotations import TeamStrength, Tier


def _team(abbrev="AAA", rank=1, tier=Tier.GOOD, pyth=0.600):
    return TeamStrength(
        team_id=1, abbrev=abbrev, name=abbrev, pythagorean=pyth,
        runs_per_game=5.0, runs_allowed_per_game=4.0, games_played=100,
        rank=rank, tier=tier,
    )


def make(**kwargs) -> Mismatch:
    """A mismatch that clears every check, unless overridden."""
    from datetime import UTC, datetime

    defaults = dict(
        game_id=1,
        start_time=datetime.now(UTC),
        favourite_abbrev="AAA",
        underdog_abbrev="BBB",
        favourite_is_home=True,
        favourite_team=_team(),
        underdog_team=_team("BBB", 30, Tier.BAD, 0.400),
        favourite_pitcher=None,
        underdog_pitcher=None,
        score=55.0,
        team_gap=0.2,
        era_gap=2.0,
        strict=True,
        model_win_prob=0.70,
        market_fair_prob=0.66,
        best_american=-150,      # break-even 60.0%
        best_book="bookA",
        ev=0.02,
        model_ev=0.05,
        kelly_quarter=0.01,
        book_count=6,
        band_label="50+",
        band_win_rate=0.688,     # comfortably above the 60% break-even
        band_break_even=-220,
        band_sample=80,
    )
    defaults.update(kwargs)
    return Mismatch(**defaults)


class TestBandBoundaries:
    def test_bands_partition_the_range(self):
        assert band_for(0) == "0-20"
        assert band_for(19.9) == "0-20"
        assert band_for(20) == "20-35"
        assert band_for(34.9) == "20-35"
        assert band_for(35) == "35-50"
        assert band_for(49.9) == "35-50"
        assert band_for(50) == "50+"
        assert band_for(100) == "50+"


class TestBreakEven:
    def test_negative_odds(self):
        # -185 needs 185/285 = 64.9%
        assert make(best_american=-185).break_even_prob == pytest.approx(0.6491, abs=1e-4)

    def test_positive_odds(self):
        # +120 needs 100/220 = 45.5%
        assert make(best_american=120).break_even_prob == pytest.approx(0.4545, abs=1e-4)

    def test_even_money(self):
        assert make(best_american=100).break_even_prob == pytest.approx(0.5)

    def test_unpriced(self):
        assert make(best_american=None, ev=None).break_even_prob is None


class TestGate:
    def test_all_checks_pass_is_a_bet(self):
        m = make()
        assert m.passed_checks == 4
        assert m.is_good_bet is True
        assert m.grade == "bet"
        assert m.blocking_reason is None

    def test_low_score_blocks(self):
        m = make(score=MIN_GATE_SCORE - 1, band_label="20-35")
        assert m.is_good_bet is False
        assert m.blocking_reason.startswith("Lopsided enough")

    def test_negative_ev_blocks_even_when_everything_else_passes(self):
        """The real-world case: lopsided, historically supported, but overpriced."""
        m = make(ev=-0.03)
        assert m.passed_checks == 3
        assert m.grade == "near miss"
        assert m.blocking_reason.startswith("Beats the market")

    def test_ev_exactly_at_threshold_passes(self):
        assert make(ev=MIN_GATE_EV).is_good_bet is True

    def test_ev_just_below_threshold_fails(self):
        assert make(ev=MIN_GATE_EV - 0.0001).is_good_bet is False

    def test_price_worse_than_band_history_blocks(self):
        # Band won 55%, but the price demands 60% to break even.
        m = make(band_win_rate=0.55, best_american=-150)
        assert m.is_good_bet is False
        assert m.blocking_reason.startswith("Price beats its band")

    def test_too_few_books_blocks(self):
        m = make(book_count=MIN_GATE_BOOKS - 1)
        assert m.is_good_bet is False
        assert m.blocking_reason.startswith("Enough books")

    def test_unpriced_is_graded_unpriced_not_pass(self):
        m = make(ev=None, best_american=None, market_fair_prob=None, book_count=0)
        assert m.grade == "unpriced"
        assert m.is_good_bet is False

    def test_missing_history_does_not_silently_pass(self):
        """No band history must fail the check, never skip it."""
        m = make(band_win_rate=None, band_break_even=None)
        assert m.is_good_bet is False
        assert any(c.key == "history" and not c.passed for c in m.checks)

    def test_checks_are_always_exposed_with_the_grade(self):
        m = make()
        keys = [c.key for c in m.checks]
        assert keys == ["lopsided", "value", "history", "books"]
        assert all(c.detail for c in m.checks)

    def test_two_failures_grade_as_pass_not_near_miss(self):
        m = make(ev=-0.03, book_count=1)
        assert m.passed_checks == 2
        assert m.grade == "pass"
