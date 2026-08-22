from app.services.parsing import innings_to_float, to_float, to_int


def test_innings_uses_thirds_not_decimals():
    # MLB writes thirds after the point: 5.1 == 5 1/3, not 5.1.
    assert innings_to_float("5.0") == 5.0
    assert innings_to_float("5.1") == 5 + 1 / 3
    assert innings_to_float("5.2") == 5 + 2 / 3
    assert innings_to_float("139.0") == 139.0
    assert innings_to_float("0.1") == 1 / 3


def test_innings_edge_cases():
    assert innings_to_float(None) is None
    assert innings_to_float("") is None
    assert innings_to_float("7") == 7.0


def test_to_float_handles_mlb_null_sentinels():
    assert to_float("-.--") is None
    assert to_float(".---") is None
    assert to_float("3.24") == 3.24
    assert to_float(".229") == 0.229
    assert to_float(12) == 12.0
    assert to_float(None) is None


def test_to_int():
    assert to_int("12") == 12
    assert to_int("-.--") is None


class TestGameStartedGuard:
    """Unstarted games must never carry a score.

    Both upstreams report 0-0 for fixtures that have not begun. Storing that would
    make an unplayed game indistinguishable from a scoreless one, and would feed
    zeros into the team logs that ratings are built from.
    """

    def test_mlb_pregame_has_not_started(self):
        from app.services.ingest_mlb import _has_started

        for state in ("Scheduled", "Pre-Game", "Warmup", "Postponed"):
            assert _has_started(state) is False

    def test_mlb_live_and_final_have_started(self):
        from app.services.ingest_mlb import _has_started

        for state in ("In Progress", "Final", "Game Over", "Delayed"):
            assert _has_started(state) is True
