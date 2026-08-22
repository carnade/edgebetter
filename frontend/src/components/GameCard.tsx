/** One game on the slate: who is playing, who is pitching, and where the model
 *  disagrees with the market. */
import { Link } from "react-router-dom";
import type { Game } from "../lib/api";
import { evClass, gameTime, num, pct, signedPct } from "../lib/format";
import { NumberLine } from "./NumberLine";
import { Sparkline } from "./Sparkline";

export function GameCard({ game }: { game: Game }) {
  const proj = game.projection;
  const isMlb = game.sport === "mlb";
  const unit = isMlb ? "runs" : "pts";

  return (
    <Link to={`/game/${game.id}`} className="game glass">
      <div>
        <div className="game-head">
          <span className="game-time">{gameTime(game.start_time)}</span>
          {game.is_final ? (
            <span className="game-status">FINAL</span>
          ) : (
            <span className="game-status">{game.status.replace("STATUS_", "")}</span>
          )}
          {proj?.blended && <span className="tag-est">PRIOR-SEASON BLEND</span>}
        </div>

        <div className="matchup">
          <span className="team">{game.away.abbrev}</span>
          {game.away_score != null && <span className="score">{game.away_score}</span>}
          <span className="at">at</span>
          <span className="team">{game.home.abbrev}</span>
          {game.home_score != null && <span className="score">{game.home_score}</span>}
        </div>

        {isMlb && (game.away_pitcher || game.home_pitcher) && (
          <div className="pitchers">
            {[
              { side: "AWY", p: game.away_pitcher },
              { side: "HOM", p: game.home_pitcher },
            ].map(({ side, p }) => (
              <div className="pitcher-row" key={side}>
                <span className="side">{side}</span>
                <span className="name">{p?.name ?? "not announced"}</span>
                {p?.era != null && (
                  <span className="era">
                    <b>{num(p.era, 2)}</b> ERA
                  </span>
                )}
                {p?.recent_form?.length ? <Sparkline values={p.recent_form} /> : null}
              </div>
            ))}
          </div>
        )}

        {!isMlb && proj && (
          <div className="pitchers">
            <div className="pitcher-row">
              <span className="side">PACE</span>
              <span className="name">{num(proj.possessions, 1)} possessions</span>
              <span className="era">
                P(home) <b>{pct(proj.prob_home_win, 0)}</b>
              </span>
            </div>
          </div>
        )}
      </div>

      <div>
        <NumberLine
          market={game.best_total}
          model={proj?.total}
          unit={unit}
          span={isMlb ? 3 : 18}
          noMarketNote="no book price yet"
        />
        <div className="nl-foot">
          <span>
            {game.edge_count > 0
              ? `${game.edge_count} edge${game.edge_count === 1 ? "" : "s"}`
              : "no priced edge"}
          </span>
          {game.top_edge_ev != null ? (
            <span className={`ev ${evClass(game.top_edge_ev)}`}>
              {signedPct(game.top_edge_ev)} EV
            </span>
          ) : (
            <span className="tag-est">MODEL ESTIMATE</span>
          )}
        </div>
      </div>
    </Link>
  );
}
