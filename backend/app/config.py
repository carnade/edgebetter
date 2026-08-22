"""Application settings, loaded from environment (see .env.example)."""

from functools import lru_cache

from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env", extra="ignore")

    database_url: str = "postgresql+psycopg://edgebetter:edgebetter@db:5432/edgebetter"
    log_level: str = "INFO"

    # --- The Odds API ---
    # Blank key is a supported state: stats keep working, edges report "no odds".
    the_odds_api_key: str = ""
    odds_regions: str = "us"
    odds_markets_mlb: str = "h2h,totals"
    odds_markets_nba: str = "h2h,totals,spreads"
    odds_polls_per_day: int = 3
    odds_credit_reserve: int = 50
    odds_lookahead_hours: int = 24
    odds_format: str = "american"

    # --- Per-event markets (first 5 innings, team totals, player props) ---
    # These are only available on /events/{id}/odds, which bills markets x regions PER
    # GAME. One market across a 15-game slate is 15 credits, so the allocator decides
    # how many games we can afford and the model decides which ones.
    props_markets: str = "h2h_1st_5_innings,totals_1st_5_innings,pitcher_strikeouts,team_totals"
    # Safety ceiling independent of the budget maths; 0 disables the cap.
    props_max_games_per_day: int = 4
    # Log intended spend without calling the API. Use before any new sweep.
    props_dry_run: bool = False

    # --- stats.nba.com enrichment (optional, off by default) ---
    enable_nba_stats_enrich: bool = False
    nba_stats_max_age_days: int = 7

    @property
    def props_markets_list(self) -> list[str]:
        return [m.strip() for m in self.props_markets.split(",") if m.strip()]

    @property
    def odds_enabled(self) -> bool:
        return bool(self.the_odds_api_key.strip())

    def markets_for(self, sport_key: str) -> list[str]:
        raw = self.odds_markets_nba if sport_key == "basketball_nba" else self.odds_markets_mlb
        return [m.strip() for m in raw.split(",") if m.strip()]

    def credit_cost(self, sport_key: str) -> int:
        """The Odds API bills markets x regions per /odds call."""
        regions = len([r for r in self.odds_regions.split(",") if r.strip()])
        return len(self.markets_for(sport_key)) * max(regions, 1)


@lru_cache
def get_settings() -> Settings:
    return Settings()
