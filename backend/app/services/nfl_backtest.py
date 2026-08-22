"""Walk-forward backtest of the NFL model against the closing line.

The closing line is the benchmark because it is the market's own best estimate, and
nflverse gives it to us free for every historical game. The question is narrow and
answerable: does our projection predict results more accurately than the closing number
did? If not, we hold no information the market lacks, and the model should not drive
anything.

Three of four models in earlier phases failed this test. Reporting that plainly is the
correct outcome, not a reason to loosen the bar.
"""

from __future__ import annotations

import logging
import statistics
from dataclasses import dataclass

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NflGame, NflTeamGame
from app.services.nfl_projections import (
    RunningLeague,
    TeamRating,
    project,
)
from app.services.stats import BREAK_EVEN_110, rate

log = logging.getLogger(__name__)

# Weeks 1-3 ratings lean almost entirely on the prior season; scored separately rather
# than silently mixed in.
MIN_WEEK = 4


@dataclass
class NflBacktest:
    n: int
    model_total_mae: float
    market_total_mae: float
    model_total_bias: float
    model_margin_mae: float
    market_margin_mae: float
    actual_total_sd: float
    total_rmse: float
    margin_rmse: float
    # How often taking our side of the total would have won.
    over_under_hits: int
    over_under_n: int
    ats_hits: int
    ats_n: int
    holdout_season: int
    holdout_total_mae: float | None = None
    holdout_market_mae: float | None = None

    @property
    def beats_market_total(self) -> bool:
        return self.model_total_mae < self.market_total_mae

    @property
    def beats_market_margin(self) -> bool:
        return self.model_margin_mae < self.market_margin_mae

    def summary(self) -> str:
        ou = rate(self.over_under_hits, self.over_under_n) if self.over_under_n else None
        ats = rate(self.ats_hits, self.ats_n) if self.ats_n else None

        lines = [
            f"games scored           {self.n}",
            "",
            "TOTALS",
            f"  model MAE            {self.model_total_mae:.2f} points",
            f"  closing line MAE     {self.market_total_mae:.2f} points",
            f"  difference           {self.market_total_mae - self.model_total_mae:+.2f} "
            f"({'model better' if self.beats_market_total else 'MARKET BETTER'})",
            f"  model bias           {self.model_total_bias:+.2f}",
            f"  model RMSE           {self.total_rmse:.2f}   (actual SD {self.actual_total_sd:.2f})",
            "",
            "MARGIN / SPREAD",
            f"  model MAE            {self.model_margin_mae:.2f} points",
            f"  closing line MAE     {self.market_margin_mae:.2f} points",
            f"  difference           {self.market_margin_mae - self.model_margin_mae:+.2f} "
            f"({'model better' if self.beats_market_margin else 'MARKET BETTER'})",
            "",
            "IF WE HAD BET OUR SIDE",
        ]
        if ou:
            lines.append(f"  totals               {ou.format()} -> {ou.verdict}")
        if ats:
            lines.append(f"  spread               {ats.format()} -> {ats.verdict}")
        lines.append(f"  (break-even at -110 is {BREAK_EVEN_110:.1%})")

        if self.holdout_total_mae is not None:
            lines += [
                "",
                f"HOLDOUT {self.holdout_season}",
                f"  model MAE            {self.holdout_total_mae:.2f}",
                f"  closing line MAE     {self.holdout_market_mae:.2f}",
            ]

        lines += ["", "VERDICT"]
        if self.beats_market_total or self.beats_market_margin:
            lines.append("  Model beats the closing line on at least one market.")
            lines.append("  Worth carrying forward -- confirm on the holdout before trusting it.")
        else:
            lines.append("  Model does NOT beat the closing line. It holds no information")
            lines.append("  the market lacks, so it must not drive a recommendation.")
            lines.append("  The splits engine stands on its own regardless.")
        return "\n".join("  " + line for line in lines)


def backtest_totals(session: Session, *, holdout_season: int = 2025) -> NflBacktest:
    """Replay every game, projecting from prior games only, and compare with the close."""
    games = session.scalars(
        select(NflGame)
        .where(
            NflGame.game_type == "REG",
            NflGame.home_score.is_not(None),
            NflGame.total_line.is_not(None),
            NflGame.spread_line.is_not(None),
        )
        .order_by(NflGame.season, NflGame.week)
    ).all()
    if not games:
        raise ValueError("no NFL games with results and closing lines; run nfl-ingest first")

    # Both the team ratings and the league baseline advance game by game. Pooling all
    # seasons into one baseline was the original bias: NFL scoring swung six points
    # between 2020 and 2023, so a single mean cannot describe every season, and using
    # later seasons to project earlier ones was a look-ahead besides.
    ratings: dict[str, TeamRating] = {}
    league = RunningLeague()

    model_total_err: list[float] = []
    market_total_err: list[float] = []
    model_margin_err: list[float] = []
    market_margin_err: list[float] = []
    actual_totals: list[float] = []
    holdout_model: list[float] = []
    holdout_market: list[float] = []

    ou_hits = ou_n = ats_hits = ats_n = 0

    for game in games:
        home = ratings.setdefault(game.home_team, TeamRating(team=game.home_team))
        away = ratings.setdefault(game.away_team, TeamRating(team=game.away_team))

        scoreable = (
            game.week >= MIN_WEEK and home.games >= 4 and away.games >= 4 and league.ready
        )
        if scoreable:
            projection = project(
                home, away, league.snapshot(), wind=game.wind, roof=game.roof
            )

            actual_total = float(game.home_score + game.away_score)
            actual_margin = float(game.home_score - game.away_score)

            model_total_err.append(projection.total - actual_total)
            market_total_err.append(game.total_line - actual_total)
            model_margin_err.append(projection.margin - actual_margin)
            # nflverse spread_line is positive when the home side is favoured, which is
            # the same orientation as our margin.
            market_margin_err.append(game.spread_line - actual_margin)
            actual_totals.append(actual_total)

            if game.season == holdout_season:
                holdout_model.append(projection.total - actual_total)
                holdout_market.append(game.total_line - actual_total)

            # Would betting our side have won? Only counted when we disagree with the
            # close, since agreeing with it is not a bet.
            if abs(projection.total - game.total_line) >= 0.5 and actual_total != game.total_line:
                ou_n += 1
                took_over = projection.total > game.total_line
                went_over = actual_total > game.total_line
                ou_hits += 1 if took_over == went_over else 0

            if abs(projection.margin - game.spread_line) >= 0.5:
                adjusted = actual_margin - game.spread_line
                if abs(adjusted) > 1e-9:
                    ats_n += 1
                    took_home = projection.margin > game.spread_line
                    home_covered = adjusted > 0
                    ats_hits += 1 if took_home == home_covered else 0

        # Advance state with this game's outcome, strictly after scoring it.
        league.observe(game.home_score, game.away_score)
        for team, rating in ((game.home_team, home), (game.away_team, away)):
            row = session.scalar(
                select(NflTeamGame).where(
                    NflTeamGame.game_id == game.game_id, NflTeamGame.team == team
                )
            )
            if row is not None:
                rating.add(row)

    n = len(model_total_err)
    if n == 0:
        raise ValueError("no scoreable games; need at least 4 prior games per team")

    def mae(values: list[float]) -> float:
        return sum(abs(v) for v in values) / len(values)

    return NflBacktest(
        n=n,
        model_total_mae=mae(model_total_err),
        market_total_mae=mae(market_total_err),
        model_total_bias=sum(model_total_err) / n,
        model_margin_mae=mae(model_margin_err),
        market_margin_mae=mae(market_margin_err),
        actual_total_sd=statistics.pstdev(actual_totals),
        total_rmse=(sum(v * v for v in model_total_err) / n) ** 0.5,
        margin_rmse=(sum(v * v for v in model_margin_err) / n) ** 0.5,
        over_under_hits=ou_hits,
        over_under_n=ou_n,
        ats_hits=ats_hits,
        ats_n=ats_n,
        holdout_season=holdout_season,
        holdout_total_mae=mae(holdout_model) if holdout_model else None,
        holdout_market_mae=mae(holdout_market) if holdout_market else None,
    )
