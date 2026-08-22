"""Player prop projections: passing, rushing, and receiving yards.

Built around one observation about football data: **usage is predictable, efficiency is
not.** A receiver's target share and snap rate carry real week-to-week signal; his yards
per target does not. So yards are projected as volume x efficiency, with volume trusted
and efficiency regressed hard toward the positional mean.

This deliberately does *not* slice a player's own history by condition. A player has 17
games a season, so "vs top-10 defences, outdoors" is five games -- the noise zone. The
opponent effect is measured league-wide from every game that defence played, then applied
to the player's baseline.

The output is a distribution, not a number, so you can bring a line from any book and ask
what share of comparable games clear it. That needs no devigging and no second book.

Distribution choice is measured rather than assumed. Passing yards are near-symmetric
(mean 232, median 230), while rushing and receiving are right-skewed (mean 54/52 against
medians of 46/44) with tails at +1.40 SD where a normal predicts +1.28. A gamma covers
both: it is near-symmetric when variability is low and properly skewed when it is high.
"""

from __future__ import annotations

import logging
import math
from collections import defaultdict
from dataclasses import dataclass
from enum import Enum

from sqlalchemy import select
from sqlalchemy.orm import Session

from app.models import NflPlayerGame
from app.services.stats import Band

log = logging.getLogger(__name__)


class Market(str, Enum):
    """A prop market, as `volume x efficiency`.

    The count markets are here because that decomposition makes them nearly free. Every
    yards projection is already volume x efficiency, where volume is the reliable half (a
    role, and roles are stable) and efficiency is the noisy half. Receptions and rush
    attempts are markets on the volume term alone, so they drop the weakest part of the
    model rather than adding anything to it.

    Measured over 2021-2025, they really are easier to project -- relative error
    (MAE/mean) against their yards equivalents:

        receiving yards 0.624  ->  receptions     0.504   (19% less noisy)
        rushing yards   0.589  ->  rush attempts  0.419   (29% less noisy)

    That says the model *can* project them better, not that the edge is bigger; books
    price volume markets tighter too.
    """

    PASS_YDS = "pass_yds"
    RUSH_YDS = "rush_yds"
    RECV_YDS = "recv_yds"
    RECEPTIONS = "receptions"
    RUSH_ATT = "rush_att"

    @property
    def stat(self) -> str:
        return {
            Market.PASS_YDS: "passing_yards",
            Market.RUSH_YDS: "rushing_yards",
            Market.RECV_YDS: "receiving_yards",
            Market.RECEPTIONS: "receptions",
            Market.RUSH_ATT: "carries",
        }[self]

    @property
    def volume(self) -> str:
        """The attempt-like quantity that drives this market.

        For receptions this is targets, which makes the efficiency term catch rate --
        naturally bounded 0-1 and far better behaved than yards per target. Modelling
        receptions this way also measured marginally better than decaying the reception
        count directly (MAE 1.4493 vs 1.4525), so it wins on both architecture and
        accuracy.

        For rush attempts the stat *is* the volume, so efficiency collapses to 1.0 and the
        projection is simply the player's decayed carries. See `is_pure_volume`.
        """
        return {
            Market.PASS_YDS: "attempts",
            Market.RUSH_YDS: "carries",
            Market.RECV_YDS: "targets",
            Market.RECEPTIONS: "targets",
            Market.RUSH_ATT: "carries",
        }[self]

    @property
    def positions(self) -> tuple[str, ...]:
        return {
            Market.PASS_YDS: ("QB",),
            Market.RUSH_YDS: ("RB", "QB", "WR"),
            Market.RECV_YDS: ("WR", "TE", "RB"),
            Market.RECEPTIONS: ("WR", "TE", "RB"),
            Market.RUSH_ATT: ("RB", "QB", "WR"),
        }[self]

    @property
    def label(self) -> str:
        return {
            Market.PASS_YDS: "Passing yards",
            Market.RUSH_YDS: "Rushing yards",
            Market.RECV_YDS: "Receiving yards",
            Market.RECEPTIONS: "Receptions",
            Market.RUSH_ATT: "Rush attempts",
        }[self]

    @property
    def discrete(self) -> bool:
        """Whether outcomes are counts rather than a continuous quantity.

        This decides the distribution, and it is not cosmetic. A gamma is continuous and
        these markets are settled at lines like 2.5 receptions, where nearly all the
        probability sits on a handful of integers. Approximating that with a smooth curve
        misprices exactly the lines people bet.
        """
        return self in (Market.RECEPTIONS, Market.RUSH_ATT)

    @property
    def is_pure_volume(self) -> bool:
        """Whether the market IS the volume, leaving no efficiency term to estimate.

        True for rush attempts, where stat and volume are both carries. Projecting it
        means projecting the player's own decayed carry count and nothing else.
        """
        return self.stat == self.volume

    @property
    def max_count(self) -> int:
        """Support cap for the discrete distribution.

        Set well past anything reachable so truncated tail mass is negligible rather than
        merely small: the single-game records are 21 receptions and 45 carries.
        """
        return {Market.RECEPTIONS: 24, Market.RUSH_ATT: 50}.get(self, 0)


# Games of history before a player's own rates outweigh the positional mean. Volume
# stabilises quickly; efficiency needs far more evidence, hence the two constants.
# Volume is a ROLE, not a skill estimate, and roles differ enormously between players.
# Shrinking toward a league mean only pays when between-player variance is small, so for
# receiving and rushing the optimal shrinkage is zero: pulling a three-target receiver
# toward the league-average target count invents volume he has never had.
#
# This was measured, not assumed. The old flat 4.0 over-projected small players badly and
# left big ones alone -- rushing ran at 0.73 actual/projected under 20 yards against 1.00
# above 60, and the distortion faded as history accumulated, which is the signature of the
# league-mean pull. Setting it to zero flattens that profile and improves both in-sample
# and 2025-holdout error:
#
#   rushing    MAE 19.43 -> 19.04, holdout 18.96 -> 18.69, under-20 bucket 0.73 -> 1.01
#   receiving  MAE 20.07 -> 19.87, holdout 19.38 -> 19.26, under-20 bucket 0.83 -> 1.01
#
# Passing keeps a small term, and for a reason that supports the same rule: starting
# quarterbacks nearly all throw 25-40 times, so attempts really are homogeneous and the
# league mean is a fair guess. Efficiency is the opposite case -- a genuinely noisy skill
# estimate -- which is why it is still regressed hard below.
VOLUME_SHRINKAGE = {
    "recv_yds": 0.0,
    "rush_yds": 0.0,
    "pass_yds": 2.0,
}
EFFICIENCY_SHRINKAGE = 12.0

# Recency half-life in games. Roughly half a season: recent usage matters, but a
# three-week sample alone is too jumpy to project from.
HALF_LIFE = 8.0

# A defence needs this many games before its adjustment is trusted at all.
MIN_DEFENCE_GAMES = 8

# Venue and weather multipliers, measured WITHIN PLAYER -- the same player compared
# across venues, so team quality and talent cancel out. Raw league averages overstate
# these badly: cold-weather receiving looks 10.7% down in aggregate but only 3.6% within
# player, because cold games are late-season games involving different teams.
#
# Indoor, from players with 5+ games in each venue:
#   WR receiving +11.2% (n=218), TE +8.3% (n=112), QB passing +5.1% (n=58),
#   RB rushing +2.2% (n=142)
INDOOR_FACTOR = {
    "recv_yds": 1.10,
    "pass_yds": 1.05,
    "rush_yds": 1.02,
}

# Cold (under 40F, outdoors), from players with 4+ games either side:
#   receiving -3.6% (n=195), passing -5.5% (n=31), rushing +8.8% (n=71)
# Cold shifts offences toward the run, which is why rushing moves the other way. The
# passing and rushing figures rest on small samples and are shrunk toward 1.0.
COLD_THRESHOLD_F = 40.0
COLD_FACTOR = {
    "recv_yds": 0.965,
    "pass_yds": 0.96,
    "rush_yds": 1.05,
}

# How far an opponent adjustment may move a projection. Defensive splits are noisy and
# an uncapped multiplier lets one extreme defence dominate the estimate.
#
# NOTE: this cap now only bounds a DISPLAYED number. The opponent factor is no longer
# applied to the projection -- see APPLY_OPPONENT_FACTOR below.
MAX_OPPONENT_EFFECT = 0.25

# Whether the opponent factor multiplies the projection. It does not, and this is the
# most surprising result in the model.
#
# Four independent constructions were tested walk-forward over 2021-2025, and every one
# of them made projections WORSE than simply ignoring the opponent:
#
#   raw yards allowed per player-game   rushing +0.34%, receiving +0.16%, passing +1.02%
#   yards allowed per attempt           +0.01%, +0.16%, +0.32%
#   shrunk by defence sample size       monotonically better the more it was shrunk
#   residual-based (yards allowed vs
#     what those players normally do)   +0.46%, +0.24%, +1.10%
#
# The residual version initially looked like a 2% win. It was not: the entire gain came
# from correcting a global over-projection that the factor was absorbing incidentally.
# Once that bias was normalised out, the defence term was worse than nothing again, and
# stacking it on top of the real fix was worse than the real fix alone.
#
# So the factor is still computed and still shown, because "who is he playing" is a fair
# thing to want to see, but it is not allowed to move the number. Turning this on will
# make the model measurably worse.
APPLY_OPPONENT_FACTOR = False

# Overdispersion for the count markets: variance / mean.
#
# The two markets differ and cannot share a constant. Rush attempts are driven by game
# script -- a blowout in either direction moves them hard -- while receptions track a
# receiver's role far more closely.
#
# These were fitted on calibration, and that matters, because the obvious way to set them
# gives the wrong answer. A player's own game-to-game variance/mean is 1.06 for receptions
# and 1.76 for rush attempts, but the distribution has to cover our projection error too,
# not only the player's inherent spread. Fitting on the worst calibration gap lands
# meaningfully higher in both cases:
#
#                    var/mean   fitted    worst gap at fitted (full / 2025 holdout)
#   receptions         1.06      1.25          0.023 / 0.036
#   rush attempts      1.76      2.00          0.024 / 0.034
#
# Using the raw ratios instead would have left both markets overconfident -- receptions at
# 0.040 and rush attempts at 0.114, the latter nearly five times worse than it needs to be.
#
# A value at or below 1.0 makes `_nb_pmf` collapse to Poisson exactly. Re-fit with the
# dispersion sweep before changing these.
COUNT_DISPERSION = {
    "receptions": 1.25,
    "rush_att": 2.00,
}

# Residual scale correction, fitted on 2021-2024 and checked on 2025.
#
# Even with volume shrinkage fixed, projections run a few percent hot: volume x efficiency
# reconstructs a player's average game, and the average game of a player who is on the
# field is better than the average game he actually turns in once minor injuries, blowouts
# and early exits are counted.
#
# This is applied as a flat multiplier ONLY because the residual bias is now flat. Before
# the volume-shrinkage fix it was not -- rushing ran at 0.73 actual/projected under 20
# yards and 1.00 above 60 -- and a single constant would have dragged the accurate
# projections down to patch the broken ones. Fixing the shape first is what makes a scalar
# legitimate here.
#
# Fitted values were stable year to year (receiving 0.944/0.951/0.937/0.959, rushing
# 1.020/0.967/0.918/0.974), and the 2025 holdout came in slightly lower than the fit,
# so the correction is mildly conservative rather than overfitted.
SCALE_CORRECTION = {
    "recv_yds": 0.948,
    "rush_yds": 0.963,
    # Both count markets run a touch hot for the same reason the yards markets did, and
    # the correction is legitimate here by the same rule: the bias is uniform rather than
    # concentrated at one end. 0.98 was chosen over 0.96 because it is the value that holds
    # up on BOTH the full replay and the 2025 holdout rather than the best single number on
    # either -- it takes receptions from bias +0.080 to +0.020 and rush attempts from +0.140
    # to -0.011, and improves the holdout gap for both.
    "receptions": 0.98,
    "rush_att": 0.98,
    # Passing is deliberately left uncorrected. Its mean runs hot like the others, but
    # its quantiles do not -- it was the best-calibrated market before any correction
    # (worst gap 0.036) and applying the fitted 0.971 nearly doubled that to 0.067. The
    # mean and the quantiles disagree here because the gamma's right tail carries the
    # bias, and it is the quantiles that decide an over/under.
    "pass_yds": 1.0,
}

# Spread multiplier, fitted on 2021-2024 and checked on 2025.
#
# The raw player spread understated how often low outcomes happen even after
# conditioning on participation. 1.15 widens the distribution to match; the value was
# chosen on the fit years alone, and receiving-yard calibration then held on the 2025
# holdout to within sampling error.
# Re-fitted per market after the volume-shrinkage and scale fixes. The old single 1.15
# was chosen while a systematic over-projection was still in the model, so it was partly
# compensating for bias rather than describing spread.
#
# With the bias gone the two effects separate cleanly, and the markets disagree: receiving
# and rushing outcomes are wider than the fitted gamma (too few clear a low line, too many
# clear a high one), while passing needs no widening at all -- quarterbacks are the
# consistent performers, and the 0.35 CV floor already covers them.
#
# Chosen on the full period and confirmed on a 2025 holdout scored with the same warm-up
# the live model gets. Worst calibration gap on that holdout:
#
#              x1.00   x1.15   x1.25   x1.35
#   receiving  0.076   0.036   0.016   0.014
#   rushing    0.059   0.019   0.017   0.021
#   passing    0.020   0.028   0.036   0.045
#
# Receiving is marginally better still at 1.35, but that value is worse in-sample, so 1.25
# is taken as the value that holds up both ways rather than the best single number.
CV_MULTIPLIER = {
    "recv_yds": 1.25,
    "rush_yds": 1.25,
    "pass_yds": 1.00,
}


class Calibration(str, Enum):
    """How far each market can actually be trusted, measured on a held-out season.

    Published with the projection because a probability whose reliability is unknown is
    worse than no probability -- it invites staking on a number that has never been
    checked.
    """

    VALIDATED = "validated"
    PROVISIONAL = "provisional"


# All three numbers below are the WORST calibration gap over the full 2021-2025 replay,
# measured the same way for every market.
#
# They were not always comparable. The shipped receiving figure (0.035) was a mean gap
# while rushing's (0.069) was a max gap, so the three constants were never on the same
# scale and receiving's bar was roughly half what it should have been. Everything here is
# now the max, which is the conservative reading and matches how `calibrated` is defined.
#
# Current state, after fixing volume shrinkage, removing the opponent factor, correcting
# the residual scale, and re-fitting spread per market:
#
#                worst gap    2025 holdout    MAE      baseline
#   receiving      0.019         0.016       19.51      19.75
#   rushing        0.026         0.017       18.82      19.04
#   passing        0.036         0.020       64.53      65.22
#
#                receptions     0.013         0.028        1.44       1.45
#                rush attempts  0.028         0.026        3.10       3.12
#
# Each bar is the WORSE of the two columns, rounded UP to three decimals. Rounding up
# matters: receiving measures 0.0191 and rushing 0.0260, so rounding to nearest would have
# set bars marginally BELOW the error they stand for. A bar that understates our own
# measured error defeats the point of having one.
#
# Not the full-period figure, either. For the three yards
# markets those coincide, since the holdout came in better. Receptions is the exception --
# 0.013 across the full replay but 0.028 on 2025 -- and taking the flattering number there
# would have halved its bar on the strength of the years it was fitted through.
#
# Worth reading honestly: receptions is measurably the easiest market to PROJECT (relative
# error 0.504 against receiving yards' 0.624) and that did not translate into a tighter
# bar. Being easier to model is not the same as being better understood out of sample.
#
# The two count markets tie their baseline on MAE rather than beating it, and for rush
# attempts that is true by construction -- the projection is the player's decayed carry
# count, which is exactly what the baseline is. Their value is not a better mean, it is a
# correctly discrete distribution around it: at a 2.5 line the gamma used for yards is
# simply the wrong shape.
#
# These numbers are the grading bar, so the system self-corrects: a market that calibrates
# worse automatically demands a larger edge before a bet is called actionable.
MARKET_CALIBRATION: dict[str, tuple[Calibration, float, str]] = {
    "recv_yds": (
        Calibration.VALIDATED,
        0.020,
        "Best calibrated of the three: worst deviation 1.9 points over 16,689 replayed "
        "player-games, and 1.6 on 3,734 held out of fitting. Now beats the player's own "
        "average. Treat probabilities as good to within about two points.",
    ),
    "rush_yds": (
        Calibration.VALIDATED,
        0.027,
        "Worst deviation 2.6 points over 7,923 replayed games, 1.7 on 1,785 held out. "
        "Now beats the player's own average, which it did not before the volume-shrinkage "
        "fix. Usable for sizing, with a slightly wider bar than receiving.",
    ),
    "receptions": (
        Calibration.VALIDATED,
        0.028,
        "Best calibrated of all five across the full replay (1.3 points) but 2.8 on the "
        "2025 holdout, and the bar takes the worse of the two. Modelled as targets x catch "
        "rate, which is far better behaved than yards per target. Lines sit at 2.5 and 3.5, "
        "where the discrete distribution matters more than the projection.",
    ),
    "rush_att": (
        Calibration.VALIDATED,
        0.028,
        "Worst deviation 2.8 points over 7,923 replayed games, 2.6 on 1,785 held out. Pure "
        "volume -- the projection is the player's own decayed carry count, so it ties the "
        "baseline mean by construction and earns its keep on the distribution rather than "
        "the number. The most overdispersed market of the five, because game script moves "
        "carries hard in both directions.",
    ),
    "pass_yds": (
        Calibration.PROVISIONAL,
        0.036,
        "Worst deviation 3.6 points, on only 2,447 replayed games and 553 held out -- the "
        "thinnest sample of the three, since one quarterback covers a whole team. Its "
        "quantiles calibrate well but its mean still runs about 8 yards hot, so read the "
        "50/50 point rather than the average, and demand a large edge.",
    ),
}


@dataclass
class PlayerForm:
    """A player's recency-weighted usage and efficiency."""

    player_id: str
    player_name: str
    position: str | None
    team: str | None
    games: int
    volume: float          # attempts / carries / targets per game
    efficiency: float      # yards per attempt / carry / target
    yards: float           # observed yards per game
    yards_sd: float
    snap_pct: float | None
    target_share: float | None
    recent_yards: list[float]

    @property
    def band(self) -> Band:
        return games_band(self.games)


def games_band(games: int) -> Band:
    """Sample banding for a *continuous* projection built from game history.

    Deliberately not `stats.sample_band`, whose 30/100/300 thresholds are calibrated for
    binomial hit rates. Estimating a mean from game logs converges far faster than
    estimating a proportion, so a full 17-game season is a solid basis for a projection
    even though it would be a hopeless sample for a win-rate split. Reusing the binomial
    thresholds here labelled a complete season as "noise", which is both wrong and the
    kind of warning that gets ignored once it cries wolf.
    """
    if games < 4:
        return Band.NOISE
    if games < 8:
        return Band.SUGGESTIVE
    if games < 16:
        return Band.MODERATE
    return Band.MEANINGFUL


def _decayed(values: list[float], half_life: float = HALF_LIFE) -> tuple[float, float]:
    """(weighted mean, weighted sd) with recent observations counting for more."""
    if not values:
        return 0.0, 0.0
    decay = 0.5 ** (1.0 / half_life)
    weight = 0.0
    total = 0.0
    # Oldest first, so the most recent game ends with weight 1.
    for i, v in enumerate(reversed(values)):
        w = decay**i
        total += w * v
        weight += w
    mean = total / weight if weight else 0.0

    var_total = 0.0
    for i, v in enumerate(reversed(values)):
        w = decay**i
        var_total += w * (v - mean) ** 2
    variance = var_total / weight if weight else 0.0
    return mean, math.sqrt(max(variance, 0.0))


def player_form(
    session: Session,
    player_id: str,
    market: Market,
    *,
    season: int,
    week: int,
    lookback_seasons: int = 1,
) -> PlayerForm | None:
    """Build a player's form from games strictly before the given week."""
    rows = session.scalars(
        select(NflPlayerGame)
        .where(
            NflPlayerGame.player_id == player_id,
            NflPlayerGame.season.in_(range(season - lookback_seasons, season + 1)),
        )
        .order_by(NflPlayerGame.season, NflPlayerGame.week)
    ).all()

    history = [
        r for r in rows if r.season < season or (r.season == season and r.week < week)
    ]
    if not history:
        return None

    stat = market.stat
    volume_attr = market.volume

    all_yards = [float(getattr(r, stat) or 0.0) for r in history]
    all_volumes = [float(getattr(r, volume_attr) or 0.0) for r in history]

    # Everything is conditioned on the player actually taking part.
    #
    # 16% of running-back games have zero carries and 14% of receiver games zero targets
    # -- inactive, injured, or simply not used. A gamma with a positive mean cannot
    # produce that spike at zero, and including those games wrecked calibration below the
    # mean (we said 37% to clear our own projection; reality was 27%).
    #
    # Conditioning is also what the market does: a book voids a player prop if the player
    # does not play, so "given he plays" is the question the bet actually asks.
    played = [
        (y, v) for y, v in zip(all_yards, all_volumes, strict=True) if v > 0
    ]
    if not played:
        return None

    yards = [y for y, _ in played]
    volumes = [v for _, v in played]
    involved = played

    mean_yards, sd_yards = _decayed(yards)
    mean_volume, _ = _decayed(volumes)

    # Efficiency is decayed on the same half-life as volume.
    #
    # It used to be a flat career rate while volume was recency-weighted, which made the
    # two inconsistent: a back averaging 49 yards a game recently projected to 42 because
    # his recent yards-per-carry was well above his career figure and the product ignored
    # that. Weighting both the same way makes volume x efficiency reconcile with observed
    # yards per game by construction.
    weighted_yards, _ = _decayed([y for y, _ in involved]) if involved else (0.0, 0.0)
    weighted_volume, _ = _decayed([v for _, v in involved]) if involved else (0.0, 0.0)
    efficiency = weighted_yards / weighted_volume if weighted_volume > 0 else 0.0

    # Usage is read from games the player was active for, for the same reason.
    active = [r for r in history if (getattr(r, volume_attr) or 0) > 0]
    snaps = [r.snap_pct for r in active if r.snap_pct is not None]
    shares = [r.target_share for r in active if r.target_share is not None]
    latest = active[-1] if active else history[-1]

    return PlayerForm(
        player_id=player_id,
        player_name=latest.player_name,
        position=latest.position,
        team=latest.team,
        games=len(played),
        volume=mean_volume,
        efficiency=efficiency,
        yards=mean_yards,
        yards_sd=sd_yards,
        snap_pct=_decayed(snaps)[0] if snaps else None,
        target_share=_decayed(shares)[0] if shares else None,
        recent_yards=yards[-8:],
    )


@dataclass
class PositionBaseline:
    """League-wide rates for a position, used as the shrinkage target."""

    volume: float
    efficiency: float
    yards: float
    cv: float  # coefficient of variation, which sets the distribution's shape


def position_baseline(
    session: Session, market: Market, *, season: int, min_volume: float = 1.0
) -> PositionBaseline:
    """League-average volume, efficiency, and variability for this market."""
    rows = session.scalars(
        select(NflPlayerGame).where(
            NflPlayerGame.season < season,
            NflPlayerGame.season >= season - 3,
            NflPlayerGame.position.in_(market.positions),
        )
    ).all()

    stat, vol = market.stat, market.volume
    pairs = [
        (float(getattr(r, stat) or 0.0), float(getattr(r, vol) or 0.0))
        for r in rows
        if (getattr(r, vol) or 0) >= min_volume
    ]  # min_volume >= 1 already conditions on participation
    if not pairs:
        return PositionBaseline(volume=1.0, efficiency=1.0, yards=1.0, cv=0.7)

    yards = [y for y, _ in pairs]
    volumes = [v for _, v in pairs]
    mean_yards = sum(yards) / len(yards)
    variance = sum((y - mean_yards) ** 2 for y in yards) / max(len(yards) - 1, 1)

    return PositionBaseline(
        volume=sum(volumes) / len(volumes),
        efficiency=sum(yards) / sum(volumes) if sum(volumes) else 1.0,
        yards=mean_yards,
        cv=math.sqrt(variance) / mean_yards if mean_yards else 0.7,
    )


def defence_adjustments(
    session: Session, market: Market, *, season: int, week: int
) -> dict[str, float]:
    """Multiplier per defence: what they allow in this market, relative to league.

    Computed from every game a defence has played rather than from the handful a
    specific player has played against them, which is the only way to get a usable
    sample at this level.
    """
    rows = session.scalars(
        select(NflPlayerGame)
        .where(
            NflPlayerGame.season.in_((season - 1, season)),
            NflPlayerGame.position.in_(market.positions),
        )
        .order_by(NflPlayerGame.season, NflPlayerGame.week)
    ).all()

    stat = market.stat
    allowed: dict[str, list[float]] = defaultdict(list)
    weeks_seen: dict[str, set[tuple[int, int]]] = defaultdict(set)

    for r in rows:
        if r.season > season or (r.season == season and r.week >= week):
            continue
        value = getattr(r, stat)
        if value is None:
            continue
        allowed[r.opponent].append(float(value))
        weeks_seen[r.opponent].add((r.season, r.week))

    league = [v for values in allowed.values() for v in values]
    if not league:
        return {}
    league_mean = sum(league) / len(league)
    if league_mean <= 0:
        return {}

    out: dict[str, float] = {}
    for team, values in allowed.items():
        if len(weeks_seen[team]) < MIN_DEFENCE_GAMES:
            continue
        ratio = (sum(values) / len(values)) / league_mean
        # Cap the effect: defensive splits are noisy and one extreme should not swamp
        # a projection built on far more evidence.
        out[team] = max(1 - MAX_OPPONENT_EFFECT, min(1 + MAX_OPPONENT_EFFECT, ratio))
    return out


# ---------------------------------------------------------------- distribution
def _gamma_cdf(x: float, shape: float, scale: float) -> float:
    """Regularised lower incomplete gamma, by series and continued fraction.

    Written out rather than pulled from scipy to keep the image small; both branches are
    the standard Numerical Recipes formulations and agree to well under a basis point.
    """
    if x <= 0:
        return 0.0
    if shape <= 0 or scale <= 0:
        return 0.0
    a = shape
    z = x / scale

    if z < a + 1.0:
        # Series expansion converges quickly below the mean.
        term = 1.0 / a
        total = term
        n = a
        for _ in range(400):
            n += 1.0
            term *= z / n
            total += term
            if abs(term) < abs(total) * 1e-12:
                break
        return total * math.exp(-z + a * math.log(z) - math.lgamma(a))

    # Continued fraction above the mean.
    tiny = 1e-300
    b = z + 1.0 - a
    c = 1.0 / tiny
    d = 1.0 / b
    h = d
    for i in range(1, 400):
        an = -i * (i - a)
        b += 2.0
        d = an * d + b
        if abs(d) < tiny:
            d = tiny
        c = b + an / c
        if abs(c) < tiny:
            c = tiny
        d = 1.0 / d
        delta = d * c
        h *= delta
        if abs(delta - 1.0) < 1e-12:
            break
    q = math.exp(-z + a * math.log(z) - math.lgamma(a)) * h
    return 1.0 - q


@dataclass
class PropProjection:
    """A distribution over a player's yardage, plus the reasoning behind it."""

    player_id: str
    player_name: str
    position: str | None
    team: str | None
    opponent: str
    market: Market

    expected: float
    sd: float
    games_of_history: int
    band: Band

    projected_volume: float
    projected_efficiency: float
    opponent_factor: float
    context_factor: float

    snap_pct: float | None
    target_share: float | None
    recent_yards: list[float]
    notes: list[str]

    @property
    def calibration(self) -> Calibration:
        return MARKET_CALIBRATION[self.market.value][0]

    @property
    def calibration_note(self) -> str:
        return MARKET_CALIBRATION[self.market.value][2]

    @property
    def worst_calibration_gap(self) -> float:
        return MARKET_CALIBRATION[self.market.value][1]

    @property
    def shape(self) -> float:
        """Gamma shape. High shape is near-symmetric, low shape is strongly skewed."""
        if self.sd <= 0 or self.expected <= 0:
            return 0.0
        return (self.expected / self.sd) ** 2

    @property
    def scale(self) -> float:
        if self.expected <= 0:
            return 0.0
        return (self.sd**2) / self.expected

    def prob_over(self, line: float) -> float | None:
        """P(outcome > line). None when there is not enough to model."""
        if self.expected <= 0 or self.sd <= 0 or line < 0:
            return None
        if self.market.discrete:
            from app.services.projections_props import prob_over_line

            return prob_over_line(
                self.expected,
                line,
                dispersion=COUNT_DISPERSION.get(self.market.value, 1.0),
                max_count=self.market.max_count,
            )
        return 1.0 - _gamma_cdf(line, self.shape, self.scale)

    @property
    def median(self) -> float | None:
        """The 50/50 point -- the number that actually decides an over/under.

        Reported alongside the mean because for a right-skewed distribution they are not
        the same, and quoting only the mean is actively misleading: a receiver can project
        for 30 yards on average while going under 28.5 most weeks, because his typical
        game sits well below his average one. Real receiving yards have a median around
        75% of the mean.
        """
        if self.expected <= 0 or self.sd <= 0:
            return None
        if self.market.discrete:
            # For a count the median is an integer: the smallest k with CDF >= 0.5. There
            # is nothing to bisect, and reporting a fractional median next to a 2.5 line
            # would invite reading it as a yards figure.
            from app.services.projections_props import distribution

            cumulative = 0.0
            dist = distribution(
                self.expected,
                dispersion=COUNT_DISPERSION.get(self.market.value, 1.0),
                max_count=self.market.max_count,
            )
            for k, pk in enumerate(dist):
                cumulative += pk
                if cumulative >= 0.5:
                    return float(k)
            return float(len(dist) - 1)
        lo, hi = 0.0, self.expected * 6.0
        for _ in range(60):
            mid = (lo + hi) / 2.0
            if _gamma_cdf(mid, self.shape, self.scale) < 0.5:
                lo = mid
            else:
                hi = mid
        return (lo + hi) / 2.0

    def percentile(self, line: float) -> float | None:
        p = self.prob_over(line)
        return None if p is None else 1.0 - p

    @property
    def trustworthy(self) -> bool:
        return self.band.trustworthy and self.games_of_history >= 6


def project_prop(
    session: Session,
    player_id: str,
    market: Market,
    *,
    season: int,
    week: int,
    opponent: str,
    is_home: bool = True,
    roof: str | None = None,
    wind: float | None = None,
    temp: float | None = None,
) -> PropProjection | None:
    """Project one player's yardage distribution for one matchup.

    Volume is taken as the player's own (it is a role, not an estimate), efficiency is
    regressed hard toward the positional mean, and the opponent is measured but not
    applied -- it failed to beat ignoring it in every construction tested.
    """
    form = player_form(session, player_id, market, season=season, week=week)
    if form is None or form.games == 0:
        return None

    baseline = position_baseline(session, market, season=season)
    notes: list[str] = []

    # Volume is the player's own. Shrinking it toward the league mean was measured to
    # over-project low-usage players by up to 27% while leaving starters untouched.
    shrink = VOLUME_SHRINKAGE.get(market.value, 0.0)
    if shrink > 0:
        vw = form.games / (form.games + shrink)
        volume = vw * form.volume + (1 - vw) * baseline.volume
    else:
        volume = form.volume

    # Efficiency is mostly noise at this sample size and is pulled hard to the mean.
    ew = form.games / (form.games + EFFICIENCY_SHRINKAGE)
    efficiency = (
        ew * form.efficiency + (1 - ew) * baseline.efficiency
        if form.efficiency > 0
        else baseline.efficiency
    )

    # Measured for display only. Applying it made every market worse -- see
    # APPLY_OPPONENT_FACTOR.
    adjustments = defence_adjustments(session, market, season=season, week=week)
    opponent_factor = adjustments.get(opponent, 1.0)
    if opponent in adjustments and abs(opponent_factor - 1.0) >= 0.05:
        direction = "generous" if opponent_factor > 1 else "stingy"
        notes.append(
            f"{opponent} has been {direction} here ({opponent_factor - 1:+.0%} vs league), "
            f"shown for context but not applied -- it does not predict out of sample"
        )

    context = 1.0
    indoors = roof in ("dome", "closed")

    if indoors:
        factor = INDOOR_FACTOR.get(market.value, 1.0)
        if factor != 1.0:
            context *= factor
            notes.append(f"indoors ({factor:+.0%} historically for this market)".replace("+1", "+"))
    else:
        # Wind suppresses passing far more than running.
        if wind is not None and wind >= 15:
            if market is Market.PASS_YDS:
                context *= 0.94
                notes.append(f"high wind ({wind:.0f}mph) suppresses passing")
            elif market is Market.RECV_YDS:
                context *= 0.96
                notes.append(f"high wind ({wind:.0f}mph) suppresses receiving")
        # Cold pushes offences toward the run.
        if temp is not None and temp < COLD_THRESHOLD_F:
            factor = COLD_FACTOR.get(market.value, 1.0)
            if factor != 1.0:
                context *= factor
                direction = "helps" if factor > 1 else "hurts"
                notes.append(f"cold ({temp:.0f}F) {direction} this market")

    applied_opponent = opponent_factor if APPLY_OPPONENT_FACTOR else 1.0
    scale = SCALE_CORRECTION.get(market.value, 1.0)
    expected = volume * efficiency * applied_opponent * context * scale

    if market.discrete:
        # Counts take their spread from the distribution itself: for a negative binomial
        # the variance is mean x dispersion, so there is no coefficient of variation to
        # estimate and none of the gamma machinery below applies. `sd` is still populated
        # so the dataclass and its consumers stay uniform across markets.
        sd = math.sqrt(max(expected, 0.0) * COUNT_DISPERSION.get(market.value, 1.0))
    else:
        # Variability: the player's own spread once there is enough of it, otherwise the
        # positional coefficient of variation.
        if form.games >= 8 and form.yards > 0 and form.yards_sd > 0:
            cv = form.yards_sd / form.yards
        else:
            cv = baseline.cv
            notes.append("variability taken from position, not enough player history")
        # Bound the shape, and bound it *after* the multiplier so the cap actually binds.
        #
        # A gamma with CV above 1.0 has shape below 1, which puts its mode at zero -- the
        # distribution then claims the single likeliest outcome for a starting receiver is
        # no yards at all. That is how a player whose real record is 40% under a line came
        # out at 63%. Capping CV at 1.0 keeps the shape unimodal with a mode above zero
        # while still allowing the genuine right skew.
        cv = max(0.35, min(1.0, cv * CV_MULTIPLIER.get(market.value, 1.0)))
        sd = expected * cv

    if form.games < 6:
        notes.append(f"only {form.games} prior games -- treat as indicative")

    return PropProjection(
        player_id=player_id,
        player_name=form.player_name,
        position=form.position,
        team=form.team,
        opponent=opponent,
        market=market,
        expected=expected,
        sd=sd,
        games_of_history=form.games,
        band=form.band,
        projected_volume=volume,
        projected_efficiency=efficiency,
        opponent_factor=opponent_factor,
        context_factor=context,
        snap_pct=form.snap_pct,
        target_share=form.target_share,
        recent_yards=form.recent_yards,
        notes=notes,
    )
