"""Projection maths. Distribution properties are asserted against hand-computed values."""

import math

import pytest

from app.services.projections_mlb import (
    AWAY_RUN_MULTIPLIER,
    HOME_RUN_MULTIPLIER,
    LEAGUE_ERA,
    PitcherInput,
    prob_home_wins,
    prob_total_over,
    project_runs,
    total_distribution,
)
from app.services.projections_nba import (
    HOME_ADVANTAGE,
    TOTAL_SIGMA,
    expected_possessions,
    project_from_ratings,
)
from app.services.ratings import TeamRating


def rating(off, dfn, pace, gp=82, weight=1.0):
    from app.models import StatSource

    return TeamRating(
        team_id=1, season=2026, games_played=gp, off_rating=off,
        def_rating=dfn, pace=pace, source=StatSource.ESPN, current_weight=weight,
    )


class TestNbaProjection:
    def test_two_average_teams_project_to_league_average(self):
        avg = rating(113.0, 113.0, 100.0)
        p = project_from_ratings(avg, avg, 113.0, 100.0)
        # 100 possessions at 113 per 100 is 113 points a side, less the bias correction.
        assert p.possessions == pytest.approx(100.0)
        assert p.total == pytest.approx(226.0 - 1.21, abs=0.01)
        assert p.margin == pytest.approx(HOME_ADVANTAGE - 0.67, abs=0.01)

    def test_home_team_is_favoured_between_equals(self):
        avg = rating(113.0, 113.0, 100.0)
        assert project_from_ratings(avg, avg, 113.0, 100.0).margin > 0

    def test_better_offence_raises_the_total(self):
        avg = rating(113.0, 113.0, 100.0)
        good = rating(120.0, 113.0, 100.0)
        assert project_from_ratings(good, avg, 113.0, 100.0).total > project_from_ratings(
            avg, avg, 113.0, 100.0
        ).total

    def test_better_defence_lowers_the_total(self):
        avg = rating(113.0, 113.0, 100.0)
        stingy = rating(113.0, 104.0, 100.0)
        assert project_from_ratings(stingy, avg, 113.0, 100.0).total < project_from_ratings(
            avg, avg, 113.0, 100.0
        ).total

    def test_faster_teams_produce_more_possessions_and_points(self):
        fast = rating(113.0, 113.0, 106.0)
        avg = rating(113.0, 113.0, 100.0)
        fast_game = project_from_ratings(fast, fast, 113.0, 100.0)
        slow_game = project_from_ratings(avg, avg, 113.0, 100.0)
        assert fast_game.possessions > slow_game.possessions
        assert fast_game.total > slow_game.total

    def test_expected_possessions_formula(self):
        assert expected_possessions(100.0, 100.0, 100.0) == pytest.approx(100.0)
        assert expected_possessions(104.0, 98.0, 100.0) == pytest.approx(101.92)

    def test_expected_possessions_rejects_bad_league_pace(self):
        with pytest.raises(ValueError):
            expected_possessions(100.0, 100.0, 0.0)

    def test_prob_over_is_half_at_the_projected_total(self):
        avg = rating(113.0, 113.0, 100.0)
        p = project_from_ratings(avg, avg, 113.0, 100.0)
        assert p.prob_over(p.total) == pytest.approx(0.5)

    def test_sigma_is_the_measured_value_not_the_folklore_one(self):
        # The walk-forward backtest over 1075 games measured forecast RMSE at 19.3.
        # The commonly quoted 11-13 makes probabilities badly overconfident.
        assert TOTAL_SIGMA > 15.0

    def test_blended_flag_propagates_from_ratings(self):
        early = rating(113.0, 113.0, 100.0, gp=5, weight=0.33)
        full = rating(113.0, 113.0, 100.0)
        assert project_from_ratings(early, full, 113.0, 100.0).blended is True
        assert project_from_ratings(full, full, 113.0, 100.0).blended is False


class TestPoisson:
    def test_distribution_sums_to_one(self):
        assert sum(total_distribution(4.5, 4.5)) == pytest.approx(1.0, abs=1e-9)

    def test_sum_of_poissons_matches_closed_form(self):
        """Sum of independent Poissons is Poisson with the summed rate."""
        dist = total_distribution(4.0, 5.0)
        for k in (0, 5, 9, 12):
            expected = math.exp(-9.0 + k * math.log(9.0) - math.lgamma(k + 1))
            assert dist[k] == pytest.approx(expected, abs=1e-9)

    def test_prob_over_hand_computed(self):
        # Two 4.5-run teams: total is Poisson(9). P(total > 8.5) = P(X >= 9).
        expected = sum(
            math.exp(-9.0 + k * math.log(9.0) - math.lgamma(k + 1)) for k in range(9, 60)
        )
        assert prob_total_over(4.5, 4.5, 8.5) == pytest.approx(expected, abs=1e-6)

    def test_prob_over_decreases_with_the_line(self):
        probs = [prob_total_over(4.5, 4.5, line) for line in (6.5, 7.5, 8.5, 9.5, 10.5)]
        assert all(a > b for a, b in zip(probs, probs[1:]))

    def test_equal_teams_are_a_coin_flip(self):
        assert prob_home_wins(4.5, 4.5) == pytest.approx(0.5)

    def test_stronger_team_wins_more(self):
        assert prob_home_wins(5.5, 4.0) > 0.5
        assert prob_home_wins(4.0, 5.5) < 0.5

    def test_win_probability_excludes_ties(self):
        # Baseball has no draws, so the two sides must sum to exactly 1.
        assert prob_home_wins(5.0, 4.0) + prob_home_wins(4.0, 5.0) == pytest.approx(1.0)


class TestMlbProjection:
    def _avg_pitcher(self):
        return PitcherInput(name="avg", era=LEAGUE_ERA, innings_pitched=150.0, innings_per_start=6.0)

    def test_average_everything_returns_league_average(self):
        """Average teams and average pitchers reproduce the league run rate, split by HFA."""
        p = self._avg_pitcher()
        home, away = project_runs(
            home_offense_rpg=4.5, away_offense_rpg=4.5,
            home_pitcher=p, away_pitcher=p,
            home_team_era=LEAGUE_ERA, away_team_era=LEAGUE_ERA, league_rpg=4.5,
        )
        # HFA multipliers are calibrated to the measured 0.519 home win rate.
        assert home == pytest.approx(4.5 * HOME_RUN_MULTIPLIER)
        assert away == pytest.approx(4.5 * AWAY_RUN_MULTIPLIER)
        assert home + away == pytest.approx(9.0)

    def test_pitcher_index_is_damped_toward_league_average(self):
        """An extreme ERA must not translate one-for-one into run suppression."""
        from app.services.projections_mlb import PITCHER_INDEX_DAMPING, _runs_allowed_index

        ace = PitcherInput(name="ace", era=1.50, innings_pitched=200.0, innings_per_start=6.0)
        undamped_gap = abs(_runs_allowed_index(ace, LEAGUE_ERA) - 1.0)
        # With damping < 1 the index sits closer to league average than the raw ratio.
        assert 0 < PITCHER_INDEX_DAMPING < 1
        assert undamped_gap < 0.5

    def test_ace_suppresses_the_opposing_offence(self):
        ace = PitcherInput(name="ace", era=2.10, innings_pitched=150.0, innings_per_start=6.5)
        avg = self._avg_pitcher()
        _, away_vs_ace = project_runs(
            home_offense_rpg=4.5, away_offense_rpg=4.5,
            home_pitcher=ace, away_pitcher=avg,
            home_team_era=LEAGUE_ERA, away_team_era=LEAGUE_ERA, league_rpg=4.5,
        )
        _, away_vs_avg = project_runs(
            home_offense_rpg=4.5, away_offense_rpg=4.5,
            home_pitcher=avg, away_pitcher=avg,
            home_team_era=LEAGUE_ERA, away_team_era=LEAGUE_ERA, league_rpg=4.5,
        )
        assert away_vs_ace < away_vs_avg

    def test_era_is_regressed_by_innings(self):
        """A tiny sample must be pulled hard toward league average."""
        small = PitcherInput(name="x", era=1.00, innings_pitched=10.0, innings_per_start=5.0)
        large = PitcherInput(name="y", era=1.00, innings_pitched=200.0, innings_per_start=6.0)
        assert small.regressed_era > large.regressed_era
        assert small.regressed_era > 3.0  # heavily regressed
        assert large.regressed_era < 2.0  # mostly trusted

    def test_missing_pitcher_falls_back_to_league_era(self):
        unknown = PitcherInput(name="?", era=None, innings_pitched=None, innings_per_start=None)
        assert unknown.regressed_era == LEAGUE_ERA

    def test_no_announced_starter_still_projects(self):
        home, away = project_runs(
            home_offense_rpg=4.5, away_offense_rpg=4.5,
            home_pitcher=None, away_pitcher=None,
            home_team_era=LEAGUE_ERA, away_team_era=LEAGUE_ERA, league_rpg=4.5,
        )
        assert home > 0 and away > 0

    def test_short_outing_gives_the_bullpen_more_weight(self):
        """With a bad bullpen, a shorter start concedes more runs."""
        short = PitcherInput(name="s", era=2.00, innings_pitched=150.0, innings_per_start=5.0)
        long_start = PitcherInput(name="l", era=2.00, innings_pitched=150.0, innings_per_start=7.0)
        _, away_short = project_runs(
            home_offense_rpg=4.5, away_offense_rpg=4.5, home_pitcher=short, away_pitcher=None,
            home_team_era=5.50, away_team_era=LEAGUE_ERA, league_rpg=4.5,
        )
        _, away_long = project_runs(
            home_offense_rpg=4.5, away_offense_rpg=4.5, home_pitcher=long_start, away_pitcher=None,
            home_team_era=5.50, away_team_era=LEAGUE_ERA, league_rpg=4.5,
        )
        assert away_short > away_long

    def test_strong_offence_scores_more(self):
        p = self._avg_pitcher()
        strong, _ = project_runs(
            home_offense_rpg=5.5, away_offense_rpg=4.5, home_pitcher=p, away_pitcher=p,
            home_team_era=LEAGUE_ERA, away_team_era=LEAGUE_ERA, league_rpg=4.5,
        )
        weak, _ = project_runs(
            home_offense_rpg=3.5, away_offense_rpg=4.5, home_pitcher=p, away_pitcher=p,
            home_team_era=LEAGUE_ERA, away_team_era=LEAGUE_ERA, league_rpg=4.5,
        )
        assert strong > weak


class TestInningsPerStart:
    """Season innings divided by starts is only meaningful for actual starters."""

    def test_normal_starter(self):
        from app.services.projections_mlb import innings_per_start

        assert innings_per_start(139.0, 23, 23) == pytest.approx(6.04, abs=0.01)

    def test_swingman_is_rejected(self):
        # 68 IP across 2 starts is relief work: 34 innings a start is not a real number.
        from app.services.projections_mlb import innings_per_start

        assert innings_per_start(68.0, 2, 30) is None

    def test_implausible_value_is_rejected_even_without_appearance_count(self):
        from app.services.projections_mlb import innings_per_start

        assert innings_per_start(68.0, 2) is None

    def test_missing_inputs_return_none(self):
        from app.services.projections_mlb import innings_per_start

        assert innings_per_start(None, 20, 20) is None
        assert innings_per_start(120.0, 0, 20) is None
        assert innings_per_start(120.0, None, None) is None

    def test_rejected_value_falls_back_to_the_default_in_projection(self):
        from app.services.projections_mlb import DEFAULT_STARTER_INNINGS, _runs_allowed_index

        swing = PitcherInput(name="s", era=3.0, innings_pitched=68.0, innings_per_start=None)
        explicit = PitcherInput(
            name="s", era=3.0, innings_pitched=68.0, innings_per_start=DEFAULT_STARTER_INNINGS
        )
        assert _runs_allowed_index(swing, 4.2) == pytest.approx(_runs_allowed_index(explicit, 4.2))


class TestTeamTotals:
    """Team totals reuse the per-side Poisson mean the game model already computes."""

    def test_prob_at_the_mean_is_near_half(self):
        from app.services.projections_mlb import prob_team_over

        # Poisson(4.5) over 4.5 excludes the push mass at exactly 4.
        assert 0.4 < prob_team_over(4.5, 4.5) < 0.6

    def test_monotonic_in_line(self):
        from app.services.projections_mlb import prob_team_over

        probs = [prob_team_over(4.5, x) for x in (2.5, 3.5, 4.5, 5.5, 6.5)]
        assert all(a > b for a, b in zip(probs, probs[1:]))

    def test_stronger_offence_clears_the_same_line_more_often(self):
        from app.services.projections_mlb import prob_team_over

        assert prob_team_over(5.5, 4.5) > prob_team_over(3.5, 4.5)

    def test_team_probabilities_are_not_the_game_total(self):
        """A 4.5 team total is a different question from a 9.0 game total."""
        from app.services.projections_mlb import prob_team_over, prob_total_over

        assert prob_team_over(4.5, 4.5) != prob_total_over(4.5, 4.5, 4.5)


class TestStrikeoutModel:
    def test_expected_scales_with_rate_and_innings(self):
        from app.services.projections_props import expected_strikeouts

        # 9 K/9 over 6 innings against a league-average offence is 6 strikeouts.
        assert expected_strikeouts(9.0, 6.0, 0.221, 0.221) == pytest.approx(6.0)

    def test_opponent_adjustment_is_material(self):
        """Team K rates span 18.7%-25.4%; that must move the projection meaningfully."""
        from app.services.projections_props import expected_strikeouts

        low = expected_strikeouts(9.0, 6.0, 0.187, 0.221)
        high = expected_strikeouts(9.0, 6.0, 0.254, 0.221)
        assert high / low == pytest.approx(0.254 / 0.187, abs=1e-6)
        assert high - low > 1.5  # more than a full strikeout of spread

    def test_distribution_sums_to_one(self):
        from app.services.projections_props import distribution

        assert sum(distribution(6.0)) == pytest.approx(1.0, abs=1e-6)

    def test_dispersion_of_one_is_exactly_poisson(self):
        """The backtest chose Poisson; the NB form must collapse to it cleanly."""
        import math

        from app.services.projections_props import _nb_pmf

        for k in (0, 3, 7, 12):
            poisson = math.exp(-6.0 + k * math.log(6.0) - math.lgamma(k + 1))
            assert _nb_pmf(k, 6.0, 1.0) == pytest.approx(poisson, abs=1e-9)

    def test_prob_over_decreases_with_line(self):
        from app.services.projections_props import prob_over_line

        probs = [prob_over_line(6.0, x) for x in (3.5, 4.5, 5.5, 6.5, 7.5)]
        assert all(a > b for a, b in zip(probs, probs[1:]))
