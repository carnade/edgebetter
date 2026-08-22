import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type Sport } from "../lib/api";
import { american, evClass, gameTime, marketLabel, num, pct, signedPct } from "../lib/format";

export function Edges({ sport }: { sport: Sport }) {
  const [agreeOnly, setAgreeOnly] = useState(false);
  const { data: edges, isLoading } = useQuery({
    queryKey: ["edges", sport, agreeOnly],
    queryFn: () => api.edges(sport, agreeOnly),
  });
  const { data: odds } = useQuery({ queryKey: ["odds"], queryFn: api.oddsStatus });

  if (isLoading) return <div className="loading">LOADING EDGES</div>;

  return (
    <div className="page">
      <p className="eyebrow">RANKED BY EXPECTED VALUE</p>
      <h1 className="page-title">Edges</h1>
      <p className="page-sub">
        Fair price is the median devigged consensus across books. Stake is quarter Kelly.
      </p>

      <div className="banner">
        <span className="banner-key">HOW TO READ</span>
        <span>
          <strong>EV</strong> comes from the market alone and needs no model to be right.{" "}
          <strong>Model</strong> is an independent projection — treat it as an estimate.
          When both point the same way the row is marked <strong>AGREE</strong>, which is a
          stronger signal than either on its own.
        </span>
      </div>

      <div style={{ marginBottom: 12 }}>
        <button
          className={`ev ${agreeOnly ? "pos" : ""}`}
          onClick={() => setAgreeOnly(!agreeOnly)}
          aria-pressed={agreeOnly}
        >
          {agreeOnly ? "SHOWING AGREEMENTS ONLY" : "SHOW AGREEMENTS ONLY"}
        </button>
      </div>

      {!edges?.length ? (
        <div className="empty">
          <b>No positive-EV plays</b>
          {odds?.enabled
            ? "Either no games are priced right now, or the books agree with each other."
            : "Add an odds API key to price the slate."}
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th className="label">MATCHUP</th>
                <th>MARKET</th>
                <th>PICK</th>
                <th>BOOK</th>
                <th>PRICE</th>
                <th>FAIR</th>
                <th>EV</th>
                <th>STAKE</th>
                <th>MODEL</th>
                <th />
              </tr>
            </thead>
            <tbody>
              {edges.map((e) => (
                <tr key={e.id}>
                  <td className="label">
                    <Link to={`/game/${e.game_id}`}>{e.matchup}</Link>
                    <div className="dim" style={{ fontSize: 10 }}>
                      {e.start_time ? gameTime(e.start_time) : ""}
                    </div>
                  </td>
                  <td className="dim">{marketLabel(e.market, e.point)}</td>
                  <td>{e.selection}</td>
                  <td className="dim">{e.best_book}</td>
                  <td>{american(e.best_price_american)}</td>
                  <td className="dim">{pct(e.fair_prob)}</td>
                  <td>
                    <span className={`ev ${evClass(e.ev)}`}>{signedPct(e.ev)}</span>
                  </td>
                  <td>{pct(e.kelly_quarter, 2)}</td>
                  <td className={e.model_ev != null && e.model_ev > 0 ? "pos" : "dim"}>
                    {e.model_prob != null ? pct(e.model_prob) : "—"}
                    {e.model_line != null && (
                      <div className="dim" style={{ fontSize: 10 }}>
                        line {num(e.model_line, 1)}
                      </div>
                    )}
                  </td>
                  <td>{e.signals_agree ? <span className="agree">AGREE</span> : null}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
    </div>
  );
}
