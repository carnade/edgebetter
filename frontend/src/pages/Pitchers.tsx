import { useQuery } from "@tanstack/react-query";
import { api, type Pitcher } from "../lib/api";
import { SortableTable, type Column } from "../components/SortableTable";
import { Sparkline } from "../components/Sparkline";
import { num } from "../lib/format";

export function Pitchers() {
  const { data, isLoading } = useQuery({ queryKey: ["pitchers"], queryFn: api.pitchers });
  if (isLoading) return <div className="loading">LOADING PITCHERS</div>;
  if (!data?.length)
    return (
      <div className="page">
        <div className="empty">
          <b>No pitcher data</b>Run the MLB stats ingest.
        </div>
      </div>
    );

  const columns: Column<Pitcher>[] = [
    { key: "name", label: "PITCHER", value: (r) => r.name },
    { key: "w", label: "W-L", value: (r) => r.wins, render: (r) => `${r.wins ?? 0}-${r.losses ?? 0}` },
    { key: "era", label: "ERA", value: (r) => r.era, render: (r) => num(r.era, 2) },
    { key: "whip", label: "WHIP", value: (r) => r.whip, render: (r) => num(r.whip, 2) },
    { key: "k9", label: "K/9", value: (r) => r.k_per_9, render: (r) => num(r.k_per_9, 2) },
    { key: "bb9", label: "BB/9", value: (r) => r.bb_per_9, render: (r) => num(r.bb_per_9, 2) },
    { key: "ip", label: "IP", value: (r) => r.innings_pitched, render: (r) => num(r.innings_pitched) },
    { key: "gs", label: "GS", value: (r) => r.games_started },
    {
      key: "ipgs",
      label: "IP/START",
      value: (r) => r.innings_per_start,
      render: (r) => num(r.innings_per_start, 2),
    },
    {
      key: "form",
      label: "LAST 8 (ER/9)",
      value: (r) => r.recent_form.length,
      render: (r) => <Sparkline values={r.recent_form} />,
    },
  ];

  return (
    <div className="page">
      <p className="eyebrow">STARTING PITCHERS</p>
      <h1 className="page-title">Pitchers</h1>
      <p className="page-sub">
        Innings per start drives how much of a game the model gives the starter versus the
        bullpen. Recent form bars show earned runs per nine; red is a rough outing.
      </p>
      <SortableTable
        rows={data}
        columns={columns}
        initialSort="era"
        initialDesc={false}
        rowKey={(r) => r.id}
      />
    </div>
  );
}
