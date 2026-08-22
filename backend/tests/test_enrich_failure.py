"""The enricher's contract: when stats.nba.com fails, ESPN data must survive untouched.

This is the guarantee that lets the optional upstream stay optional. It is asserted
here with a forced failure rather than by waiting for the real throttle, which is
intermittent.
"""

import pytest

from app.providers.http import ProviderError


class FakeRow:
    def __init__(self, off, dfn, pace):
        self.off_rating, self.def_rating, self.pace = off, dfn, pace


def test_provider_error_writes_nothing_and_preserves_espn(monkeypatch):
    from app.services import enrich_nba

    calls = {"commit": 0, "added": []}

    class FakeSession:
        def scalar(self, *a, **k):
            return None

        def scalars(self, *a, **k):
            raise AssertionError("must not query teams after the upstream failed")

        def add(self, obj):
            calls["added"].append(obj)

        def commit(self):
            calls["commit"] += 1

    monkeypatch.setattr(
        enrich_nba.nba_stats,
        "league_dash_team_stats",
        lambda *a, **k: (_ for _ in ()).throw(ProviderError("throttled: 0-byte body")),
    )
    monkeypatch.setattr(
        enrich_nba, "get_settings", lambda: type("S", (), {"enable_nba_stats_enrich": True})()
    )

    written = enrich_nba.enrich_team_ratings(FakeSession(), 2026)

    assert written == 0
    # Only the audit row is written -- never a stats row.
    assert all(type(o).__name__ == "IngestRun" for o in calls["added"])
    assert len(calls["added"]) == 1
    assert calls["added"][0].ok is False


def test_empty_table_is_treated_as_failure_not_as_zero_stats(monkeypatch):
    from app.services import enrich_nba

    added = []

    class FakeSession:
        def scalar(self, *a, **k):
            return None

        def scalars(self, *a, **k):
            raise AssertionError("must not proceed on an empty table")

        def add(self, obj):
            added.append(obj)

        def commit(self):
            pass

    monkeypatch.setattr(enrich_nba.nba_stats, "league_dash_team_stats", lambda *a, **k: [])
    monkeypatch.setattr(
        enrich_nba, "get_settings", lambda: type("S", (), {"enable_nba_stats_enrich": True})()
    )

    assert enrich_nba.enrich_team_ratings(FakeSession(), 2026) == 0
    assert added and added[0].ok is False


def test_disabled_flag_skips_entirely(monkeypatch):
    from app.services import enrich_nba

    def boom(*a, **k):
        raise AssertionError("must not call the upstream when disabled")

    monkeypatch.setattr(enrich_nba.nba_stats, "league_dash_team_stats", boom)
    monkeypatch.setattr(
        enrich_nba, "get_settings", lambda: type("S", (), {"enable_nba_stats_enrich": False})()
    )

    assert enrich_nba.enrich_team_ratings(object(), 2026) == 0


def test_partial_row_is_rejected(monkeypatch):
    """A row missing any of the three ratings must not be written at all."""
    from app.services import enrich_nba

    added = []

    class FakeTeam:
        id, abbrev, display_name = 1, "OKC", "Oklahoma City Thunder"

    class FakeSession:
        def scalar(self, *a, **k):
            return None

        def scalars(self, *a, **k):
            return type("R", (), {"all": lambda self: [FakeTeam()]})()

        def add(self, obj):
            added.append(obj)

        def commit(self):
            pass

    monkeypatch.setattr(
        enrich_nba.nba_stats,
        "league_dash_team_stats",
        lambda *a, **k: [
            {"TEAM_ID": 1, "TEAM_NAME": "Oklahoma City Thunder",
             "OFF_RATING": 117.6, "DEF_RATING": None, "PACE": 99.0, "GP": 82}
        ],
    )
    monkeypatch.setattr(
        enrich_nba, "get_settings", lambda: type("S", (), {"enable_nba_stats_enrich": True})()
    )

    assert enrich_nba.enrich_team_ratings(FakeSession(), 2026) == 0
    assert all(type(o).__name__ == "IngestRun" for o in added)
