import { useQuery } from "@tanstack/react-query";
import { api, type Sport, type TeamStats } from "../lib/api";
import { SortableTable, type Column } from "../components/SortableTable";
import { num, signed } from "../lib/format";

export function Teams({ sport }: { sport: Sport }) {
  const { data, isLoading } = useQuery({
    queryKey: ["teams", sport],
    queryFn: () => api.teams(sport),
  });
  const { data: status } = useQuery({
    queryKey: ["status", sport],
    queryFn: () => api.sportStatus(sport),
  });

  if (isLoading) return <div className="loading">LOADING TEAMS</div>;
  if (!data?.length) return <div className="page"><div className="empty"><b>No team stats</b>Run the stats ingest.</div></div>;

  const nba: Column<TeamStats>[] = [
    { key: "team", label: "TEAM", value: (r) => r.team.abbrev, render: (r) => r.team.name },
    { key: "gp", label: "GP", value: (r) => r.games_played },
    { key: "pf", label: "PPG", value: (r) => r.points_for, render: (r) => num(r.points_for) },
    { key: "pa", label: "OPP PPG", value: (r) => r.points_against, render: (r) => num(r.points_against) },
    { key: "ortg", label: "ORTG", value: (r) => r.off_rating, render: (r) => num(r.off_rating) },
    { key: "drtg", label: "DRTG", value: (r) => r.def_rating, render: (r) => num(r.def_rating) },
    {
      key: "net",
      label: "NET",
      value: (r) => r.net_rating,
      render: (r) => (
        <span className={(r.net_rating ?? 0) >= 0 ? "pos" : "neg-v"}>{signed(r.net_rating)}</span>
      ),
    },
    { key: "pace", label: "PACE", value: (r) => r.pace, render: (r) => num(r.pace) },
  ];

  const mlb: Column<TeamStats>[] = [
    { key: "team", label: "TEAM", value: (r) => r.team.abbrev, render: (r) => r.team.name },
    { key: "gp", label: "GP", value: (r) => r.games_played },
    {
      key: "rpg",
      label: "RUNS/G",
      value: (r) => (r.runs_for && r.games_played ? r.runs_for / r.games_played : null),
      render: (r) => num(r.runs_for && r.games_played ? r.runs_for / r.games_played : null, 2),
    },
    {
      key: "rapg",
      label: "ALLOWED/G",
      value: (r) => (r.runs_against && r.games_played ? r.runs_against / r.games_played : null),
      render: (r) => num(r.runs_against && r.games_played ? r.runs_against / r.games_played : null, 2),
    },
    { key: "era", label: "TEAM ERA", value: (r) => r.team_era, render: (r) => num(r.team_era, 2) },
    { key: "whip", label: "WHIP", value: (r) => r.team_whip, render: (r) => num(r.team_whip, 2) },
    { key: "ops", label: "OPS", value: (r) => r.team_ops, render: (r) => num(r.team_ops, 3) },
  ];

  return (
    <div className="page">
      <p className="eyebrow">SEASON {status?.season ?? ""}</p>
      <h1 className="page-title">Team ratings</h1>
      <p className="page-sub">
        {sport === "nba"
          ? "Offensive and defensive rating are points per 100 possessions. Click any column to sort."
          : "Click any column to sort."}
      </p>
      <SortableTable
        rows={data}
        columns={sport === "nba" ? nba : mlb}
        initialSort={sport === "nba" ? "net" : "era"}
        initialDesc={sport === "nba"}
        rowKey={(r) => r.team.id}
      />
    </div>
  );
}
