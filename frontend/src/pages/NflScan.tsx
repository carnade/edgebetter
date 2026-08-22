import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type GradedProp } from "../lib/api";
import { american, num, pct } from "../lib/format";

const GRADE_CLASS: Record<string, string> = {
  A: "grade-a",
  B: "grade-b",
  C: "grade-c",
  D: "grade-d",
};

/** Recent games against this line — the sanity check on a probability.
 *  Green cleared the number, red did not, most recent on the right. */
function RecentVsLine({ p }: { p: GradedProp }) {
  if (!p.recent_yards.length) return <span className="dim">—</span>;
  return (
    <div className="recent">
      <span className="recent-games">
        {p.recent_yards.map((y, i) => (
          <i key={i} className={y > p.line ? "over" : "under"} title={`${y.toFixed(0)} yds`}>
            {Math.round(y)}
          </i>
        ))}
      </span>
      <span className="recent-tally dim">
        {p.recent_over}/{p.recent_counted} over
      </span>
    </div>
  );
}

/** How far past the required bar an edge sits, drawn to scale.
 *  The bar differs per market — that is the whole point of the grade. */
function EdgeBar({ p }: { p: GradedProp }) {
  const ratio = Math.max(0, Math.min(p.edge_ratio, 3));
  return (
    <div className="edgebar" title={`${(p.edge * 100).toFixed(1)} pts vs a ${(p.required_edge * 100).toFixed(1)} pt bar`}>
      <span className="edgebar-track">
        <i className="edgebar-fill" style={{ width: `${(ratio / 3) * 100}%` }} />
        <i className="edgebar-bar" style={{ left: `${(1 / 3) * 100}%` }} />
      </span>
      <b>{p.edge_ratio.toFixed(1)}×</b>
    </div>
  );
}

export function NflScan() {
  const [minGrade, setMinGrade] = useState("D");
  const { data, isLoading } = useQuery({
    queryKey: ["nfl-scan", minGrade],
    queryFn: () => api.nflPropScan(1, minGrade),
  });

  if (isLoading) return <div className="loading">SCANNING SLATE</div>;

  const counts = data?.grade_counts ?? {};

  return (
    <div className="page">
      <p className="eyebrow">EVERY POSTED LINE, GRADED</p>
      <h1 className="page-title">Scan</h1>
      <p className="page-sub">
        All three markets, identical analysis. What differs is the bar an edge must clear.
      </p>

      <div className="factor glass" style={{ marginBottom: 14 }}>
        <h3>How the grade works</h3>
        <p className="hint" style={{ marginBottom: 12 }}>
          An edge only counts if it beats our own measured error. Replayed over five
          seasons, receiving yards are accurate to <strong>1.9 points</strong>, rushing to{" "}
          <strong>2.6</strong>, passing to <strong>3.6</strong>. So the same +3 point edge
          is a real signal on receiving and noise on passing — same method, different bar.
          <br />
          <br />
          Yardage is right-skewed, so a player&rsquo;s <strong>50/50 point sits below his
          average</strong> — a receiver can average 30 yards and still go under 28.5 most
          weeks. The pick follows the 50/50 number, which is why it can disagree with the
          average.
          <br />
          <br />
          <strong>LAST 6 vs LINE</strong> is the sanity check: green cleared the number,
          most recent on the right. When the model disagrees with a player&rsquo;s recent
          record, that is a judgement call the data cannot make for you.
        </p>
        <div className="factor-grid">
          {(["A", "B", "C", "D"] as const).map((g) => (
            <div key={g}>
              <b className={GRADE_CLASS[g]}>{counts[g] ?? 0}</b>
              <span>GRADE {g}</span>
            </div>
          ))}
          <div>
            <b>{data?.lines_seen ?? 0}</b>
            <span>LINES SEEN</span>
          </div>
          <div>
            <b>{data?.players_without_history ?? 0}</b>
            <span>NO HISTORY</span>
          </div>
        </div>
      </div>

      {data?.one_sided_warning && (
        <div className="banner warn">
          <span className="banner-key">ONE-SIDED</span>
          <span>{data.one_sided_warning}</span>
        </div>
      )}

      {data?.coverage_warning && (
        <div className="banner warn">
          <span className="banner-key">THIN COVERAGE</span>
          <span>{data.coverage_warning}</span>
        </div>
      )}

      <div className="presets" style={{ marginBottom: 12 }}>
        {[
          { key: "A", label: "A only" },
          { key: "B", label: "A and B" },
          { key: "C", label: "Down to C" },
          { key: "D", label: "Everything" },
        ].map((o) => (
          <button
            key={o.key}
            className={`preset${minGrade === o.key ? " active" : ""}`}
            onClick={() => setMinGrade(o.key)}
          >
            {o.label}
          </button>
        ))}
      </div>

      {!data?.props.length ? (
        <div className="empty">
          <b>No lines at this grade</b>
          Poll prop lines first, or loosen the filter. Lines fill in as books post them
          during game week.
        </div>
      ) : (
        <div className="table-wrap">
          <table>
            <thead>
              <tr>
                <th className="label">PLAYER</th>
                <th>MARKET</th>
                <th>PICK</th>
                <th>BOOK</th>
                <th>PRICE</th>
                <th>OURS</th>
                <th>NEEDS</th>
                <th>EDGE</th>
                <th>VS BAR</th>
                <th className="label">LAST 6 vs LINE</th>
              </tr>
            </thead>
            <tbody>
              {data.props.map((p, i) => (
                <tr key={`${p.player_name}-${p.market}-${p.side}-${i}`}>
                  <td className="label">
                    <span className={`grade ${GRADE_CLASS[p.grade]}`}>{p.grade}</span>{" "}
                    {p.player_name}
                    <div className="dim" style={{ fontSize: 10 }}>
                      {p.team} vs {p.opponent} · {p.games_of_history} games
                    </div>
                    <div className="row-reason">{p.reason}</div>
                    {p.coverage_warning && (
                      <div className="row-warn" title={p.coverage_warning}>
                        {p.books_posting <= 1
                          ? `1 book only (${p.book})`
                          : `${p.books_posting} books · lines differ by ${num(
                              p.line_span ?? 0,
                              1,
                            )}`}
                      </div>
                    )}
                  </td>
                  <td className="dim">
                    {p.market_label.replace(" yards", "")}
                    <div style={{ fontSize: 9.5 }}>{p.calibration}</div>
                  </td>
                  <td>
                    {p.side} {num(p.line, 1)}
                    {/* The median, not the mean, is the 50/50 point that decides an
                        over/under. Showing only the average made picks look inconsistent
                        with their own projection. */}
                    <div className="dim" style={{ fontSize: 10 }}>
                      50/50 at {num(p.projected_median ?? p.projected, 1)}
                      <span style={{ opacity: 0.65 }}> · avg {num(p.projected, 1)}</span>
                    </div>
                  </td>
                  <td className="dim">{p.book}</td>
                  <td>{american(p.price_american)}</td>
                  <td className={p.edge > 0 ? "pos" : "dim"}>{pct(p.model_prob, 1)}</td>
                  <td className="dim">{pct(p.break_even, 1)}</td>
                  <td className={p.edge > 0 ? "pos" : "neg-v"}>
                    {(p.edge * 100 >= 0 ? "+" : "") + (p.edge * 100).toFixed(1)}p
                    <div className="dim" style={{ fontSize: 9.5 }}>
                      bar {(p.required_edge * 100).toFixed(1)}p
                    </div>
                  </td>
                  <td>
                    <EdgeBar p={p} />
                  </td>
                  <td className="label">
                    <RecentVsLine p={p} />
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <p className="hint" style={{ marginTop: 14, maxWidth: 820 }}>
        <strong>VS BAR</strong> is how many times over its required edge a pick sits. Below
        1.0× the edge is inside our own error and grades C no matter how large it looks —
        which is why a modest receiving edge can outrank a bigger passing one.
      </p>
    </div>
  );
}
