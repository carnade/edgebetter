import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { num, pct, signed } from "../lib/format";

/** Two measured effects, each stated with what the market already prices in.
 *
 *  Both are cases where the folklore is right about the effect and wrong about the
 *  opportunity: the scoring change is real, and the line has already moved for it.
 */
export function NflFactors() {
  const { data: qb } = useQuery({ queryKey: ["nfl-qb"], queryFn: api.nflQbImpact });
  const { data: p } = useQuery({ queryKey: ["nfl-partials"], queryFn: api.nflPartials });

  if (!qb || !p) return <div className="loading">LOADING FACTORS</div>;

  return (
    <div className="page">
      <p className="eyebrow">MEASURED EFFECTS · 2020–2025</p>
      <h1 className="page-title">Factors</h1>
      <p className="page-sub">
        Two things widely believed to move games, measured against what the market already
        charges for them.
      </p>

      <div className="factor glass">
        <h3>Backup quarterback starts</h3>
        <p className="hint">
          Widely called the biggest single-factor swing in football. It is — and the market
          knows.
        </p>
        <div className="factor-grid">
          <div>
            <b>{num(qb.backup_points, 1)}</b>
            <span>PTS WITH BACKUP</span>
          </div>
          <div>
            <b>{num(qb.starter_points, 1)}</b>
            <span>PTS WITH STARTER</span>
          </div>
          <div>
            <b className="neg-v">{signed(qb.points_swing, 1)}</b>
            <span>SCORING DROP</span>
          </div>
          <div>
            <b>{signed(qb.line_swing, 1)}</b>
            <span>LINE MOVES</span>
          </div>
          <div>
            <b>{pct(qb.fade_rate, 1)}</b>
            <span>FADING THEM WENT</span>
          </div>
          <div>
            <b>{qb.backup_games}</b>
            <span>BACKUP STARTS</span>
          </div>
        </div>
        <p className="factor-verdict">{qb.verdict}</p>
      </div>

      <div className="factor glass">
        <h3>First-half scoring</h3>
        <p className="hint">
          Scripted opening drives are said to make early scoring more predictable. This one
          holds up.
        </p>
        <div className="factor-grid">
          <div>
            <b>{num(p.first_quarter_mean, 1)}</b>
            <span>1Q POINTS</span>
          </div>
          <div>
            <b>{num(p.first_half_mean, 1)}</b>
            <span>1H POINTS</span>
          </div>
          <div>
            <b>{num(p.second_half_mean, 1)}</b>
            <span>2H POINTS</span>
          </div>
          <div>
            <b>{pct(p.first_half_share, 1)}</b>
            <span>1H SHARE</span>
          </div>
          <div>
            <b className={p.first_half_more_stable ? "pos" : ""}>{num(p.first_half_cv, 3)}</b>
            <span>1H VARIABILITY</span>
          </div>
          <div>
            <b>{num(p.second_half_cv, 3)}</b>
            <span>2H VARIABILITY</span>
          </div>
        </div>
        <p className="factor-verdict">
          {p.verdict} Teams are held scoreless in the first quarter{" "}
          {pct(p.scoreless_first_quarter.rate, 1)} of the time.
        </p>
      </div>
    </div>
  );
}
