/** The four checks, collapsed into one verdict but never hidden behind it.
 *
 *  The whole point of this panel is that a "good bet" badge with no visible reasoning
 *  is how people end up betting on autopilot. The grade is the headline; the checks
 *  are always one glance away.
 */
import { Link } from "react-router-dom";
import type { Mismatch } from "../lib/api";
import { american, gameTime, pct, signedPct } from "../lib/format";

export function GradePill({ grade }: { grade: Mismatch["grade"] }) {
  const cls =
    grade === "bet" ? "pos" : grade === "near miss" ? "thin" : grade === "pass" ? "" : "muted";
  return <span className={`ev ${cls}`}>{grade.toUpperCase()}</span>;
}

function CheckRow({ passed, label, detail }: { passed: boolean; label: string; detail: string }) {
  return (
    <div className="check">
      <span className={passed ? "check-mark pass" : "check-mark fail"} aria-hidden="true">
        {passed ? "✓" : "✕"}
      </span>
      <span className="check-label">{label}</span>
      <span className="check-detail">{detail}</span>
    </div>
  );
}

export function BetGate({ rows }: { rows: Mismatch[] }) {
  const bets = rows.filter((m) => m.grade === "bet");
  const near = rows.filter((m) => m.grade === "near miss");
  const priced = rows.filter((m) => m.ev != null);
  const featured = bets.length ? bets : near.slice(0, 2);

  return (
    <div className="gate">
      <div className="gate-head">
        <div>
          <p className="eyebrow" style={{ margin: 0 }}>
            ALL FOUR CHECKS, APPLIED FOR YOU
          </p>
          <h2 className="gate-title">
            {bets.length > 0
              ? `${bets.length} bet${bets.length === 1 ? "" : "s"} clears every check`
              : "Nothing clears every check right now"}
          </h2>
        </div>
        <div className="gate-count">
          <b>{bets.length}</b>
          <span>
            of {priced.length} priced
          </span>
        </div>
      </div>

      {bets.length === 0 && (
        <p className="gate-note">
          That is the normal result, not a broken screen — across the whole 2026 season
          the market priced these matchups efficiently. Below is the closest miss and
          exactly which check it failed.
        </p>
      )}

      {featured.length === 0 ? (
        <p className="gate-note">No games are priced yet. The odds worker polls three times a day.</p>
      ) : (
        <div className="gate-cards">
          {featured.map((m) => (
            <div key={m.game_id} className={`gate-card${m.grade === "bet" ? " is-bet" : ""}`}>
              <div className="gate-card-head">
                <Link to={`/game/${m.game_id}`} className="gate-matchup">
                  {m.matchup}
                </Link>
                <GradePill grade={m.grade} />
              </div>
              <div className="gate-card-sub">
                {m.favourite} to win · {m.best_american != null ? american(m.best_american) : "—"}{" "}
                {m.best_book ? `at ${m.best_book}` : ""} · {gameTime(m.start_time)}
              </div>

              <div className="checks">
                {m.checks.map((c) => (
                  <CheckRow key={c.key} passed={c.passed} label={c.label} detail={c.detail} />
                ))}
              </div>

              {m.grade === "bet" ? (
                <div className="gate-action">
                  Stake <b>{pct(m.kelly_quarter, 2)}</b> of bankroll (quarter Kelly) · EV{" "}
                  <b>{signedPct(m.ev)}</b>
                </div>
              ) : (
                <div className="gate-action muted">
                  Not a bet — {m.blocking_reason}
                </div>
              )}
            </div>
          ))}
        </div>
      )}
    </div>
  );
}
