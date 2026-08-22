import { useQuery } from "@tanstack/react-query";
import { api, type NflGame } from "../lib/api";
import { num, signed } from "../lib/format";

function conditions(g: NflGame): string {
  const bits: string[] = [];
  if (g.roof && g.roof !== "outdoors") bits.push(g.roof);
  if (g.wind != null && g.wind >= 12) bits.push(`${num(g.wind, 0)}mph wind`);
  if (g.temp != null && g.temp <= 35) bits.push(`${num(g.temp, 0)}°F`);
  if (g.div_game) bits.push("divisional");
  const rest = (g.home_rest ?? 7) - (g.away_rest ?? 7);
  if (Math.abs(rest) >= 3) bits.push(`${rest > 0 ? "home" : "away"} +${Math.abs(rest)} rest`);
  return bits.join(" · ");
}

export function NflSlate() {
  const { data: status } = useQuery({ queryKey: ["nfl-status"], queryFn: api.nflStatus });
  const season = status?.upcoming_season ?? 2026;
  const { data: games, isLoading } = useQuery({
    queryKey: ["nfl-schedule", season],
    queryFn: () => api.nflSchedule(season, 1, true),
    enabled: !!status,
  });

  if (isLoading || !status) return <div className="loading">LOADING SCHEDULE</div>;

  return (
    <div className="page">
      <p className="eyebrow">SEASON {season} · WEEK 1</p>
      <h1 className="page-title">Schedule</h1>
      <p className="page-sub">
        {status.completed_games.toLocaleString()} completed games loaded ·{" "}
        {status.scheduled_games} scheduled
      </p>

      <div className="banner warn">
        <span className="banner-key">MODEL STATUS</span>
        <span>{status.model_note}</span>
      </div>

      {!games?.length ? (
        <div className="empty">
          <b>No games for this week</b>
          Run the NFL ingest to load the schedule.
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th className="label">MATCHUP</th>
                <th>DATE</th>
                <th>TOTAL</th>
                <th>SPREAD</th>
                <th>MODEL</th>
                <th>DIFF</th>
                <th>QB</th>
                <th className="label">CONDITIONS</th>
              </tr>
            </thead>
            <tbody>
              {games.map((g) => {
                const diff =
                  g.projected_total != null && g.total_line != null
                    ? g.projected_total - g.total_line
                    : null;
                return (
                  <tr key={g.game_id}>
                    <td className="label">
                      {g.away_team} @ {g.home_team}
                    </td>
                    <td className="dim">{g.gameday}</td>
                    <td>{g.total_line != null ? num(g.total_line, 1) : "—"}</td>
                    <td className="dim">
                      {g.spread_line != null ? signed(g.spread_line, 1) : "—"}
                    </td>
                    <td className={g.projection_thin ? "dim" : ""}>
                      {g.projected_total != null ? num(g.projected_total, 1) : "—"}
                      {g.projection_thin && (
                        <div style={{ fontSize: 9.5, color: "var(--muted-dim)" }}>
                          thin sample
                        </div>
                      )}
                    </td>
                    <td className="dim">{diff != null ? signed(diff, 1) : "—"}</td>
                    <td className="dim" style={{ fontSize: 10 }}>
                      {g.away_qb_name?.split(" ").slice(-1)[0] ?? "?"} /{" "}
                      {g.home_qb_name?.split(" ").slice(-1)[0] ?? "?"}
                    </td>
                    <td className="label dim" style={{ fontSize: 10 }}>
                      {conditions(g) || "—"}
                    </td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
