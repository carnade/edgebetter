"""Operational CLI: ingest, enrich, poll odds, compute edges, backtest.

Run inside the api container, e.g.
    docker compose exec api python -m app.cli ingest-mlb --date today
"""

from __future__ import annotations

import logging
from datetime import UTC, date, datetime, timedelta

import typer

from app.db import SessionLocal
from app.models import Sport

app = typer.Typer(add_completion=False, help="EdgeBetter operations")


def _setup_logging(verbose: bool = True) -> None:
    logging.basicConfig(
        level=logging.INFO if verbose else logging.WARNING,
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logging.getLogger("httpx").setLevel(logging.WARNING)


def _parse_date(value: str) -> date:
    if value in {"today", "now"}:
        return datetime.now(UTC).date()
    if value == "tomorrow":
        return datetime.now(UTC).date() + timedelta(days=1)
    if value == "yesterday":
        return datetime.now(UTC).date() - timedelta(days=1)
    return date.fromisoformat(value)


@app.command("seed")
def seed() -> None:
    """Seed the team crosswalk. Idempotent."""
    _setup_logging()
    from app.services.team_map import seed_teams

    with SessionLocal() as session:
        typer.echo(f"seeded {seed_teams(session)} teams")


@app.command("ingest-mlb")
def ingest_mlb_cmd(
    day: str = typer.Option("today", "--date", help="ISO date, or today/tomorrow/yesterday"),
    stats: bool = typer.Option(False, "--stats", help="also refresh team and pitcher stats"),
    days: int = typer.Option(1, "--days", help="number of days forward from --date"),
) -> None:
    """Ingest the MLB schedule, probable pitchers, and optionally season stats."""
    _setup_logging()
    from app.services import ingest_mlb
    from app.services.season_resolver import mlb_season

    start = _parse_date(day)
    with SessionLocal() as session:
        info = mlb_season()
        total = 0
        for offset in range(days):
            total += ingest_mlb.ingest_schedule(session, start + timedelta(days=offset), info.season)
        typer.echo(f"{total} games ingested for season {info.season}")

        if stats:
            typer.echo(f"{ingest_mlb.ingest_team_stats(session, info.season)} team stat rows")
            typer.echo(f"{ingest_mlb.ingest_qualified_pitchers(session, info.season)} pitchers")
            typer.echo(
                f"{ingest_mlb.ingest_probable_pitcher_details(session, info.season)} game logs"
            )


@app.command("ingest-nba")
def ingest_nba_cmd(
    day: str = typer.Option("today", "--date"),
    backfill: int | None = typer.Option(None, "--backfill", help="ESPN season year to backfill"),
    stats: bool = typer.Option(False, "--stats"),
    force: bool = typer.Option(False, "--force", help="re-fetch dates already complete"),
) -> None:
    """Ingest NBA scoreboards, or backfill a whole season."""
    _setup_logging()
    from app.services import ingest_nba
    from app.services.season_resolver import nba_season

    with SessionLocal() as session:
        info = nba_season()
        if backfill:
            typer.echo(f"backfilling season {backfill} (this takes a few minutes)")
            typer.echo(f"{ingest_nba.backfill_season(session, backfill, force=force)} games")
            typer.echo(f"{ingest_nba.ingest_team_stats(session, backfill)} team stat rows")
            return

        season = info.season if info.started else info.prior_season
        typer.echo(f"{ingest_nba.ingest_scoreboard(session, _parse_date(day), season)} games")
        if stats:
            typer.echo(f"{ingest_nba.ingest_team_stats(session, season)} team stat rows")


@app.command("enrich-nba")
def enrich_nba_cmd(season: int = typer.Option(..., "--season")) -> None:
    """Optional stats.nba.com enrichment. Failure is tolerated by design."""
    _setup_logging()
    from app.services.enrich_nba import enrich_team_ratings

    with SessionLocal() as session:
        written = enrich_team_ratings(session, season)
        typer.echo(
            f"{written} teams upgraded"
            if written
            else "enrichment unavailable or disabled; ESPN ratings stand"
        )


@app.command("ingest-odds")
def ingest_odds_cmd(
    sport: str = typer.Option(..., "--sport", help="mlb or nba"),
    force: bool = typer.Option(False, "--force", help="ignore the no-upcoming-games guard"),
) -> None:
    """Poll odds once, respecting the credit guard."""
    _setup_logging()
    from app.services.ingest_odds import poll_odds

    with SessionLocal() as session:
        result = poll_odds(session, Sport(sport.lower()), force=force)
        if not result.polled:
            typer.echo(f"skipped: {result.reason}")
            raise typer.Exit(0)
        typer.echo(
            f"{result.snapshots_written} snapshots across {result.games_matched} games; "
            f"cost {result.credits_used} credits, {result.credits_remaining} remaining"
        )


@app.command("edges")
def edges_cmd(sport: str = typer.Option(..., "--sport")) -> None:
    """Recompute edges for upcoming games."""
    _setup_logging()
    from app.services.edges import recompute_edges

    with SessionLocal() as session:
        typer.echo(f"{recompute_edges(session, Sport(sport.lower()))} edges")


@app.command("backtest")
def backtest_cmd(
    sport: str = typer.Argument("nba"),
    season: int = typer.Option(2026, "--season"),
    raw: bool = typer.Option(False, "--raw", help="report the model before bias correction"),
) -> None:
    """Walk-forward backtest. Uses only games played before each fixture."""
    _setup_logging(verbose=False)
    if sport.lower() == "mlb":
        from app.services.backtest import backtest_mlb

        with SessionLocal() as session:
            result = backtest_mlb(session, season)
            typer.echo(f"MLB {season} walk-forward backtest (team level, no pitchers)")
            typer.echo(result.summary())
            if result.total_mae >= result.baseline_mae:
                typer.echo(
                    "\n  NOTE: team-level projections show no skill over a league-average\n"
                    "  baseline. In baseball the starting pitcher dominates, so treat the\n"
                    "  MLB model as a weak signal and rely on the devigged market consensus."
                )
        raise typer.Exit(0)

    if sport.lower() != "nba":
        typer.echo("backtesting supports nba or mlb")
        raise typer.Exit(1)

    from app.services.backtest import backtest_nba, calibration_report, suggest_constants
    from app.services.projections_nba import TOTAL_SIGMA

    with SessionLocal() as session:
        result = backtest_nba(session, season, apply_corrections=not raw)
        typer.echo(f"NBA {season} walk-forward backtest ({'raw' if raw else 'corrected'})")
        typer.echo(result.summary())
        typer.echo("\ncalibration:")
        typer.echo(calibration_report(result, TOTAL_SIGMA))
        typer.echo("\nconstants:")
        typer.echo(suggest_constants(result))


@app.command("nfl-ingest")
def nfl_ingest_cmd(
    start_season: int = typer.Option(2020, "--from", help="earliest season to load"),
    pbp: bool = typer.Option(False, "--pbp", help="also derive EPA and half/quarter scoring"),
    injuries: bool = typer.Option(False, "--injuries"),
) -> None:
    """Load NFL games from nflverse. No API key, no credits."""
    _setup_logging()
    from app.services.ingest_nfl import (
        ingest_games,
        ingest_injuries,
        ingest_pbp_season,
        sanity_check,
    )

    with SessionLocal() as session:
        typer.echo(str(ingest_games(session, start_season=start_season)))

        seasons = range(start_season, datetime.now(UTC).year + 1)
        if pbp:
            for season in seasons:
                try:
                    typer.echo(f"  {season} pbp: {ingest_pbp_season(session, season)} team-games")
                except Exception as exc:  # noqa: BLE001 - a missing season must not abort
                    typer.echo(f"  {season} pbp unavailable: {exc}")
        if injuries:
            for season in seasons:
                typer.echo(f"  {season} injuries: {ingest_injuries(session, season)} rows")

        typer.echo("\nsanity checks:")
        for season in seasons:
            problems = sanity_check(session, season)
            typer.echo(f"  {season}: {'clean' if not problems else '; '.join(problems)}")


@app.command("nfl-splits")
def nfl_splits_cmd(
    wind_min: float = typer.Option(None, "--wind-min"),
    wind_max: float = typer.Option(None, "--wind-max"),
    temp_min: float = typer.Option(None, "--temp-min"),
    temp_max: float = typer.Option(None, "--temp-max"),
    roof: str = typer.Option(None, "--roof", help="outdoors, dome, closed, open"),
    surface: str = typer.Option(None, "--surface"),
    outdoor: bool = typer.Option(False, "--outdoor", help="restrict to outdoor games"),
    div: bool = typer.Option(None, "--div/--no-div", help="divisional games only"),
    home: bool = typer.Option(None, "--home/--away"),
    favourite: bool = typer.Option(None, "--fav/--dog"),
    rest_adv: int = typer.Option(None, "--rest-adv", help="minimum rest advantage in days"),
    team: str = typer.Option(None, "--team"),
    opponent: str = typer.Option(None, "--opp"),
    team_total: float = typer.Option(23.5, "--team-total", help="team-total line to test"),
    vs_baseline: bool = typer.Option(
        True, "--compare/--no-compare", help="contrast against unconditioned games"
    ),
) -> None:
    """Historical base rates under a set of conditions.

    Every rate is reported with a confidence interval, a sample band, and a holdout
    check against 2025. A percentage on its own is not a finding.
    """
    _setup_logging(verbose=False)
    from app.services.nfl_splits import Filters, analyse, compare

    filters = Filters(
        wind_min=wind_min, wind_max=wind_max, temp_min=temp_min, temp_max=temp_max,
        roof=roof, surface=surface, outdoor_only=outdoor, div_game=div, is_home=home,
        is_favourite=favourite, rest_advantage_min=rest_adv, team=team,
        opponent=opponent, team_total_line=team_total,
    )

    with SessionLocal() as session:
        typer.echo(analyse(session, filters).format())
        if vs_baseline:
            baseline = Filters(outdoor_only=outdoor, team_total_line=team_total)
            typer.echo("\n" + compare(session, baseline, filters))


@app.command("nfl-odds")
def nfl_odds_cmd(
    seed: bool = typer.Option(False, "--seed", help="seed opening lines from nflverse first"),
    days: int = typer.Option(10, "--days", help="lookahead window"),
) -> None:
    """Poll consensus NFL lines and append them to the movement history."""
    _setup_logging()
    from app.services.ingest_nfl_odds import poll_nfl_odds, seed_openers
    from app.services.season_resolver import nba_season  # noqa: F401 - season helper

    with SessionLocal() as session:
        if seed:
            season = datetime.now(UTC).year
            typer.echo(f"{seed_openers(session, season)} openers seeded")
        typer.echo(poll_nfl_odds(session, lookahead_days=days).summary())


@app.command("nfl-clv")
def nfl_clv_cmd(season: int = typer.Option(None, "--season")) -> None:
    """Has the market drifted toward the side we took at the open?"""
    _setup_logging(verbose=False)
    from app.services.nfl_clv import clv_report

    with SessionLocal() as session:
        typer.echo(clv_report(session, season=season).summary())


@app.command("nfl-backtest")
def nfl_backtest_cmd(
    holdout: int = typer.Option(2025, "--holdout", help="season withheld from fitting"),
) -> None:
    """Walk-forward test of the NFL model against the closing line."""
    _setup_logging(verbose=False)
    from app.services.nfl_backtest import backtest_totals

    with SessionLocal() as session:
        typer.echo(backtest_totals(session, holdout_season=holdout).summary())


@app.command("slate")
def slate_cmd(sport: str = typer.Option("mlb", "--sport")) -> None:
    """Print today's slate with model projections -- a quick end-to-end check."""
    _setup_logging(verbose=False)
    from sqlalchemy import select

    from app.models import Game
    from app.services import projections_mlb, projections_nba

    sport_enum = Sport(sport.lower())
    now = datetime.now(UTC)
    with SessionLocal() as session:
        games = session.scalars(
            select(Game)
            .where(
                Game.sport == sport_enum,
                Game.start_time >= now - timedelta(hours=6),
                Game.start_time <= now + timedelta(hours=36),
            )
            .order_by(Game.start_time)
        ).all()
        if not games:
            typer.echo(f"no upcoming {sport} games ingested")
            raise typer.Exit(0)

        for game in games:
            if sport_enum is Sport.NBA:
                proj = projections_nba.project(
                    session, game.home_team_id, game.away_team_id, game.season
                )
            else:
                proj = projections_mlb.project(
                    session, game.home_team_id, game.away_team_id, game.season,
                    home_pitcher_id=game.home_probable_pitcher_id,
                    away_pitcher_id=game.away_probable_pitcher_id,
                )
            line = f"{game.start_time:%m-%d %H:%M} {game.away_team.abbrev:>3s} @ {game.home_team.abbrev:<3s}"
            if proj is None:
                typer.echo(f"{line}   (no projection: missing stats)")
                continue
            if sport_enum is Sport.MLB:
                hp = game.home_probable_pitcher
                ap = game.away_probable_pitcher
                names = f"{(ap.full_name if ap else '?'):<18s} vs {(hp.full_name if hp else '?'):<18s}"
                typer.echo(
                    f"{line}  {names} proj {proj.away_runs:4.1f}-{proj.home_runs:4.1f} "
                    f"total {proj.total:4.1f}  P(home) {proj.prob_home_win():.3f}"
                )
            else:
                typer.echo(
                    f"{line}  proj {proj.away_points:5.1f}-{proj.home_points:5.1f} "
                    f"total {proj.total:5.1f}  P(home) {proj.prob_home_win():.3f}"
                )


@app.command("nfl-props")
def nfl_props_cmd(
    week: int = typer.Option(None, "--week"),
    limit: int = typer.Option(None, "--limit", help="cap games polled"),
    dry_run: bool = typer.Option(True, "--dry-run/--live", help="dry run by default"),
    days: int = typer.Option(10, "--days", help="lookahead window"),
) -> None:
    """Poll player prop lines. Dry run by default -- these cost credits per game."""
    _setup_logging()
    from app.services.ingest_nfl_props import poll_nfl_props

    with SessionLocal() as session:
        result = poll_nfl_props(
            session, week=week, limit=limit, dry_run=dry_run, lookahead_days=days
        )
        typer.echo(result.summary())
        for note in result.skipped[:5]:
            typer.echo(f"  skipped: {note}")


@app.command("nfl-scan")
def nfl_scan_cmd(
    week: int = typer.Option(None, "--week"),
    season: int = typer.Option(None, "--season"),
    top: int = typer.Option(15, "--top"),
) -> None:
    """Grade every posted prop line for a week, best first.

    All three markets get identical analysis; the bar an edge must clear differs by how
    accurate that market proved on a held-out season.
    """
    _setup_logging(verbose=False)
    from app.services.nfl_prop_scanner import scan_week

    with SessionLocal() as session:
        result = scan_week(session, season=season, week=week)
        typer.echo(result.summary())
        if not result.graded:
            typer.echo("no graded lines -- poll prop lines first with nfl-props --live")
            raise typer.Exit(0)
        typer.echo("")
        header = (
            f"  {'':1s} {'player':22s} {'market':10s} {'side':5s} {'line':>6s} "
            f"{'price':>6s} {'ours':>6s} {'need':>6s} {'edge':>7s} {'bar':>6s}"
        )
        typer.echo(header)
        for g in result.graded[:top]:
            typer.echo(
                f"  {g.grade.value} {g.player_name[:22]:22s} {g.market.value:10s} "
                f"{g.side:5s} {g.line:>6.1f} {g.price_american:>+6d} "
                f"{g.model_prob:>6.1%} {g.break_even:>6.1%} "
                f"{g.edge * 100:>+6.1f}p {g.required_edge * 100:>5.1f}p"
            )


if __name__ == "__main__":
    app()
