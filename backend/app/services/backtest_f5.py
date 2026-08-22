"""Walk-forward backtest for the first-five-innings model.

This is the decisive test of the whole phase. The full-game MLB model showed no skill,
and the stated reason was that bullpen innings and late-game randomness swamp the
starting-pitcher signal. F5 removes exactly that noise. If the thesis is right, the same
kind of model should do measurably better here; if it does not, the thesis is wrong and
we should say so rather than ship it.
"""

from __future__ import annotations

import statistics
from collections import defaultdict
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Game, GameInnings, PitcherGameLog, Sport
from app.services.projections_mlb import LEAGUE_ERA, PITCHER_REGRESSION_INNINGS

MIN_TEAM_GAMES = 20
MIN_PRIOR_STARTS = 3


@dataclass
class F5Backtest:
    n: int
    mae: float
    bias: float
    rmse: float
    baseline_mae: float
    actual_sd: float
    home_win_rate: float
    tie_rate: float
    # Same measurement on full-game totals, for a like-for-like comparison.
    full_game_mae: float
    full_game_baseline_mae: float

    @property
    def f5_improvement(self) -> float:
        return 1 - self.mae / self.baseline_mae

    @property
    def full_game_improvement(self) -> float:
        return 1 - self.full_game_mae / self.full_game_baseline_mae

    def summary(self) -> str:
        lines = [
            f"games scored         {self.n}",
            f"F5 total MAE         {self.mae:.2f} runs",
            f"F5 bias              {self.bias:+.2f}",
            f"F5 RMSE              {self.rmse:.2f}",
            f"F5 actual SD         {self.actual_sd:.2f}",
            f"F5 baseline MAE      {self.baseline_mae:.2f}  (league average)",
            f"F5 improvement       {self.f5_improvement * 100:+.1f}% vs baseline",
            "",
            f"full-game improvement {self.full_game_improvement * 100:+.1f}% "
            f"(MAE {self.full_game_mae:.2f} vs baseline {self.full_game_baseline_mae:.2f})",
            "",
            f"home led after 5     {self.home_win_rate:.1%}",
            f"tied after 5         {self.tie_rate:.1%}  (books push these on the F5 line)",
        ]
        return "\n".join("  " + line for line in lines)


def backtest_f5(session: Session, season: int) -> F5Backtest:
    """Replay the season projecting F5 runs from prior information only."""
    f5_rows = session.execute(
        select(
            GameInnings.game_id,
            GameInnings.home_runs,
            GameInnings.away_runs,
        ).where(GameInnings.inning <= 5)
    ).all()

    f5_by_game: dict[int, list[int]] = defaultdict(lambda: [0, 0])
    for game_id, home, away in f5_rows:
        f5_by_game[game_id][0] += home
        f5_by_game[game_id][1] += away

    games = session.scalars(
        select(Game)
        .where(
            Game.sport == Sport.MLB,
            Game.season == season,
            Game.is_final.is_(True),
            Game.home_score.is_not(None),
        )
        .order_by(Game.start_time)
    ).all()

    logs_by_pitcher: dict[int, list] = defaultdict(list)
    for row in session.scalars(
        select(PitcherGameLog)
        .where(PitcherGameLog.season == season)
        .order_by(PitcherGameLog.game_date)
    ).all():
        logs_by_pitcher[row.player_id].append(row)

    team_f5: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])  # for, against, games
    team_full: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])
    pitcher: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0])  # ip, er, starts
    consumed: dict[int, int] = defaultdict(int)

    league_f5 = [0.0, 0]   # runs, team-games
    league_full = [0.0, 0]

    errors: list[float] = []
    baseline_errors: list[float] = []
    actuals: list[int] = []
    full_errors: list[float] = []
    full_baseline: list[float] = []
    home_led = tied = scored = 0

    def regressed(pid: int | None) -> float | None:
        if not pid:
            return None
        ip, er, starts = pitcher[pid]
        if starts < MIN_PRIOR_STARTS or ip <= 0:
            return None
        era = 9.0 * er / ip
        w = ip / (ip + PITCHER_REGRESSION_INNINGS)
        return w * era + (1 - w) * LEAGUE_ERA

    for game in games:
        f5 = f5_by_game.get(game.id)
        home_stats = team_f5[game.home_team_id]
        away_stats = team_f5[game.away_team_id]

        if (
            f5
            and home_stats[2] >= MIN_TEAM_GAMES
            and away_stats[2] >= MIN_TEAM_GAMES
            and league_f5[1] > 0
        ):
            league_avg_f5 = league_f5[0] / league_f5[1]
            home_off = home_stats[0] / home_stats[2]
            away_off = away_stats[0] / away_stats[2]
            home_def = home_stats[1] / home_stats[2]
            away_def = away_stats[1] / away_stats[2]

            # Ratio form, same structure as the game model but on F5 quantities.
            proj_home = home_off * (away_def / league_avg_f5)
            proj_away = away_off * (home_def / league_avg_f5)

            # Nudge by the announced starters where we have a prior rate for them.
            for pid, is_home in ((game.away_probable_pitcher_id, False),
                                 (game.home_probable_pitcher_id, True)):
                era = regressed(pid)
                if era is None:
                    continue
                factor = 1.0 + ((era / LEAGUE_ERA) - 1.0) * 0.55
                if is_home:
                    proj_away *= factor
                else:
                    proj_home *= factor

            actual = f5[0] + f5[1]
            errors.append(proj_home + proj_away - actual)
            baseline_errors.append(2 * league_avg_f5 - actual)
            actuals.append(actual)

            # Same measurement on the full game, so the comparison is like for like.
            if league_full[1] > 0 and team_full[game.home_team_id][2] >= MIN_TEAM_GAMES:
                lf = league_full[0] / league_full[1]
                fh = team_full[game.home_team_id]
                fa = team_full[game.away_team_id]
                pf = (fh[0] / fh[2]) * ((fa[1] / fa[2]) / lf)
                pa = (fa[0] / fa[2]) * ((fh[1] / fh[2]) / lf)
                actual_full = game.home_score + game.away_score
                full_errors.append(pf + pa - actual_full)
                full_baseline.append(2 * lf - actual_full)

            scored += 1
            if f5[0] > f5[1]:
                home_led += 1
            elif f5[0] == f5[1]:
                tied += 1

        # Advance state.
        if f5:
            team_f5[game.home_team_id][0] += f5[0]
            team_f5[game.home_team_id][1] += f5[1]
            team_f5[game.home_team_id][2] += 1
            team_f5[game.away_team_id][0] += f5[1]
            team_f5[game.away_team_id][1] += f5[0]
            team_f5[game.away_team_id][2] += 1
            league_f5[0] += f5[0] + f5[1]
            league_f5[1] += 2

        team_full[game.home_team_id][0] += game.home_score
        team_full[game.home_team_id][1] += game.away_score
        team_full[game.home_team_id][2] += 1
        team_full[game.away_team_id][0] += game.away_score
        team_full[game.away_team_id][1] += game.home_score
        team_full[game.away_team_id][2] += 1
        league_full[0] += game.home_score + game.away_score
        league_full[1] += 2

        for pid in (game.home_probable_pitcher_id, game.away_probable_pitcher_id):
            if not pid:
                continue
            entries = logs_by_pitcher.get(pid, [])
            i = consumed[pid]
            while i < len(entries) and entries[i].game_date <= game.game_date:
                e = entries[i]
                if e.innings_pitched and e.earned_runs is not None:
                    pitcher[pid][0] += e.innings_pitched
                    pitcher[pid][1] += e.earned_runs
                    pitcher[pid][2] += 1
                i += 1
            consumed[pid] = i

    if not errors:
        raise ValueError(f"no scoreable F5 games for {season}")

    n = len(errors)
    fn = len(full_errors) or 1
    return F5Backtest(
        n=n,
        mae=sum(abs(e) for e in errors) / n,
        bias=sum(errors) / n,
        rmse=(sum(e * e for e in errors) / n) ** 0.5,
        baseline_mae=sum(abs(e) for e in baseline_errors) / n,
        actual_sd=statistics.pstdev(actuals),
        home_win_rate=home_led / scored if scored else 0.0,
        tie_rate=tied / scored if scored else 0.0,
        full_game_mae=sum(abs(e) for e in full_errors) / fn,
        full_game_baseline_mae=sum(abs(e) for e in full_baseline) / fn,
    )
