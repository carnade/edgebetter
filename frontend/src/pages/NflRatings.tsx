import { useQuery } from "@tanstack/react-query";
import { api, type NflTeamRating } from "../lib/api";
import { SortableTable, type Column } from "../components/SortableTable";
import { num, signed } from "../lib/format";

export function NflRatings() {
  const { data: status } = useQuery({ queryKey: ["nfl-status"], queryFn: api.nflStatus });
  const season = status?.holdout_season ?? 2025;
  const { data, isLoading } = useQuery({
    queryKey: ["nfl-ratings", season],
    queryFn: () => api.nflRatings(season),
    enabled: !!status,
  });

  if (isLoading) return <div className="loading">LOADING RATINGS</div>;
  if (!data?.length)
    return (
      <div className="page">
        <div className="empty">
          <b>No ratings</b>Run the NFL ingest with --pbp.
        </div>
      </div>
    );

  const columns: Column<NflTeamRating>[] = [
    { key: "team", label: "TEAM", value: (r) => r.team },
    { key: "games", label: "GAMES", value: (r) => r.games },
    { key: "pf", label: "PTS/G", value: (r) => r.points_for, render: (r) => num(r.points_for, 1) },
    {
      key: "pa",
      label: "ALLOWED/G",
      value: (r) => r.points_against,
      render: (r) => num(r.points_against, 1),
    },
    {
      key: "oepa",
      label: "OFF EPA/PLAY",
      value: (r) => r.off_epa_per_play,
      render: (r) => (
        <span className={(r.off_epa_per_play ?? 0) >= 0 ? "pos" : "neg-v"}>
          {signed(r.off_epa_per_play, 3)}
        </span>
      ),
    },
    {
      key: "depa",
      label: "DEF EPA/PLAY",
      value: (r) => r.def_epa_per_play,
      render: (r) => (
        <span className={(r.def_epa_per_play ?? 0) <= 0 ? "pos" : "neg-v"}>
          {signed(r.def_epa_per_play, 3)}
        </span>
      ),
    },
    {
      key: "net",
      label: "NET EPA",
      value: (r) => r.net_epa,
      render: (r) => (
        <span className={(r.net_epa ?? 0) >= 0 ? "pos" : "neg-v"}>{signed(r.net_epa, 3)}</span>
      ),
    },
    {
      key: "plays",
      label: "PLAYS/G",
      value: (r) => r.plays_per_game,
      render: (r) => num(r.plays_per_game, 1),
    },
  ];

  return (
    <div className="page">
      <p className="eyebrow">RECENCY-WEIGHTED · THROUGH {season}</p>
      <h1 className="page-title">Team ratings</h1>
      <p className="page-sub">
        EPA per play measures how much each snap moved scoring expectation. Ratings decay with
        a 10-game half-life, so recent form dominates — a flat average across seasons was
        biasing projections by nearly two points.
      </p>
      <SortableTable
        rows={data}
        columns={columns}
        initialSort="net"
        initialDesc
        rowKey={(r) => r.team}
      />
    </div>
  );
}
