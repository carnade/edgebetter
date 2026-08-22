"""nflverse data client.

nflverse publishes NFL data as release assets on GitHub: no key, no rate limit, and
history back to 1999. `games.csv` is the centrepiece because every row carries both the
closing line and the result, which is what makes historical prop outcomes directly
computable.

Two design rules, both learned the hard way earlier in this project:

- **Cache first.** nflverse is a volunteer project and assets can lag or move. A failed
  download must never block work that a previously-downloaded file could answer.
- **Fail loud on schema drift.** Silently mis-mapping a column would poison every base
  rate downstream, and the error would look like a surprising finding rather than a bug.
"""

from __future__ import annotations

import csv
import gzip
import io
import logging
import os
from collections.abc import Iterator
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

import httpx

log = logging.getLogger(__name__)

BASE = "https://github.com/nflverse/nflverse-data/releases/download"

# Where downloaded assets live. Kept out of the repo; safe to delete at any time.
CACHE_DIR = Path(os.environ.get("NFLVERSE_CACHE", "/srv/.nflverse_cache"))

# Assets larger than this stream to disk rather than through memory.
DOWNLOAD_TIMEOUT = httpx.Timeout(180.0, connect=15.0)

# Columns we depend on. If any disappear, the ingest must stop rather than guess.
GAMES_REQUIRED = frozenset({
    "game_id", "season", "game_type", "week", "gameday", "away_team", "home_team",
    "away_score", "home_score", "result", "total", "spread_line", "total_line",
    "away_moneyline", "home_moneyline", "roof", "surface", "temp", "wind",
    "away_rest", "home_rest", "div_game", "home_qb_name", "away_qb_name", "referee",
    "stadium", "overtime",
})

PLAYER_WEEK_REQUIRED = frozenset({
    "player_display_name", "player_id", "position", "team", "season", "week",
    "opponent_team", "passing_yards", "rushing_yards", "receiving_yards",
    "targets", "receptions", "carries", "attempts", "completions",
})

PBP_REQUIRED = frozenset({
    "game_id", "posteam", "defteam", "qtr", "epa", "success", "play_type",
    "total_home_score", "total_away_score", "half_seconds_remaining", "pass", "rush",
})


class NflverseError(RuntimeError):
    """Download or schema problem. Callers should surface this, never swallow it."""


@dataclass(frozen=True)
class Asset:
    release: str
    filename: str

    @property
    def url(self) -> str:
        return f"{BASE}/{self.release}/{self.filename}"

    @property
    def cache_path(self) -> Path:
        return CACHE_DIR / self.release / self.filename


GAMES = Asset("schedules", "games.csv")


def pbp_asset(season: int) -> Asset:
    return Asset("pbp", f"play_by_play_{season}.csv.gz")


def injuries_asset(season: int) -> Asset:
    return Asset("injuries", f"injuries_{season}.csv")


def _is_fresh(path: Path, max_age: timedelta) -> bool:
    if not path.exists() or path.stat().st_size == 0:
        return False
    age = datetime.now(UTC) - datetime.fromtimestamp(path.stat().st_mtime, UTC)
    return age < max_age


def download(asset: Asset, *, max_age: timedelta = timedelta(hours=12)) -> Path:
    """Fetch an asset, reusing a fresh cached copy when one exists.

    On failure with a stale cached copy present, the stale copy is used and a warning
    logged: out-of-date data beats no data, and the alternative is a broken pipeline
    every time GitHub hiccups.
    """
    path = asset.cache_path
    if _is_fresh(path, max_age):
        log.debug("nflverse cache hit: %s", asset.filename)
        return path

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(path.suffix + ".part")

    try:
        with httpx.stream(
            "GET", asset.url, timeout=DOWNLOAD_TIMEOUT, follow_redirects=True
        ) as response:
            response.raise_for_status()
            with tmp.open("wb") as fh:
                for chunk in response.iter_bytes(chunk_size=1 << 16):
                    fh.write(chunk)
        tmp.replace(path)
        log.info("nflverse downloaded %s (%.1f MB)", asset.filename, path.stat().st_size / 1e6)
        return path
    except Exception as exc:  # noqa: BLE001 - any failure falls back to cache
        tmp.unlink(missing_ok=True)
        if path.exists() and path.stat().st_size > 0:
            log.warning(
                "nflverse download of %s failed (%s); using cached copy from %s",
                asset.filename,
                exc,
                datetime.fromtimestamp(path.stat().st_mtime, UTC).date(),
            )
            return path
        raise NflverseError(f"could not fetch {asset.url}: {exc}") from exc


def _open_text(path: Path) -> io.TextIOBase:
    if path.suffix == ".gz":
        return gzip.open(path, "rt", newline="", encoding="utf-8", errors="replace")
    return path.open("rt", newline="", encoding="utf-8", errors="replace")


def _check_schema(fieldnames: list[str] | None, required: frozenset[str], what: str) -> None:
    present = set(fieldnames or ())
    missing = required - present
    if missing:
        raise NflverseError(
            f"{what} is missing expected columns: {sorted(missing)}. "
            f"nflverse may have changed its schema -- fix the mapping rather than "
            f"letting the ingest guess, which would corrupt every base rate downstream."
        )


def read_games(*, max_age: timedelta = timedelta(hours=12)) -> list[dict[str, Any]]:
    """Every game 1999-present, including the upcoming season's schedule."""
    path = download(GAMES, max_age=max_age)
    with _open_text(path) as fh:
        reader = csv.DictReader(fh)
        _check_schema(reader.fieldnames, GAMES_REQUIRED, "games.csv")
        return list(reader)


def iter_pbp(season: int, *, max_age: timedelta = timedelta(days=7)) -> Iterator[dict[str, Any]]:
    """Stream one season of play-by-play.

    A season file is ~18 MB gzipped and several hundred MB expanded, so this yields rows
    rather than materialising them.
    """
    path = download(pbp_asset(season), max_age=max_age)
    with _open_text(path) as fh:
        reader = csv.DictReader(fh)
        _check_schema(reader.fieldnames, PBP_REQUIRED, f"play_by_play_{season}.csv.gz")
        yield from reader


def read_player_weeks(season: int, *, max_age: timedelta = timedelta(hours=12)) -> list[dict[str, Any]]:
    """Weekly per-player box scores: volume, yardage, and usage share."""
    asset = Asset("stats_player", f"stats_player_week_{season}.csv")
    path = download(asset, max_age=max_age)
    with _open_text(path) as fh:
        reader = csv.DictReader(fh)
        _check_schema(reader.fieldnames, PLAYER_WEEK_REQUIRED, asset.filename)
        return list(reader)


def read_snap_counts(season: int, *, max_age: timedelta = timedelta(hours=12)) -> list[dict[str, Any]]:
    """Snap participation. Usage is far more stable than yardage, so this carries
    more predictive weight than any efficiency number."""
    path = download(Asset("snap_counts", f"snap_counts_{season}.csv"), max_age=max_age)
    with _open_text(path) as fh:
        return list(csv.DictReader(fh))


def read_injuries(season: int, *, max_age: timedelta = timedelta(hours=12)) -> list[dict[str, Any]]:
    path = download(injuries_asset(season), max_age=max_age)
    with _open_text(path) as fh:
        return list(csv.DictReader(fh))


# ------------------------------------------------------------------ value parsing
def to_float(value: Any) -> float | None:
    """nflverse writes missing values as empty string or the R sentinel 'NA'."""
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.upper() == "NA":
        return None
    try:
        return float(text)
    except ValueError:
        return None


def to_int(value: Any) -> int | None:
    f = to_float(value)
    return int(f) if f is not None else None


def to_bool(value: Any) -> bool:
    """`div_game` and friends arrive as 0/1, and `overtime` sometimes as TRUE/FALSE."""
    text = str(value or "").strip().upper()
    return text in {"1", "TRUE", "T", "YES"}


def to_str(value: Any) -> str | None:
    if value is None:
        return None
    text = str(value).strip()
    return text if text and text.upper() != "NA" else None
