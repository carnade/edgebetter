import { useQuery } from "@tanstack/react-query";
import { Link, useParams } from "react-router-dom";
import { api } from "../lib/api";
import { NumberLine } from "../components/NumberLine";
import { Sparkline } from "../components/Sparkline";
import { american, evClass, gameTime, marketLabel, num, pct, signedPct } from "../lib/format";

export function GameDetail() {
  const { id } = useParams();
  const { data: game, isLoading } = useQuery({
    queryKey: ["game", id],
    queryFn: () => api.game(Number(id)),
    enabled: !!id,
  });

  if (isLoading) return <div className="loading">LOADING GAME</div>;
  if (!game)
    return (
      <div className="page">
        <div className="empty">
          <b>Game not found</b>It may have been removed from the schedule.
        </div>
      </div>
    );

  const proj = game.projection;
  const isMlb = game.sport === "mlb";
  const unit = isMlb ? "runs" : "pts";

  // Group the latest price per book so the odds table reads one row per book.
  const byBook = new Map<string, typeof game.prices>();
  for (const p of game.prices) {
    if (!byBook.has(p.bookmaker)) byBook.set(p.bookmaker, []);
    byBook.get(p.bookmaker)!.push(p);
  }

  return (
    <div className="page">
      <p className="eyebrow">
        <Link to={`/${game.sport}`}>&larr; SLATE</Link>
      </p>
      <h1 className="page-title">
        {game.away.name} at {game.home.name}
      </h1>
      <p className="page-sub">
        {gameTime(game.start_time)} · {game.status.replace("STATUS_", "")}
        {game.is_final && game.home_score != null
          ? ` · final ${game.away_score}–${game.home_score}`
          : ""}
      </p>

      <div className="detail-grid">
        <div style={{ display: "grid", gap: 14 }}>
          <div className="panel glass">
            <h3>Total</h3>
            <p className="hint">
              Where the book&rsquo;s number sits against the model&rsquo;s projection.
            </p>
            <NumberLine
              market={game.best_total}
              model={proj?.total}
              unit={unit}
              span={isMlb ? 3 : 18}
              noMarketNote="no book has posted a total for this game"
            />
          </div>

          {byBook.size === 0 && (
            <div className="empty">
              {game.is_final ? (
                <>
                  <b>No pre-game prices recorded</b>
                  Only prices captured before first pitch are kept. Once a game starts the
                  books switch to in-play pricing, which is a different market.
                </>
              ) : (
                <>
                  <b>No book prices yet</b>
                  Once odds are polled, every bookmaker&rsquo;s price lands here alongside the
                  devigged consensus, and any positive-EV plays appear below it.
                </>
              )}
            </div>
          )}

          {byBook.size > 0 && (
            <div>
              <p className="eyebrow">PRICES BY BOOK</p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th className="label">BOOK</th>
                      <th>MARKET</th>
                      <th>SELECTION</th>
                      <th>LINE</th>
                      <th>PRICE</th>
                      <th>IMPLIED</th>
                    </tr>
                  </thead>
                  <tbody>
                    {[...byBook.entries()].flatMap(([book, prices]) =>
                      prices.map((p, i) => (
                        <tr key={`${book}-${p.market}-${p.outcome}-${i}`}>
                          <td className="label">{i === 0 ? book : ""}</td>
                          <td className="dim">{marketLabel(p.market, null)}</td>
                          <td>{p.outcome}</td>
                          <td className="dim">{p.point != null ? num(p.point, 1) : "—"}</td>
                          <td>{american(p.american)}</td>
                          <td className="dim">{pct(1 / p.decimal)}</td>
                        </tr>
                      )),
                    )}
                  </tbody>
                </table>
              </div>
            </div>
          )}

          {game.edges.length > 0 && (
            <div>
              <p className="eyebrow">EDGES ON THIS GAME</p>
              <div className="table-wrap">
                <table>
                  <thead>
                    <tr>
                      <th className="label">MARKET</th>
                      <th>PICK</th>
                      <th>BOOK</th>
                      <th>PRICE</th>
                      <th>FAIR</th>
                      <th>BOOKS</th>
                      <th>EV</th>
                      <th>STAKE</th>
                      <th />
                    </tr>
                  </thead>
                  <tbody>
                    {game.edges.map((e) => (
                      <tr key={e.id}>
                        <td className="label">{marketLabel(e.market, e.point)}</td>
                        <td>{e.selection}</td>
                        <td className="dim">{e.best_book}</td>
                        <td>{american(e.best_price_american)}</td>
                        <td className="dim">{pct(e.fair_prob)}</td>
                        <td className="dim">{e.book_count}</td>
                        <td>
                          <span className={`ev ${evClass(e.ev)}`}>{signedPct(e.ev)}</span>
                        </td>
                        <td>{pct(e.kelly_quarter, 2)}</td>
                        <td>{e.signals_agree ? <span className="agree">AGREE</span> : null}</td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              </div>
            </div>
          )}
        </div>

        <div style={{ display: "grid", gap: 14 }}>
          <div className="panel glass">
            <h3>
              Model projection <span className="tag-est">ESTIMATE</span>
            </h3>
            <p className="hint">
              An independent opinion, not a prediction. Unvalidated until it has a track record.
            </p>
            {proj ? (
              <>
                <div className="kv">
                  <span>{game.away.abbrev}</span>
                  <b>{num(proj.away_score, isMlb ? 2 : 1)}</b>
                </div>
                <div className="kv">
                  <span>{game.home.abbrev}</span>
                  <b>{num(proj.home_score, isMlb ? 2 : 1)}</b>
                </div>
                <div className="kv">
                  <span>Total</span>
                  <b>{num(proj.total, isMlb ? 2 : 1)}</b>
                </div>
                <div className="kv">
                  <span>Margin</span>
                  <b>{num(proj.margin, isMlb ? 2 : 1)}</b>
                </div>
                <div className="kv">
                  <span>P(home win)</span>
                  <b>{pct(proj.prob_home_win)}</b>
                </div>
                {proj.possessions != null && (
                  <div className="kv">
                    <span>Possessions</span>
                    <b>{num(proj.possessions)}</b>
                  </div>
                )}
                {proj.blended && (
                  <p className="hint" style={{ marginTop: 10 }}>
                    Small current-season sample: ratings are blended with last season.
                  </p>
                )}
              </>
            ) : (
              <p className="hint">Not enough team data to project this game.</p>
            )}
          </div>

          {isMlb && (game.away_pitcher || game.home_pitcher) && (
            <div className="panel glass">
              <h3>Starting pitchers</h3>
              <p className="hint">Recent form is earned runs per nine, oldest to newest.</p>
              {[
                { label: game.away.abbrev, p: game.away_pitcher },
                { label: game.home.abbrev, p: game.home_pitcher },
              ].map(({ label, p }) => (
                <div key={label} style={{ marginBottom: 14 }}>
                  <div className="kv">
                    <span>{label}</span>
                    <b>{p?.name ?? "not announced"}</b>
                  </div>
                  {p && (
                    <>
                      <div className="kv">
                        <span>ERA / WHIP</span>
                        <b>
                          {num(p.era, 2)} / {num(p.whip, 2)}
                        </b>
                      </div>
                      <div className="kv">
                        <span>K/9 · BB/9</span>
                        <b>
                          {num(p.k_per_9, 2)} · {num(p.bb_per_9, 2)}
                        </b>
                      </div>
                      <div className="kv">
                        <span>IP / start</span>
                        <b>{num(p.innings_per_start, 2)}</b>
                      </div>
                      <div className="kv">
                        <span>Last 8</span>
                        <b>
                          <Sparkline values={p.recent_form} />
                        </b>
                      </div>
                    </>
                  )}
                </div>
              ))}
            </div>
          )}
        </div>
      </div>
    </div>
  );
}
