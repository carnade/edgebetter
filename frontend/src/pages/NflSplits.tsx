/** Conditional base rates: "under these conditions, what has historically happened?"
 *
 *  Every rate is rendered with its interval, sample band, and holdout verdict. A bare
 *  percentage is the one thing this page must never show — with only ~1,600 games in the
 *  pool, a filtered sample of twelve will happily produce a 75% "edge", and the interval
 *  is what stops that from looking like a finding.
 */
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api, type NflMarketSplit, type NflRate, type SplitQuery } from "../lib/api";
import { num, pct } from "../lib/format";

const BANDS: Record<string, { label: string; cls: string }> = {
  noise: { label: "NOISE", cls: "band-noise" },
  suggestive: { label: "THIN", cls: "band-thin" },
  moderate: { label: "MODERATE", cls: "band-mod" },
  meaningful: { label: "SOLID", cls: "band-solid" },
};

/** The interval drawn to scale, with break-even marked. Seeing the width is the point. */
function IntervalBar({ r, breakEven }: { r: NflRate; breakEven: number }) {
  const lo = r.lower * 100;
  const hi = r.upper * 100;
  const mid = r.rate * 100;
  const be = breakEven * 100;
  return (
    <div className="ci" title={`${lo.toFixed(1)}% – ${hi.toFixed(1)}%`}>
      <span className="ci-track">
        <i className="ci-span" style={{ left: `${lo}%`, width: `${Math.max(hi - lo, 0.5)}%` }} />
        <i className="ci-point" style={{ left: `${mid}%` }} />
        <i className="ci-be" style={{ left: `${be}%` }} />
      </span>
    </div>
  );
}

function SplitRow({
  split,
  baseline,
  breakEven,
}: {
  split: NflMarketSplit;
  baseline?: NflMarketSplit;
  breakEven: number;
}) {
  const r = split.result;
  const band = BANDS[r.band] ?? BANDS.noise;
  const diff = baseline ? r.rate - baseline.result.rate : null;

  return (
    <tr>
      <td className="label">{split.label}</td>
      <td>
        <b style={{ color: r.beats_break_even ? "var(--green-bright)" : "var(--text)" }}>
          {pct(r.rate, 1)}
        </b>
        <div className="dim" style={{ fontSize: 10 }}>
          {r.hits}/{r.n}
        </div>
      </td>
      <td style={{ width: 150 }}>
        <IntervalBar r={r} breakEven={breakEven} />
        <div className="dim" style={{ fontSize: 9.5 }}>
          {pct(r.lower, 1)} – {pct(r.upper, 1)}
        </div>
      </td>
      <td>
        <span className={`band ${band.cls}`}>{band.label}</span>
      </td>
      <td className="dim">{baseline ? pct(baseline.result.rate, 1) : "—"}</td>
      <td className={diff == null ? "dim" : diff > 0 ? "pos" : "neg-v"}>
        {diff == null ? "—" : `${diff > 0 ? "+" : ""}${(diff * 100).toFixed(1)}%`}
      </td>
      <td className="dim">
        {split.mean_value != null ? (
          <>
            {num(split.mean_value, 1)}
            {baseline?.mean_value != null && (
              <div style={{ fontSize: 9.5 }}>
                vs {num(baseline.mean_value, 1)}
              </div>
            )}
          </>
        ) : (
          "—"
        )}
      </td>
      <td>
        {split.holdout ? (
          <span
            className={split.holdout.direction_held ? "dim" : "neg-v"}
            title={`${split.holdout.season}: ${pct(split.holdout.rate.rate, 1)} on ${split.holdout.rate.n}`}
          >
            {split.holdout.status}
          </span>
        ) : (
          <span className="dim">—</span>
        )}
      </td>
    </tr>
  );
}

const PRESETS: { label: string; query: SplitQuery }[] = [
  { label: "High wind (15+)", query: { outdoor: true, wind_min: 15 } },
  { label: "Calm (under 8)", query: { outdoor: true, wind_max: 8 } },
  { label: "Cold (under 35°F)", query: { outdoor: true, temp_max: 35 } },
  { label: "Indoors", query: { roof: "dome" } },
  { label: "Divisional", query: { div_game: true } },
  { label: "Home underdog", query: { is_home: true, is_favourite: false } },
  { label: "Extra rest (+3)", query: { rest_advantage_min: 3 } },
];

export function NflSplits() {
  const [query, setQuery] = useState<SplitQuery>({ outdoor: true, wind_min: 15 });
  const [active, setActive] = useState("High wind (15+)");

  const { data: status } = useQuery({ queryKey: ["nfl-status"], queryFn: api.nflStatus });
  const { data, isLoading } = useQuery({
    queryKey: ["nfl-splits", query],
    queryFn: () => api.nflSplits(query),
  });

  const baseline = new Map((data?.baseline ?? []).map((m) => [m.market, m]));
  const tiny = data && data.n_team_games < 30;

  return (
    <div className="page">
      <p className="eyebrow">CONDITIONAL BASE RATES · 2020–2025</p>
      <h1 className="page-title">Splits</h1>
      <p className="page-sub">
        What has actually happened under these conditions — no model involved.
      </p>

      <div className="banner warn">
        <span className="banner-key">HOW TO READ</span>
        <span>
          Break-even at &minus;110 is <strong>52.4%</strong>, not 50%. A rate only means
          something if its whole interval clears that line, and with ~1,600 games in the pool
          a narrow filter leaves samples too small to say anything. The band and interval
          columns are there to stop a coincidence looking like an edge.
          {status && <> Holdout season is <strong>{status.holdout_season}</strong>.</>}
        </span>
      </div>

      <div className="presets">
        {PRESETS.map((p) => (
          <button
            key={p.label}
            className={`preset${active === p.label ? " active" : ""}`}
            onClick={() => {
              setActive(p.label);
              setQuery(p.query);
            }}
          >
            {p.label}
          </button>
        ))}
      </div>

      {isLoading ? (
        <div className="loading">LOADING SPLITS</div>
      ) : !data || data.n_team_games === 0 ? (
        <div className="empty">
          <b>No games match</b>
          Loosen the conditions — NFL seasons are short and filters cut the sample fast.
        </div>
      ) : (
        <>
          <div className="split-head">
            <span className="split-desc">{data.description}</span>
            <span className={`split-n${tiny ? " tiny" : ""}`}>
              {data.n_team_games} team-games
              {tiny && " — too few to draw conclusions"}
            </span>
          </div>

          <div className="table-wrap">
            <table>
              <thead>
                <tr>
                  <th className="label">MARKET</th>
                  <th>RATE</th>
                  <th>95% INTERVAL</th>
                  <th>SAMPLE</th>
                  <th>BASELINE</th>
                  <th>DIFF</th>
                  <th>MEAN PTS</th>
                  <th>HOLDOUT {data.holdout_season}</th>
                </tr>
              </thead>
              <tbody>
                {data.markets.map((m) => (
                  <SplitRow
                    key={m.market}
                    split={m}
                    baseline={baseline.get(m.market)}
                    breakEven={data.break_even}
                  />
                ))}
              </tbody>
            </table>
          </div>

          <p className="hint" style={{ marginTop: 14, maxWidth: 800 }}>
            The interval bar is drawn 0–100%; the vertical marker is break-even. When the bar
            straddles that marker, the split cannot be distinguished from a coin flip no
            matter how the headline percentage reads.
          </p>
        </>
      )}
    </div>
  );
}
