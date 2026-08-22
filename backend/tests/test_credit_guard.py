"""The credit guard is what keeps a 500-credit/month free tier from running dry."""

import pytest

from app.config import Settings


class TestCreditCost:
    def test_cost_is_markets_times_regions(self):
        s = Settings(odds_regions="us", odds_markets_mlb="h2h,totals",
                     odds_markets_nba="h2h,totals,spreads")
        assert s.credit_cost("baseball_mlb") == 2
        assert s.credit_cost("basketball_nba") == 3

    def test_two_regions_doubles_the_cost(self):
        s = Settings(odds_regions="us,uk", odds_markets_mlb="h2h,totals")
        assert s.credit_cost("baseball_mlb") == 4

    def test_planned_budget_fits_the_free_tier(self):
        """3 polls/day of each sport must stay under 500 credits in a 31-day month."""
        s = Settings(odds_regions="us", odds_markets_mlb="h2h,totals",
                     odds_markets_nba="h2h,totals,spreads", odds_polls_per_day=3)
        per_day = (s.credit_cost("baseball_mlb") + s.credit_cost("basketball_nba")) * s.odds_polls_per_day
        assert per_day == 15
        assert per_day * 31 == 465
        assert per_day * 31 < 500

    def test_mlb_only_period_is_far_under_budget(self):
        s = Settings(odds_regions="us", odds_markets_mlb="h2h,totals", odds_polls_per_day=3)
        assert s.credit_cost("baseball_mlb") * 3 * 31 == 186


class TestOddsEnabled:
    def test_blank_key_disables_odds(self):
        assert Settings(the_odds_api_key="").odds_enabled is False
        assert Settings(the_odds_api_key="   ").odds_enabled is False

    def test_present_key_enables_odds(self):
        assert Settings(the_odds_api_key="abc123").odds_enabled is True


class TestGuards:
    """poll_odds must refuse in each protected situation without calling the API."""

    def _session_with(self, monkeypatch, *, remaining, upcoming):
        from app.services import ingest_odds

        monkeypatch.setattr(ingest_odds, "latest_remaining", lambda s: remaining)
        monkeypatch.setattr(ingest_odds, "_upcoming_game_count", lambda s, sp, h: upcoming)

        def boom(*a, **k):
            raise AssertionError("guard failed: the API must not be called")

        monkeypatch.setattr(ingest_odds.oddsapi, "odds", boom)
        return ingest_odds

    def test_missing_key_skips_without_calling(self, monkeypatch):
        from app.models import Sport
        from app.services import ingest_odds

        mod = self._session_with(monkeypatch, remaining=500, upcoming=10)
        monkeypatch.setattr(mod, "get_settings", lambda: Settings(the_odds_api_key=""))
        result = ingest_odds.poll_odds(object(), Sport.MLB)
        assert result.polled is False
        assert result.reason == "no_api_key"

    def test_no_upcoming_games_skips_without_calling(self, monkeypatch):
        from app.models import Sport
        from app.services import ingest_odds

        mod = self._session_with(monkeypatch, remaining=500, upcoming=0)
        monkeypatch.setattr(mod, "get_settings", lambda: Settings(the_odds_api_key="k"))
        result = ingest_odds.poll_odds(object(), Sport.NBA)
        assert result.polled is False
        assert result.reason == "no_upcoming_games"

    def test_reserve_floor_refuses_when_credits_are_low(self, monkeypatch):
        from app.models import Sport
        from app.services import ingest_odds

        mod = self._session_with(monkeypatch, remaining=51, upcoming=5)
        monkeypatch.setattr(
            mod, "get_settings",
            lambda: Settings(the_odds_api_key="k", odds_credit_reserve=50,
                             odds_markets_mlb="h2h,totals", odds_regions="us"),
        )
        # 51 remaining - 2 cost = 49, which is below the floor of 50.
        result = ingest_odds.poll_odds(object(), Sport.MLB)
        assert result.polled is False
        assert result.reason == "credit_reserve"
        assert result.credits_remaining == 51

    def test_reserve_floor_allows_when_exactly_at_the_boundary(self, monkeypatch):
        """52 - 2 == 50 is not below the floor, so this must proceed to the call."""
        from app.models import Sport
        from app.services import ingest_odds

        monkeypatch.setattr(ingest_odds, "latest_remaining", lambda s: 52)
        monkeypatch.setattr(ingest_odds, "_upcoming_game_count", lambda s, sp, h: 5)
        monkeypatch.setattr(
            ingest_odds, "get_settings",
            lambda: Settings(the_odds_api_key="k", odds_credit_reserve=50,
                             odds_markets_mlb="h2h,totals", odds_regions="us"),
        )

        called = {"n": 0}

        def fake_odds(*a, **k):
            called["n"] += 1
            raise RuntimeError("stop after the guard")

        monkeypatch.setattr(ingest_odds.oddsapi, "odds", fake_odds)
        with pytest.raises(RuntimeError):
            ingest_odds.poll_odds(object(), Sport.MLB)
        assert called["n"] == 1

    def test_no_prior_usage_does_not_block_the_first_call(self, monkeypatch):
        """A fresh install has no recorded balance; that must not be read as zero."""
        from app.models import Sport
        from app.services import ingest_odds

        monkeypatch.setattr(ingest_odds, "latest_remaining", lambda s: None)
        monkeypatch.setattr(ingest_odds, "_upcoming_game_count", lambda s, sp, h: 3)
        monkeypatch.setattr(
            ingest_odds, "get_settings", lambda: Settings(the_odds_api_key="k")
        )
        called = {"n": 0}

        def fake_odds(*a, **k):
            called["n"] += 1
            raise RuntimeError("stop after the guard")

        monkeypatch.setattr(ingest_odds.oddsapi, "odds", fake_odds)
        with pytest.raises(RuntimeError):
            ingest_odds.poll_odds(object(), Sport.MLB)
        assert called["n"] == 1

    def test_force_overrides_the_no_games_guard_but_not_the_credit_floor(self, monkeypatch):
        from app.models import Sport
        from app.services import ingest_odds

        mod = self._session_with(monkeypatch, remaining=10, upcoming=0)
        monkeypatch.setattr(
            mod, "get_settings",
            lambda: Settings(the_odds_api_key="k", odds_credit_reserve=50),
        )
        result = ingest_odds.poll_odds(object(), Sport.MLB, force=True)
        assert result.polled is False
        assert result.reason == "credit_reserve"


class TestInPlayExclusion:
    """In-play prices are a different market and must never be stored as pre-game."""

    def _event(self, home="Boston Red Sox", away="Arizona Diamondbacks"):
        return {
            "home_team": home,
            "away_team": away,
            "commence_time": "2026-08-19T20:10:00Z",
            "bookmakers": [
                {
                    "key": "betmgm",
                    "markets": [
                        {
                            "key": "totals",
                            "outcomes": [
                                {"name": "Over", "price": -110, "point": 8.5},
                                {"name": "Under", "price": -110, "point": 8.5},
                            ],
                        }
                    ],
                }
            ],
        }

    def test_started_game_is_skipped(self, monkeypatch):
        from datetime import UTC, datetime, timedelta

        from app.models import Sport
        from app.services import ingest_odds

        now = datetime.now(UTC)

        class FakeGame:
            id = 1
            external_id = "x"
            start_time = now - timedelta(hours=1)  # already under way
            is_final = False

        added = []

        class FakeSession:
            def add(self, obj):
                added.append(obj)

            def commit(self):
                pass

        monkeypatch.setattr(ingest_odds, "_match_game", lambda *a, **k: FakeGame())
        response = type(
            "R", (), {"data": [self._event()], "fetched_at": now}
        )()
        written, matched = ingest_odds._store(FakeSession(), Sport.MLB, response)

        assert written == 0
        assert matched == 0
        assert added == []

    def test_upcoming_game_is_stored(self, monkeypatch):
        from datetime import UTC, datetime, timedelta

        from app.models import Sport
        from app.services import ingest_odds

        now = datetime.now(UTC)

        class FakeGame:
            id = 1
            external_id = "x"
            start_time = now + timedelta(hours=2)
            is_final = False

        added = []

        class FakeSession:
            def add(self, obj):
                added.append(obj)

            def commit(self):
                pass

        monkeypatch.setattr(ingest_odds, "_match_game", lambda *a, **k: FakeGame())
        response = type("R", (), {"data": [self._event()], "fetched_at": now})()
        written, matched = ingest_odds._store(FakeSession(), Sport.MLB, response)

        assert written == 2
        assert matched == 1
