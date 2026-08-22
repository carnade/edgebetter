import { useQuery } from "@tanstack/react-query";
import { api, type Sport } from "../lib/api";
import { GameCard } from "../components/GameCard";

export function Slate({ sport }: { sport: Sport }) {
  const { data: games, isLoading } = useQuery({
    queryKey: ["slate", sport],
    queryFn: () => api.slate(sport),
  });
  const { data: status } = useQuery({
    queryKey: ["status", sport],
    queryFn: () => api.sportStatus(sport),
  });
  const { data: odds } = useQuery({ queryKey: ["odds"], queryFn: api.oddsStatus });

  if (isLoading) return <div className="loading">LOADING SLATE</div>;

  const label = sport === "mlb" ? "Baseball" : "Basketball";

  return (
    <div className="page">
      <p className="eyebrow">TODAY&rsquo;S SLATE</p>
      <h1 className="page-title">{label}</h1>
      <p className="page-sub">
        {status ? `${status.season_display} season · ${status.teams_with_stats} teams with stats` : ""}
      </p>

      {odds && !odds.enabled && (
        <div className="banner warn">
          <span className="banner-key">NO ODDS KEY</span>
          <span>
            Add <strong>THE_ODDS_API_KEY</strong> to <strong>.env</strong> to price these games.
            Stats and projections work without it; edges need book prices to compare against.
          </span>
        </div>
      )}

      {status && !status.season_started && (
        <div className="banner">
          <span className="banner-key">OFF SEASON</span>
          <span>
            The {status.season_display} season hasn&rsquo;t started. Team ratings below come from{" "}
            <strong>{status.season}</strong>, and carry over as the early-season prior once games
            begin.
          </span>
        </div>
      )}

      {!games?.length ? (
        <div className="empty">
          <b>No games scheduled</b>
          {status && !status.season_started
            ? "Check back when the season starts. Team and rating views are populated now."
            : "Run the ingest job to pull the schedule."}
        </div>
      ) : (
        <div className="slate">
          {games.map((g) => (
            <GameCard key={g.id} game={g} />
          ))}
        </div>
      )}
    </div>
  );
}
