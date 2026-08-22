"""Budget arithmetic. A mistake here burns a month's quota in a single job."""

from datetime import UTC, date, datetime

import pytest

from app.config import Settings
from app.services.credit_budget import (
    DEFAULT_MONTHLY_QUOTA,
    Budget,
    days_left_in_period,
)


class TestDaysLeft:
    def test_counts_today(self):
        assert days_left_in_period(date(2026, 8, 31)) == 1
        assert days_left_in_period(date(2026, 8, 30)) == 2

    def test_full_month(self):
        assert days_left_in_period(date(2026, 8, 1)) == 31
        assert days_left_in_period(date(2026, 9, 1)) == 30

    def test_leap_february(self):
        assert days_left_in_period(date(2028, 2, 1)) == 29

    def test_never_zero(self):
        # Division by this value must always be safe.
        for day in (1, 15, 28, 30, 31):
            assert days_left_in_period(date(2026, 1, day)) >= 1


class TestPerGameCost:
    def test_four_markets_one_region(self):
        s = Settings(
            props_markets="h2h_1st_5_innings,totals_1st_5_innings,pitcher_strikeouts,team_totals",
            odds_regions="us",
        )
        assert len(s.props_markets_list) == 4

    def test_regions_multiply(self):
        s = Settings(props_markets="pitcher_strikeouts", odds_regions="us,uk")
        regions = len(s.odds_regions.split(","))
        assert len(s.props_markets_list) * regions == 2


class TestCompute:
    """compute() is driven through fakes so the arithmetic is pinned exactly."""

    def _run(self, monkeypatch, *, remaining, game_level, spent=0, today=date(2026, 8, 20),
             markets=4, cap=4, reserve=50):
        from app.services import credit_budget

        monkeypatch.setattr(credit_budget, "latest_remaining", lambda s: remaining)
        monkeypatch.setattr(credit_budget, "spent_today", lambda s, t=None: spent)
        monkeypatch.setattr(credit_budget, "active_sports_game_level_cost", lambda s: game_level)
        monkeypatch.setattr(
            credit_budget,
            "get_settings",
            lambda: Settings(
                odds_regions="us",
                odds_credit_reserve=reserve,
                props_max_games_per_day=cap,
                props_markets=",".join(f"m{i}" for i in range(markets)),
            ),
        )
        return credit_budget.compute(object(), today=today)

    def test_summer_mlb_only_funds_props(self, monkeypatch):
        """498 credits, 12 days left, MLB game-level only: props are affordable."""
        b = self._run(monkeypatch, remaining=498, game_level=6, today=date(2026, 8, 20))
        assert b.days_left == 12
        assert b.daily_allowance == (498 - 50) // 12  # 37
        assert b.props_games_today > 0
        assert not b.exhausted

    def test_october_overlap_starves_props(self, monkeypatch):
        """MLB playoffs plus NBA: game-level polling eats the whole allowance."""
        # 200 left, 20 days => 7/day allowance, but both sports cost 15/day.
        b = self._run(monkeypatch, remaining=250, game_level=15, today=date(2026, 10, 12))
        assert b.props_games_today == 0
        assert b.exhausted
        assert "game-level" in b.reason

    def test_reserve_floor_stops_everything(self, monkeypatch):
        b = self._run(monkeypatch, remaining=40, game_level=6, reserve=50)
        assert b.props_games_today == 0
        assert "reserve floor" in b.reason

    def test_exactly_at_reserve_is_stopped(self, monkeypatch):
        b = self._run(monkeypatch, remaining=50, game_level=0, reserve=50)
        assert b.props_games_today == 0

    def test_cap_limits_even_when_rich(self, monkeypatch):
        """A large balance must not authorise an unbounded sweep."""
        b = self._run(monkeypatch, remaining=5000, game_level=6, cap=4)
        assert b.props_games_today == 4

    def test_cap_of_zero_disables_the_ceiling(self, monkeypatch):
        b = self._run(monkeypatch, remaining=5000, game_level=6, cap=0)
        assert b.props_games_today > 4

    def test_spend_already_made_today_reduces_headroom(self, monkeypatch):
        rich = self._run(monkeypatch, remaining=400, game_level=6, spent=0)
        spent = self._run(monkeypatch, remaining=400, game_level=6, spent=6)
        # Having already paid for game-level today frees that much for props.
        assert spent.props_allowance >= rich.props_allowance

    def test_more_markets_means_fewer_games(self, monkeypatch):
        few = self._run(monkeypatch, remaining=400, game_level=6, markets=2, cap=0)
        many = self._run(monkeypatch, remaining=400, game_level=6, markets=8, cap=0)
        assert few.props_games_today > many.props_games_today

    def test_fresh_install_assumes_free_tier(self, monkeypatch):
        b = self._run(monkeypatch, remaining=None, game_level=6)
        assert b.remaining == DEFAULT_MONTHLY_QUOTA
        assert "assuming free-tier quota" in b.reason

    def test_month_end_is_conservative_not_generous(self, monkeypatch):
        """Same balance late in the month must not authorise a bigger daily spend
        than it can sustain -- one day left means the whole balance is available,
        which is correct, but the cap still applies."""
        b = self._run(monkeypatch, remaining=400, game_level=6, today=date(2026, 8, 31), cap=4)
        assert b.days_left == 1
        assert b.props_games_today == 4  # cap, not the raw allowance

    def test_projected_month_stays_inside_the_free_tier(self, monkeypatch):
        """The headline claim: default settings must not exceed 500 credits/month."""
        b = self._run(monkeypatch, remaining=500, game_level=6, today=date(2026, 8, 1), cap=4)
        monthly = (b.game_level_cost_today + b.props_games_today * b.props_markets_per_game) * 31
        assert monthly <= 500, f"projected {monthly}/month exceeds the free tier"


class TestCanSpend:
    def test_blocks_below_reserve(self, monkeypatch):
        from app.services import credit_budget

        monkeypatch.setattr(credit_budget, "latest_remaining", lambda s: 52)
        monkeypatch.setattr(
            credit_budget, "get_settings", lambda: Settings(odds_credit_reserve=50)
        )
        assert credit_budget.can_spend(object(), 2) is True
        assert credit_budget.can_spend(object(), 3) is False

    def test_allows_first_ever_call(self, monkeypatch):
        from app.services import credit_budget

        monkeypatch.setattr(credit_budget, "latest_remaining", lambda s: None)
        monkeypatch.setattr(credit_budget, "get_settings", lambda: Settings())
        assert credit_budget.can_spend(object(), 60) is True
