import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type MarketRow } from "../lib/api";
import { american, evClass, gameTime, num, pct, signedPct } from "../lib/format";

const ORDER = [
  "h2h_1st_5_innings",
  "totals_1st_5_innings",
  "pitcher_strikeouts",
  "team_totals",
];

function BudgetStrip() {
  const { data: b } = useQuery({ queryKey: ["budget"], queryFn: api.budget });
  if (!b) return null;
  return (
    <div className="budget">
      <div className="budget-nums">
        <div>
          <b>{b.remaining}</b>
          <span>CREDITS LEFT</span>
        </div>
        <div>
          <b>{b.daily_allowance}</b>
          <span>PER DAY</span>
        </div>
        <div>
          <b>{b.props_games_today}</b>
          <span>GAMES PRICED TODAY</span>
        </div>
        <div>
          <b>{b.props_markets_per_game}</b>
          <span>CREDITS PER GAME</span>
        </div>
      </div>
      <p className="budget-reason">{b.reason}</p>
    </div>
  );
}

function MarketTable({ rows }: { rows: MarketRow[] }) {
  return (
    <div className="table-wrap">
      <table>
        <thead>
          <tr>
            <th className="label">GAME</th>
            <th>SELECTION</th>
            <th>LINE</th>
            <th>BOOK</th>
            <th>PRICE</th>
            <th>NEEDS</th>
            <th>FAIR</th>
            <th>BOOKS</th>
            <th>EV</th>
            <th>MODEL</th>
          </tr>
        </thead>
        <tbody>
          {rows.map((r, i) => (
            <tr key={`${r.game_id}-${r.selection}-${r.subject ?? ""}-${r.point ?? ""}-${i}`}>
              <td className="label">
                <Link to={`/game/${r.game_id}`}>{r.matchup}</Link>
                <div className="dim" style={{ fontSize: 10 }}>{gameTime(r.start_time)}</div>
              </td>
              <td>
                {r.subject ? (
                  <>
                    {r.subject}
                    <div className="dim" style={{ fontSize: 10 }}>{r.selection}</div>
                  </>
                ) : (
                  r.selection
                )}
              </td>
              <td className="dim">{r.point != null ? num(r.point, 1) : "—"}</td>
              <td className="dim">
                {r.best_book}
                {r.outliers.length > 0 && (
                  <div
                    className="dim"
                    style={{ fontSize: 9.5, color: "var(--amber)" }}
                    title={`Quarantined as inconsistent with the consensus: ${r.outliers.join(", ")}`}
                  >
                    {r.outliers.length} book{r.outliers.length === 1 ? "" : "s"} ignored
                  </div>
                )}
              </td>
              <td>{american(r.best_american)}</td>
              <td className="dim">{pct(r.break_even_prob, 1)}</td>
              <td className="dim">{pct(r.fair_prob, 1)}</td>
              <td className="dim">{r.book_count}</td>
              <td>
                <span className={`ev ${evClass(r.ev)}`}>{signedPct(r.ev)}</span>
              </td>
              <td className="dim" title={r.model_unvalidated ? "Model failed its backtest — context only" : ""}>
                {r.model_value != null ? num(r.model_value, 2) : "—"}
                {r.model_unvalidated && r.model_value != null && (
                  <div style={{ fontSize: 9, color: "var(--muted-dim)" }}>unvalidated</div>
                )}
              </td>
            </tr>
          ))}
        </tbody>
      </table>
    </div>
  );
}

export function Markets() {
  const { data, isLoading } = useQuery({ queryKey: ["markets"], queryFn: api.markets });
  if (isLoading) return <div className="loading">LOADING MARKETS</div>;

  const groups = data ?? {};
  const all = Object.values(groups).flat();
  const positive = all.filter((r) => r.ev > 0.005);
  const quarantined = all.filter((r) => r.outliers.length > 0).length;

  return (
    <div className="page">
      <p className="eyebrow">FIRST 5 INNINGS · STRIKEOUTS · TEAM TOTALS</p>
      <h1 className="page-title">Markets</h1>
      <p className="page-sub">
        Priced by devigging every book and shopping for the best number — no model required.
      </p>

      <BudgetStrip />

      <div className="banner warn">
        <span className="banner-key">WHAT THIS IS NOT</span>
        <span>
          The first-5-innings and strikeout <strong>models both failed</strong> their
          walk-forward backtests (−1.1% and +1.0% against a league-average baseline), so
          nothing here is driven by a projection. The MODEL column is context only. What is
          real is the <strong>line shopping</strong>: {all.length} outcomes across{" "}
          {Object.keys(groups).length} markets, of which{" "}
          <strong>{positive.length}</strong> beat the consensus.
          {quarantined > 0 && (
            <>
              {" "}
              {quarantined} row{quarantined === 1 ? "" : "s"} had a book quarantined for
              contradicting the others.
            </>
          )}
        </span>
      </div>

      {all.length === 0 ? (
        <div className="empty">
          <b>No per-event prices yet</b>
          These markets cost credits per game, so only the top-ranked games get priced. The
          strip above shows today&rsquo;s allowance.
        </div>
      ) : (
        ORDER.filter((m) => (groups[m] ?? []).length > 0).map((m) => (
          <div key={m} style={{ marginBottom: 22 }}>
            <p className="eyebrow">{groups[m][0].market_label.toUpperCase()}</p>
            <MarketTable rows={groups[m]} />
          </div>
        ))
      )}

      <p className="hint" style={{ marginTop: 14, maxWidth: 780 }}>
        <strong>NEEDS</strong> is the win rate the price must beat to break even;{" "}
        <strong>FAIR</strong> is the devigged consensus across books. When FAIR is below
        NEEDS, the bet loses money on average — which is the case for almost everything here.
      </p>
    </div>
  );
}
