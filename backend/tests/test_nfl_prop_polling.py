"""Which games the weekly prop poll targets.

Two failures this guards against, both silent:

  - A slate polled one game short. The missing game simply has no lines, so the scan
    shows fewer rows and looks complete.
  - The same week bought twice. 80 credits a week is affordable exactly once.

An NFL week runs Thursday to Monday and can open on a Wednesday, so no rolling date
window anchored to a weekday covers one cleanly. The job targets a week instead.
"""

from datetime import date, timedelta

from app.services.ingest_nfl_props import next_unplayed_week


class _Row(tuple):
    """Stands in for the (season, week, gameday) row the query returns."""


class _FakeSession:
    def __init__(self, row):
        self._row = row

    def execute(self, _stmt):
        row = self._row
        return type("R", (), {"first": staticmethod(lambda: row)})()


def _session(days_out: int | None, *, season=2026, week=1):
    if days_out is None:
        return _FakeSession(None)
    return _FakeSession(
        _Row((season, week, date.today() + timedelta(days=days_out)))
    )


class TestNextUnplayedWeek:
    def test_returns_the_week_when_it_starts_soon(self):
        assert next_unplayed_week(_session(2)) == (2026, 1)

    def test_polls_a_week_starting_today(self):
        assert next_unplayed_week(_session(0)) == (2026, 1)

    def test_skips_a_week_that_is_still_far_off(self):
        """The whole preseason counts week 1 as 'next'. Without this the weekly job would
        buy the same slate every Tuesday from August, at 80 credits a time, for markets
        most books have not posted yet."""
        assert next_unplayed_week(_session(14)) is None

    def test_boundary_is_inclusive(self):
        assert next_unplayed_week(_session(6)) is not None
        assert next_unplayed_week(_session(7)) is None

    def test_lead_time_is_adjustable(self):
        assert next_unplayed_week(_session(10), within_days=14) == (2026, 1)

    def test_no_unplayed_games_means_nothing_to_poll(self):
        """The offseason, and the end of the season, must cost nothing."""
        assert next_unplayed_week(_session(None)) is None
