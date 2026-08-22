import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { num, pct, signed } from "../lib/format";

/** Closing line value: does the market drift toward the number we would have taken?
 *
 *  This is the only measurement in the project that can show edge without beating the
 *  closing line. Every model here loses to the close — but if the market moves toward
 *  our side after the open, being early is worth something on its own.
 */
export function NflMovement() {
  const { data, isLoading } = useQuery({
    queryKey: ["nfl-movement"],
    queryFn: () => api.nflMovement(2026),
  });

  if (isLoading || !data) return <div className="loading">LOADING MOVEMENT</div>;

  const pending = Math.max(0, 30 - data.resolved);

  return (
    <div className="page">
      <p className="eyebrow">CLOSING LINE VALUE · 2026</p>
      <h1 className="page-title">Movement</h1>
      <p className="page-sub">
        Whether the market drifts toward the side our model took at the open.
      </p>

      <div className="factor glass">
        <h3>Why this and not the model</h3>
        <p className="hint">
          Our projection loses to the closing line — 10.81 MAE against 10.26. But a bet is
          placed at the price on offer when you place it, not at the close. If the line
          moves our way after the open, being early is the edge, and that resolves before
          the game is even played.
        </p>
        <div className="factor-grid">
          <div>
            <b>{data.tracked_games}</b>
            <span>GAMES TRACKED</span>
          </div>
          <div>
            <b>{data.games_with_movement}</b>
            <span>LINES MOVED</span>
          </div>
          <div>
            <b>{data.resolved}</b>
            <span>RESOLVED</span>
          </div>
          <div>
            <b className={data.ready ? "pos" : ""}>
              {data.clv_rate ? pct(data.clv_rate.rate, 1) : "—"}
            </b>
            <span>MOVED OUR WAY</span>
          </div>
          <div>
            <b>{signed(data.mean_drift, 2)}</b>
            <span>MEAN DRIFT</span>
          </div>
        </div>
        <p className="factor-verdict">{data.verdict}</p>
      </div>

      {!data.ready && (
        <div className="banner warn">
          <span className="banner-key">NOT READY</span>
          <span>
            Needs roughly <strong>{pending} more</strong> resolved games before the number
            means anything. Lines are polled daily; this becomes readable a few weeks into
            the season. Until then the percentage above is decoration, not evidence.
          </span>
        </div>
      )}

      {data.movements.length === 0 ? (
        <div className="empty">
          <b>No movement recorded yet</b>
          Openers are seeded and polling is running daily — drift accumulates from here.
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th className="label">MATCHUP</th>
                <th>WK</th>
                <th>OPEN</th>
                <th>NOW</th>
                <th>DRIFT</th>
                <th>MODEL</th>
                <th>OUR SIDE</th>
                <th>OBS</th>
                <th>RESULT</th>
              </tr>
            </thead>
            <tbody>
              {data.movements.map((m) => (
                <tr key={m.game_id}>
                  <td className="label">{m.matchup}</td>
                  <td className="dim">{m.week}</td>
                  <td>{m.open_total != null ? num(m.open_total, 1) : "—"}</td>
                  <td>{m.latest_total != null ? num(m.latest_total, 1) : "—"}</td>
                  <td
                    className={
                      m.total_drift == null || m.total_drift === 0
                        ? "dim"
                        : m.total_drift > 0
                          ? "pos"
                          : "neg-v"
                    }
                  >
                    {m.total_drift != null ? signed(m.total_drift, 1) : "—"}
                  </td>
                  <td className="dim">
                    {m.model_total_at_open != null ? num(m.model_total_at_open, 1) : "—"}
                  </td>
                  <td className="dim">
                    {m.model_disagreement == null
                      ? "—"
                      : m.model_disagreement > 0
                        ? "over"
                        : "under"}
                  </td>
                  <td className="dim">{m.observations}</td>
                  <td>
                    {m.moved_our_way == null ? (
                      <span className="dim">pending</span>
                    ) : m.moved_our_way ? (
                      <span className="pos">toward us</span>
                    ) : (
                      <span className="neg-v">against us</span>
                    )}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="hint" style={{ marginTop: 14, maxWidth: 800 }}>
        A game only resolves once our model disagreed with the opener by at least a point
        and the line then moved at least half a point. Games where we agreed with the
        market, or where nothing moved, are not bets and are excluded rather than counted
        as wins.
      </p>
    </div>
  );
}
