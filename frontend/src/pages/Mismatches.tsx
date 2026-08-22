import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router-dom";
import { api, type Mismatch, type RotationSlot } from "../lib/api";
import { american, gameTime, num, pct, signedPct } from "../lib/format";
import { BetGate, GradePill } from "../components/BetGate";

/** Bar showing how one-sided a matchup is. Not a confidence meter — a size meter. */
function ScoreBar({ score }: { score: number }) {
  return (
    <div className="score">
      <b>{score.toFixed(0)}</b>
      <span>
        <i style={{ width: `${Math.min(100, score)}%` }} />
      </span>
    </div>
  );
}

function PitcherCell({ slot, side }: { slot?: RotationSlot | null; side: "fav" | "dog" }) {
  if (!slot) return <span className="dim">not announced</span>;
  const flagged = side === "fav" ? slot.is_top_two : slot.is_bottom_two;
  return (
    <>
      <span className={flagged ? "pos" : ""}>{slot.name}</span>
      <div className="dim" style={{ fontSize: 10 }}>
        #{slot.rank}/{slot.rotation_size} · {num(slot.era, 2)} ERA
        {flagged && (side === "fav" ? " · top 2" : " · bottom 2")}
      </div>
    </>
  );
}

function verdictClass(verdict: string): string {
  if (verdict === "value") return "pos";
  if (verdict === "overpriced") return "neg-v";
  return "dim";
}

export function Mismatches() {
  const [strictOnly, setStrictOnly] = useState(false);
  const { data: rows, isLoading } = useQuery({
    queryKey: ["mismatches", strictOnly],
    queryFn: () => api.mismatches(strictOnly),
  });
  const { data: evidence } = useQuery({
    queryKey: ["mismatch-evidence"],
    queryFn: api.mismatchEvidence,
  });

  if (isLoading) return <div className="loading">LOADING MISMATCHES</div>;

  const priced = (rows ?? []).filter((m) => m.ev != null);
  const withValue = priced.filter((m) => (m.ev ?? 0) > 0);

  return (
    <div className="page">
      <p className="eyebrow">LOPSIDED PITCHING MATCHUPS</p>
      <h1 className="page-title">Mismatches</h1>
      <p className="page-sub">
        A strong club&rsquo;s best arm against a weak club&rsquo;s worst. Ranked by how
        one-sided the matchup is — the starting pitcher counts for 65% of the score.
      </p>

      <BetGate rows={rows ?? []} />

      <div className="banner warn">
        <span className="banner-key">WHY SO FEW</span>
        <span>
          A high score means the favourite is <strong>likely to win</strong> — not that the
          bet is worth making. These are the games the market prices most carefully, so a
          lopsided matchup and a good bet are rarely the same thing.
          {priced.length > 0 && (
            <>
              {" "}
              Right now <strong>{withValue.length} of {priced.length}</strong> priced
              mismatches beat the market.
            </>
          )}
        </span>
      </div>

      {evidence && (
        <div className="evidence">
          <p className="eyebrow">
            HOW OFTEN THE FAVOURITE ACTUALLY WON · 2026 · WALK-FORWARD
          </p>
          <div className="evidence-bands">
            {evidence.bands.map((b) => (
              <div className="band" key={b.label}>
                <span className="band-label">SCORE {b.label}</span>
                <b>{pct(b.win_rate, 1)}</b>
                <span className="band-n">{b.games} games</span>
                {b.break_even_american != null && (
                  <span className="band-be">
                    breaks even at {american(b.break_even_american)}
                  </span>
                )}
              </div>
            ))}
            {evidence.strict_games > 0 && (
              <div className="band strict">
                <span className="band-label">STRICT</span>
                <b>{pct(evidence.strict_win_rate ?? 0, 1)}</b>
                <span className="band-n">
                  {evidence.strict_wins}/{evidence.strict_games} games
                </span>
                {evidence.strict_break_even_american != null && (
                  <span className="band-be">
                    breaks even at {american(evidence.strict_break_even_american)}
                  </span>
                )}
              </div>
            )}
          </div>
          <p className="hint" style={{ marginTop: 10 }}>
            Compare each band&rsquo;s break-even price with what books actually offer. Books
            price the strongest mismatches near &minus;190; that band won{" "}
            {pct(evidence.bands[evidence.bands.length - 1]?.win_rate ?? 0, 1)} and needs{" "}
            {american(evidence.bands[evidence.bands.length - 1]?.break_even_american ?? 0)} to
            break even, on only{" "}
            {evidence.bands[evidence.bands.length - 1]?.games ?? 0} games — too small a
            sample to call an edge. Home teams won{" "}
            {pct(evidence.baseline_home_win_rate, 1)} overall.
          </p>
          <p className="hint" style={{ marginTop: 6 }}>{evidence.caveat}</p>
        </div>
      )}

      <div style={{ margin: "16px 0 12px" }}>
        <button
          className={`ev ${strictOnly ? "pos" : ""}`}
          onClick={() => setStrictOnly(!strictOnly)}
          aria-pressed={strictOnly}
        >
          {strictOnly ? "SHOWING STRICT ONLY" : "SHOW STRICT ONLY"}
        </button>
        <span className="dim" style={{ marginLeft: 10, fontSize: 11 }}>
          strict = top-10 team with a top-2 starter vs bottom-10 team with a bottom-2 starter
        </span>
      </div>

      {!rows?.length ? (
        <div className="empty">
          <b>No mismatches in the next 48 hours</b>
          {strictOnly
            ? "Nothing meets the strict definition right now. Turn the filter off to see the closest matchups."
            : "Ingest the upcoming schedule to populate this view."}
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th className="label">MATCHUP</th>
                <th>SCORE</th>
                <th>FAVOURITE</th>
                <th>THEIR STARTER</th>
                <th>UNDERDOG</th>
                <th>THEIR STARTER</th>
                <th>MODEL</th>
                <th>PRICE</th>
                <th>RISK</th>
                <th>FAIR</th>
                <th>EV</th>
                <th>VERDICT</th>
                <th>GATE</th>
              </tr>
            </thead>
            <tbody>
              {rows.map((m: Mismatch) => (
                <tr key={m.game_id}>
                  <td className="label">
                    <Link to={`/game/${m.game_id}`}>{m.matchup}</Link>
                    <div className="dim" style={{ fontSize: 10 }}>
                      {gameTime(m.start_time)}
                      {m.strict && <span className="agree" style={{ marginLeft: 6 }}>STRICT</span>}
                    </div>
                  </td>
                  <td>
                    <ScoreBar score={m.score} />
                  </td>
                  <td>
                    {m.favourite}
                    <div className="dim" style={{ fontSize: 10 }}>
                      #{m.favourite_team_rank} · {m.favourite_pythagorean.toFixed(3)}
                    </div>
                  </td>
                  <td>
                    <PitcherCell slot={m.favourite_pitcher} side="fav" />
                  </td>
                  <td>
                    {m.underdog}
                    <div className="dim" style={{ fontSize: 10 }}>
                      #{m.underdog_team_rank} · {m.underdog_pythagorean.toFixed(3)}
                    </div>
                  </td>
                  <td>
                    <PitcherCell slot={m.underdog_pitcher} side="dog" />
                  </td>
                  <td>
                    {pct(m.model_win_prob, 1)}
                    {m.model_disagrees && (
                      <div className="dim" style={{ fontSize: 10, color: "var(--amber)" }}>
                        above market
                      </div>
                    )}
                  </td>
                  <td>{m.best_american != null ? american(m.best_american) : "—"}</td>
                  <td className="dim">
                    {m.risk_to_win_one != null ? `${num(m.risk_to_win_one, 2)}:1` : "—"}
                  </td>
                  <td className="dim">{pct(m.market_fair_prob, 1)}</td>
                  <td className={m.ev != null && m.ev > 0 ? "pos" : "dim"}>
                    {m.ev != null ? signedPct(m.ev) : "—"}
                  </td>
                  <td className={verdictClass(m.verdict)}>{m.verdict}</td>
                  <td title={m.blocking_reason ?? "clears every check"}>
                    <GradePill grade={m.grade} />
                    <div className="dim" style={{ fontSize: 9.5, marginTop: 2 }}>
                      {m.passed_checks}/4 checks
                    </div>
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="hint" style={{ marginTop: 14, maxWidth: 760 }}>
        <strong>RISK</strong> is units staked to win one. At −193 you risk 1.93 to win 1, so
        a favourite winning 70% of the time still loses money if the true rate is below
        65.9%. That gap is why a lopsided matchup and a good bet are not the same thing.
      </p>
    </div>
  );
}
