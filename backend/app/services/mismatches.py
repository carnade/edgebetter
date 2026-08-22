"""Lopsided pitching matchups: a strong club's best arm against a weak club's worst.

These are the games most likely to be won by the favourite -- and precisely the games
the market prices hardest. A high mismatch score says "likely to win", never "worth
betting"; those are different questions, so every row carries both the win probability
and what the price actually costs.

Ranking weights the starting pitcher above team quality (65/35). That is not a guess:
the walk-forward MLB backtest found team-level rate stats have no skill over a
league-average baseline at the single-game level, while the starter dominates.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import Game, Sport
from app.services import projections_mlb
from app.services.devig import (
    BookPrice,
    american_to_decimal,
    consensus,
    expected_value,
    quarter_kelly,
)
from app.services.ingest_odds import latest_snapshots
from app.services.rotations import (
    PYTHAGOREAN_EXPONENT,
    RotationSlot,
    TeamStrength,
    Tier,
    rotation_slot,
    team_strengths,
)

log = logging.getLogger(__name__)

# Score weights. The pitcher carries most of it -- see module docstring.
PITCHER_WEIGHT = 0.65
TEAM_WEIGHT = 0.35

# Normalisation ceilings, from the observed 2026 spread: Pythagorean win% runs about
# 0.37 to 0.63, and regressed starter ERA about 2.5 to 5.8.
MAX_TEAM_GAP = 0.25
MAX_ERA_GAP = 3.0

# --- Gate thresholds for the combined "is this a good bet" verdict ---
# Below this score the matchup is close to a coin flip: the 0-20 band won 48.6%.
MIN_GATE_SCORE = 35.0
# Positive expected value against the devigged consensus, with a little margin for
# the noise in our own median-of-books estimate.
MIN_GATE_EV = 0.005
# A consensus drawn from three books is not a consensus.
MIN_GATE_BOOKS = 4


@dataclass(frozen=True)
class GateCheck:
    """One pass/fail condition, carrying enough detail to explain itself."""

    key: str
    label: str
    passed: bool
    detail: str


@dataclass(frozen=True)
class Mismatch:
    game_id: int
    start_time: datetime
    favourite_abbrev: str
    underdog_abbrev: str
    favourite_is_home: bool

    favourite_team: TeamStrength
    underdog_team: TeamStrength
    favourite_pitcher: RotationSlot | None
    underdog_pitcher: RotationSlot | None

    score: float                 # 0-100
    team_gap: float
    era_gap: float
    strict: bool                 # good team + top-2 arm vs bad team + bottom-2 arm

    model_win_prob: float | None
    market_fair_prob: float | None
    best_american: int | None
    best_book: str | None
    # EV against the devigged market consensus: the trustworthy number.
    ev: float | None
    # EV against the model's own win probability. Only as good as the model, and the
    # MLB model has no demonstrated skill -- shown for comparison, not for staking.
    model_ev: float | None
    kelly_quarter: float | None
    book_count: int = 0

    # Historical win rate of this game's score band, and the price that band needs to
    # break even. Populated from the walk-forward result so each row can be judged
    # against how its own category has actually performed.
    band_label: str | None = None
    band_win_rate: float | None = None
    band_break_even: int | None = None
    band_sample: int | None = None

    @property
    def risk_to_win_one(self) -> float | None:
        """Units risked to win one. At -250 this is 2.5 -- the cost of 'safe'."""
        if self.best_american is None:
            return None
        return 1.0 / (american_to_decimal(self.best_american) - 1.0)

    @property
    def break_even_prob(self) -> float | None:
        """Win rate this price must beat to profit."""
        if self.best_american is None:
            return None
        return 1.0 / american_to_decimal(self.best_american)

    @property
    def checks(self) -> list[GateCheck]:
        """The four conditions, evaluated in the order a person would apply them.

        Kept as visible pass/fail rather than collapsed into a single badge: a verdict
        that hides its reasoning invites betting on autopilot, which is the failure
        mode this whole tool exists to prevent.
        """
        results: list[GateCheck] = []

        results.append(
            GateCheck(
                key="lopsided",
                label="Lopsided enough",
                passed=self.score >= MIN_GATE_SCORE,
                detail=f"score {self.score:.0f}, needs {MIN_GATE_SCORE:.0f}+",
            )
        )

        if self.ev is None:
            results.append(
                GateCheck("value", "Beats the market", False, "no book price yet")
            )
        else:
            results.append(
                GateCheck(
                    key="value",
                    label="Beats the market",
                    passed=self.ev >= MIN_GATE_EV,
                    detail=f"EV {self.ev * 100:+.2f}%, needs {MIN_GATE_EV * 100:+.1f}%",
                )
            )

        # The price must be better than what this score band historically needed.
        be = self.break_even_prob
        if be is None or self.band_win_rate is None:
            results.append(
                GateCheck("history", "Price beats its band", False, "not priced or no history")
            )
        else:
            results.append(
                GateCheck(
                    key="history",
                    label="Price beats its band",
                    passed=self.band_win_rate > be,
                    detail=(
                        f"band won {self.band_win_rate * 100:.1f}%, "
                        f"price needs {be * 100:.1f}%"
                    ),
                )
            )

        results.append(
            GateCheck(
                key="books",
                label="Enough books",
                passed=self.book_count >= MIN_GATE_BOOKS,
                detail=f"{self.book_count} books, needs {MIN_GATE_BOOKS}+",
            )
        )
        return results

    @property
    def passed_checks(self) -> int:
        return sum(1 for c in self.checks if c.passed)

    @property
    def is_good_bet(self) -> bool:
        return all(c.passed for c in self.checks)

    @property
    def grade(self) -> str:
        """One word, but always shown next to the checks that produced it."""
        if self.ev is None:
            return "unpriced"
        if self.is_good_bet:
            return "bet"
        if self.passed_checks == 3:
            return "near miss"
        return "pass"

    @property
    def blocking_reason(self) -> str | None:
        """The first failed check, for a one-line explanation of why this is not a bet."""
        for check in self.checks:
            if not check.passed:
                return f"{check.label}: {check.detail}"
        return None

    @property
    def verdict(self) -> str:
        """Plain-language read on the price, kept separate from the mismatch score.

        Judged against the market consensus, never against the model: the consensus
        needs no model to be correct, and treating an unvalidated projection as proof
        of value is how a "safe bet" list turns into a losing one.
        """
        if self.market_fair_prob is None or self.ev is None:
            return "unpriced"
        if self.ev >= 0.005:
            return "value"
        if self.ev >= -0.02:
            return "fairly priced"
        return "overpriced"

    @property
    def model_disagrees(self) -> bool:
        """The model rates the favourite meaningfully higher than the market does."""
        if self.model_win_prob is None or self.market_fair_prob is None:
            return False
        return self.model_win_prob - self.market_fair_prob >= 0.03


def _score(team_gap: float, era_gap: float) -> float:
    team_component = max(0.0, min(1.0, team_gap / MAX_TEAM_GAP))
    era_component = max(0.0, min(1.0, era_gap / MAX_ERA_GAP))
    return 100.0 * (TEAM_WEIGHT * team_component + PITCHER_WEIGHT * era_component)


def band_for(score: float) -> str:
    """Which evidence band a score falls into. Shared so the live board and the
    walk-forward backtest can never drift into using different boundaries."""
    if score < 20:
        return "0-20"
    if score < 35:
        return "20-35"
    if score < 50:
        return "35-50"
    return "50+"


def _moneyline_market(session: Session, game: Game) -> list[BookPrice]:
    return [
        BookPrice(
            bookmaker=s.bookmaker, outcome=s.outcome, american=s.price_american, point=None
        )
        for s in latest_snapshots(session, game.id)
        if s.market == "h2h"
    ]


def find_mismatches(
    session: Session, season: int, *, hours_ahead: int = 48, min_score: float = 0.0
) -> list[Mismatch]:
    """Score every upcoming game and return them ranked, strongest mismatch first."""
    now = datetime.now(UTC)
    games = session.scalars(
        select(Game)
        .where(
            Game.sport == Sport.MLB,
            Game.is_final.is_(False),
            Game.start_time >= now,
            Game.start_time <= now + timedelta(hours=hours_ahead),
        )
        .order_by(Game.start_time)
    ).all()

    strengths = team_strengths(session, season)

    # How each score band has actually performed, so every row can be judged against
    # its own category rather than against a hunch.
    band_stats: dict[str, tuple[float, int]] = {}
    try:
        for label, wins, played in cached_walk_forward(session, season).bands:
            if played:
                band_stats[label] = (wins / played, played)
    except Exception as exc:  # noqa: BLE001 - history is a bonus, not a dependency
        log.warning("walk-forward history unavailable (%s); gate will skip that check", exc)

    results: list[Mismatch] = []

    for game in games:
        home = strengths.get(game.home_team_id)
        away = strengths.get(game.away_team_id)
        if home is None or away is None:
            continue

        home_slot = rotation_slot(session, game.home_team_id, game.home_probable_pitcher_id, season)
        away_slot = rotation_slot(session, game.away_team_id, game.away_probable_pitcher_id, season)

        projection = projections_mlb.project(
            session,
            game.home_team_id,
            game.away_team_id,
            season,
            home_pitcher_id=game.home_probable_pitcher_id,
            away_pitcher_id=game.away_probable_pitcher_id,
        )
        home_win = projection.prob_home_win() if projection else None

        # The favourite is whichever side the model prefers; fall back to team strength
        # when a projection is unavailable.
        if home_win is not None:
            home_favoured = home_win >= 0.5
        else:
            home_favoured = home.pythagorean >= away.pythagorean

        fav_team, dog_team = (home, away) if home_favoured else (away, home)
        fav_slot, dog_slot = (home_slot, away_slot) if home_favoured else (away_slot, home_slot)
        fav_prob = None
        if home_win is not None:
            fav_prob = home_win if home_favoured else 1.0 - home_win

        team_gap = fav_team.pythagorean - dog_team.pythagorean
        era_gap = 0.0
        if fav_slot and dog_slot:
            era_gap = dog_slot.regressed_era - fav_slot.regressed_era

        strict = bool(
            fav_team.tier is Tier.GOOD
            and dog_team.tier is Tier.BAD
            and fav_slot
            and fav_slot.is_top_two
            and dog_slot
            and dog_slot.is_bottom_two
        )

        fair_prob = ev = kelly = model_ev = None
        best_american = best_book = None
        book_count = 0
        fav_name = (
            game.home_team.display_name if home_favoured else game.away_team.display_name
        )
        for outcome in consensus(_moneyline_market(session, game)):
            if outcome.outcome.strip().lower() != fav_name.strip().lower():
                continue
            fair_prob = outcome.fair_prob
            best_american = outcome.best_american
            best_book = outcome.best_book
            book_count = outcome.book_count
            ev = expected_value(outcome.fair_prob, outcome.best_decimal)
            kelly = quarter_kelly(outcome.fair_prob, outcome.best_decimal)
            if fav_prob is not None:
                model_ev = expected_value(fav_prob, outcome.best_decimal)
            break

        score = _score(team_gap, era_gap)
        if score < min_score:
            continue

        label = band_for(score)
        band_rate, band_n = band_stats.get(label, (None, None))
        band_be = (
            round(-100 * band_rate / (1 - band_rate))
            if band_rate and 0 < band_rate < 1
            else None
        )

        results.append(
            Mismatch(
                game_id=game.id,
                start_time=game.start_time,
                favourite_abbrev=fav_team.abbrev,
                underdog_abbrev=dog_team.abbrev,
                favourite_is_home=home_favoured,
                favourite_team=fav_team,
                underdog_team=dog_team,
                favourite_pitcher=fav_slot,
                underdog_pitcher=dog_slot,
                score=score,
                team_gap=team_gap,
                era_gap=era_gap,
                strict=strict,
                model_win_prob=fav_prob,
                market_fair_prob=fair_prob,
                best_american=best_american,
                best_book=best_book,
                ev=ev,
                model_ev=model_ev,
                kelly_quarter=kelly,
                book_count=book_count,
                band_label=label,
                band_win_rate=band_rate,
                band_break_even=band_be,
                band_sample=band_n,
            )
        )

    # Actionable first: real bets, then near misses, then everything else by score.
    grade_order = {"bet": 0, "near miss": 1, "pass": 2, "unpriced": 3}
    results.sort(key=lambda m: (grade_order.get(m.grade, 9), -m.score))
    return results


# --------------------------------------------------------------- historical check
@dataclass
class MismatchBacktest:
    """How often the favoured side actually won, by mismatch strength."""

    buckets: list[tuple[str, int, int]]  # (label, wins, games)
    strict_wins: int
    strict_games: int
    baseline_home_win_rate: float

    def summary(self) -> str:
        lines = [f"  {'mismatch score':<16} {'win rate':>9} {'games':>7}"]
        for label, wins, games in self.buckets:
            rate = wins / games if games else 0.0
            lines.append(f"  {label:<16} {rate:>8.1%} {games:>7}")
        if self.strict_games:
            lines.append("")
            lines.append(
                f"  strict mismatches: {self.strict_wins}/{self.strict_games} = "
                f"{self.strict_wins / self.strict_games:.1%}"
            )
        lines.append(f"  (home teams overall won {self.baseline_home_win_rate:.1%})")
        return "\n".join(lines)


def backtest_mismatches(session: Session, season: int) -> MismatchBacktest:
    """Measure how often the favoured side won, across completed games.

    Caveat worth stating plainly: team tiers and rotation ranks come from full-season
    stats, so a game played in May is graded with information from September. That
    inflates the apparent effect and makes this a description of the phenomenon, not a
    simulation of a betting strategy. It still answers the useful question -- when a
    strong club's best arm faces a weak club's worst, how often does the favourite
    actually win?
    """
    games = session.scalars(
        select(Game).where(
            Game.sport == Sport.MLB,
            Game.season == season,
            Game.is_final.is_(True),
            Game.home_score.is_not(None),
        )
    ).all()

    strengths = team_strengths(session, season)
    rotations: dict[int, dict[int, RotationSlot]] = {}

    def slot_for(team_id: int, player_id: int | None) -> RotationSlot | None:
        if not player_id:
            return None
        if team_id not in rotations:
            from app.services.rotations import team_rotation

            rotations[team_id] = {s.player_id: s for s in team_rotation(session, team_id, season)}
        return rotations[team_id].get(player_id)

    bands: dict[str, list[int]] = {"0-20": [], "20-35": [], "35-50": [], "50+": []}
    strict_wins = strict_games = 0
    home_wins = total = 0

    for game in games:
        home = strengths.get(game.home_team_id)
        away = strengths.get(game.away_team_id)
        if home is None or away is None:
            continue

        total += 1
        if game.home_score > game.away_score:
            home_wins += 1

        home_slot = slot_for(game.home_team_id, game.home_probable_pitcher_id)
        away_slot = slot_for(game.away_team_id, game.away_probable_pitcher_id)
        if not home_slot or not away_slot:
            continue

        home_favoured = home.pythagorean >= away.pythagorean
        fav_team, dog_team = (home, away) if home_favoured else (away, home)
        fav_slot, dog_slot = (home_slot, away_slot) if home_favoured else (away_slot, home_slot)

        score = _score(
            fav_team.pythagorean - dog_team.pythagorean,
            dog_slot.regressed_era - fav_slot.regressed_era,
        )
        fav_won = (
            game.home_score > game.away_score
            if home_favoured
            else game.away_score > game.home_score
        )

        band = "0-20" if score < 20 else "20-35" if score < 35 else "35-50" if score < 50 else "50+"
        bands[band].append(1 if fav_won else 0)

        if (
            fav_team.tier is Tier.GOOD
            and dog_team.tier is Tier.BAD
            and fav_slot.is_top_two
            and dog_slot.is_bottom_two
        ):
            strict_games += 1
            strict_wins += 1 if fav_won else 0

    return MismatchBacktest(
        buckets=[(label, sum(v), len(v)) for label, v in bands.items()],
        strict_wins=strict_wins,
        strict_games=strict_games,
        baseline_home_win_rate=home_wins / total if total else 0.0,
    )


# ------------------------------------------------------- walk-forward validation
@dataclass
class WalkForwardResult:
    """Same measurement, but using only information available before each game."""

    bands: list[tuple[str, int, int]]
    strict_wins: int
    strict_games: int
    baseline_home_win_rate: float
    skipped_no_data: int

    def summary(self) -> str:
        lines = [f"  {'mismatch score':<16} {'win rate':>9} {'games':>7}  {'break-even price':>17}"]
        for label, wins, games in self.bands:
            rate = wins / games if games else 0.0
            # American odds a book would need to offer for this rate to break even.
            price = f"{-100 * rate / (1 - rate):.0f}" if 0 < rate < 1 else "n/a"
            lines.append(f"  {label:<16} {rate:>8.1%} {games:>7}  {price:>17}")
        if self.strict_games:
            rate = self.strict_wins / self.strict_games
            lines.append("")
            lines.append(
                f"  strict mismatches: {self.strict_wins}/{self.strict_games} = {rate:.1%}"
            )
        lines.append(f"  (home teams overall won {self.baseline_home_win_rate:.1%})")
        lines.append(f"  ({self.skipped_no_data} games skipped for insufficient prior data)")
        return "\n".join(lines)


_WALK_FORWARD_CACHE: dict[tuple[int, int], WalkForwardResult] = {}


def cached_walk_forward(session: Session, season: int) -> WalkForwardResult:
    """Walk-forward result, recomputed only when the number of completed games changes."""
    from sqlalchemy import func

    completed = (
        session.scalar(
            select(func.count())
            .select_from(Game)
            .where(Game.sport == Sport.MLB, Game.season == season, Game.is_final.is_(True))
        )
        or 0
    )
    key = (season, completed)
    if key not in _WALK_FORWARD_CACHE:
        _WALK_FORWARD_CACHE.clear()
        _WALK_FORWARD_CACHE[key] = backtest_mismatches_walk_forward(session, season)
    return _WALK_FORWARD_CACHE[key]


def backtest_mismatches_walk_forward(
    session: Session, season: int, *, min_team_games: int = 20, min_starts: int = 3
) -> WalkForwardResult:
    """Replay the season, grading each game only on what was known beforehand.

    This is the number to trust. The full-season version inflates the effect, because a
    club that finished strong is graded as strong for games it played in April.
    """
    from collections import defaultdict

    from app.models import PitcherGameLog
    from app.services.projections_mlb import LEAGUE_ERA, PITCHER_REGRESSION_INNINGS

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

    # Pitcher starts, keyed by date, so running ERA can be advanced in step.
    logs_by_pitcher: dict[int, list] = defaultdict(list)
    for row in session.scalars(
        select(PitcherGameLog).where(PitcherGameLog.season == season).order_by(
            PitcherGameLog.game_date
        )
    ).all():
        logs_by_pitcher[row.player_id].append(row)

    team_runs: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])  # rf, ra, games
    pitcher_line: dict[int, list[float]] = defaultdict(lambda: [0.0, 0.0, 0.0])  # ip, er, starts
    team_starters: dict[int, set[int]] = defaultdict(set)
    consumed: dict[int, int] = defaultdict(int)

    bands: dict[str, list[int]] = {"0-20": [], "20-35": [], "35-50": [], "50+": []}
    strict_wins = strict_games = 0
    home_wins = total = skipped = 0

    def regressed(player_id: int) -> float | None:
        ip, er, starts = pitcher_line[player_id]
        if starts < min_starts or ip <= 0:
            return None
        era = 9.0 * er / ip
        weight = ip / (ip + PITCHER_REGRESSION_INNINGS)
        return weight * era + (1 - weight) * LEAGUE_ERA

    def pythagorean(team_id: int) -> float | None:
        rf, ra, played = team_runs[team_id]
        if played < min_team_games or rf <= 0 or ra <= 0:
            return None
        return rf**PYTHAGOREAN_EXPONENT / (rf**PYTHAGOREAN_EXPONENT + ra**PYTHAGOREAN_EXPONENT)

    def rank_within(team_id: int, player_id: int) -> tuple[int, int] | None:
        graded = []
        for candidate in team_starters[team_id]:
            value = regressed(candidate)
            if value is not None:
                graded.append((value, candidate))
        if len(graded) < 3:
            return None
        graded.sort()
        for index, (_, candidate) in enumerate(graded):
            if candidate == player_id:
                return index + 1, len(graded)
        return None

    for game in games:
        total += 1
        if game.home_score > game.away_score:
            home_wins += 1

        home_pyth = pythagorean(game.home_team_id)
        away_pyth = pythagorean(game.away_team_id)
        home_pid = game.home_probable_pitcher_id
        away_pid = game.away_probable_pitcher_id

        scored_this_game = False
        if home_pyth is not None and away_pyth is not None and home_pid and away_pid:
            home_era = regressed(home_pid)
            away_era = regressed(away_pid)
            home_rank = rank_within(game.home_team_id, home_pid)
            away_rank = rank_within(game.away_team_id, away_pid)

            if home_era is not None and away_era is not None and home_rank and away_rank:
                home_favoured = home_pyth >= away_pyth
                fav_pyth, dog_pyth = (
                    (home_pyth, away_pyth) if home_favoured else (away_pyth, home_pyth)
                )
                fav_era, dog_era = (
                    (home_era, away_era) if home_favoured else (away_era, home_era)
                )
                fav_rank, dog_rank = (
                    (home_rank, away_rank) if home_favoured else (away_rank, home_rank)
                )

                score = _score(fav_pyth - dog_pyth, dog_era - fav_era)
                fav_won = (
                    game.home_score > game.away_score
                    if home_favoured
                    else game.away_score > game.home_score
                )
                band = (
                    "0-20" if score < 20 else "20-35" if score < 35 else "35-50" if score < 50 else "50+"
                )
                bands[band].append(1 if fav_won else 0)
                scored_this_game = True

                # Strict, using ranks as they stood before the game.
                if (
                    fav_rank[0] <= 2
                    and dog_rank[0] > dog_rank[1] - 2
                    and fav_pyth - dog_pyth >= 0.05
                ):
                    strict_games += 1
                    strict_wins += 1 if fav_won else 0

        if not scored_this_game:
            skipped += 1

        # Advance state with this game's outcome.
        team_runs[game.home_team_id][0] += game.home_score
        team_runs[game.home_team_id][1] += game.away_score
        team_runs[game.home_team_id][2] += 1
        team_runs[game.away_team_id][0] += game.away_score
        team_runs[game.away_team_id][1] += game.home_score
        team_runs[game.away_team_id][2] += 1

        for pid, team_id in ((home_pid, game.home_team_id), (away_pid, game.away_team_id)):
            if not pid:
                continue
            team_starters[team_id].add(pid)
            entries = logs_by_pitcher.get(pid, [])
            index = consumed[pid]
            while index < len(entries) and entries[index].game_date <= game.game_date:
                entry = entries[index]
                if entry.innings_pitched and entry.earned_runs is not None:
                    pitcher_line[pid][0] += entry.innings_pitched
                    pitcher_line[pid][1] += entry.earned_runs
                    pitcher_line[pid][2] += 1
                index += 1
            consumed[pid] = index

    return WalkForwardResult(
        bands=[(label, sum(v), len(v)) for label, v in bands.items()],
        strict_wins=strict_wins,
        strict_games=strict_games,
        baseline_home_win_rate=home_wins / total if total else 0.0,
        skipped_no_data=skipped,
    )
