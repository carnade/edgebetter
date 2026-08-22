import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { api } from "../lib/api";
import { american, num, pct, signedPct } from "../lib/format";

// Each market carries its own labels. The volume and efficiency tiles mean different
// things across markets — for receptions the efficiency term is catch rate, and for rush
// attempts there is none at all, because the market IS the volume.
const MARKETS = [
  { key: "recv_yds", label: "Receiving yards", volume: "TARGETS", per: "YDS EACH", counts: false },
  { key: "rush_yds", label: "Rushing yards", volume: "CARRIES", per: "YDS EACH", counts: false },
  { key: "pass_yds", label: "Passing yards", volume: "ATTEMPTS", per: "YDS EACH", counts: false },
  { key: "receptions", label: "Receptions", volume: "TARGETS", per: "CATCH RATE", counts: true },
  { key: "rush_att", label: "Rush attempts", volume: "CARRIES", per: null, counts: true },
];

const marketConfig = (key: string) => MARKETS.find((m) => m.key === key) ?? MARKETS[0];

const TEAMS = [
  "ARI","ATL","BAL","BUF","CAR","CHI","CIN","CLE","DAL","DEN","DET","GB","HOU","IND",
  "JAX","KC","LA","LAC","LV","MIA","MIN","NE","NO","NYG","NYJ","PHI","PIT","SEA","SF",
  "TB","TEN","WAS",
];

/** Recent games as bars, so a projection can be sanity-checked against reality. */
function Form({ values }: { values: number[] }) {
  if (!values.length) return <span className="dim">—</span>;
  const max = Math.max(...values, 1);
  return (
    <span className="spark" title={values.map((v) => v.toFixed(0)).join(" · ")}>
      {values.map((v, i) => (
        <i key={i} style={{ height: `${Math.max(2, (v / max) * 14)}px` }} />
      ))}
    </span>
  );
}

export function NflProps() {
  const [market, setMarket] = useState("recv_yds");
  const [playerId, setPlayerId] = useState<string | null>(null);
  const [opponent, setOpponent] = useState("SF");
  const [line, setLine] = useState<string>("");

  const { data: players } = useQuery({
    queryKey: ["nfl-prop-players", market],
    queryFn: () => api.nflPropPlayers(market),
  });

  const selected = playerId ?? players?.[0]?.player_id ?? null;
  const parsedLine = line.trim() === "" ? undefined : Number(line);

  const { data: proj } = useQuery({
    queryKey: ["nfl-prop-project", selected, opponent, market, parsedLine],
    queryFn: () =>
      api.nflPropProject(selected!, opponent, market, Number.isFinite(parsedLine) ? parsedLine : undefined),
    enabled: !!selected,
  });

  return (
    <div className="page">
      <p className="eyebrow">PLAYER PROPS · YOUR LINE, OUR DISTRIBUTION</p>
      <h1 className="page-title">Props</h1>
      <p className="page-sub">
        Bring a line from any book. This prices it from the player&rsquo;s own
        distribution — no devigging, no second book needed.
      </p>

      <div className="presets" style={{ marginBottom: 10 }}>
        {MARKETS.map((m) => (
          <button
            key={m.key}
            className={`preset${market === m.key ? " active" : ""}`}
            onClick={() => {
              setMarket(m.key);
              setPlayerId(null);
            }}
          >
            {m.label}
          </button>
        ))}
      </div>

      <div className="prop-controls">
        <label>
          <span>PLAYER</span>
          <select value={selected ?? ""} onChange={(e) => setPlayerId(e.target.value)}>
            {(players ?? []).map((p) => (
              <option key={p.player_id} value={p.player_id}>
                {p.player_name} ({p.team})
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>OPPONENT</span>
          <select value={opponent} onChange={(e) => setOpponent(e.target.value)}>
            {TEAMS.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label>
          <span>LINE</span>
          <input
            type="number"
            step="0.5"
            placeholder="69.5"
            value={line}
            onChange={(e) => setLine(e.target.value)}
          />
        </label>
      </div>

      {!proj ? (
        <div className="loading">SELECT A PLAYER</div>
      ) : (
        <>
          {proj.calibration === "provisional" && (
            <div className="banner warn">
              <span className="banner-key">PROVISIONAL</span>
              <span>{proj.calibration_note}</span>
            </div>
          )}

          <div className="factor glass">
            <h3>
              {proj.player_name} <span className="dim">vs {proj.opponent}</span>{" "}
              <span className={`band ${proj.calibration === "validated" ? "band-solid" : "band-thin"}`}>
                {proj.calibration.toUpperCase()}
              </span>
            </h3>
            <p className="hint">{proj.market_label}</p>

            <div className="factor-grid">
              <div>
                {/* A count median is an integer; showing 2.0 receptions invites reading
                    it as a yards figure. */}
                <b>
                  {num(
                    proj.expected_median ?? proj.expected,
                    marketConfig(market).counts ? 0 : 1,
                  )}
                </b>
                <span>50/50 POINT</span>
              </div>
              <div>
                <b>{num(proj.expected, 1)}</b>
                <span>AVERAGE</span>
              </div>
              <div>
                <b>{num(proj.projected_volume, 1)}</b>
                <span>{marketConfig(market).volume}</span>
              </div>
              {/* Rush attempts has no efficiency term — the market is the volume, so the
                  ratio is always 1.0 and showing it would be noise. */}
              {marketConfig(market).per && (
                <div>
                  <b>{num(proj.projected_efficiency, 2)}</b>
                  <span>{marketConfig(market).per}</span>
                </div>
              )}
              <div>
                <b>{proj.snap_pct != null ? pct(proj.snap_pct, 0) : "—"}</b>
                <span>SNAP SHARE</span>
              </div>
              {proj.target_share != null && (
                <div>
                  <b>{pct(proj.target_share, 1)}</b>
                  <span>TARGET SHARE</span>
                </div>
              )}
              <div
                title={
                  "How much this defence has allowed in this market versus league average. " +
                  "Shown for context only — it is not applied to the projection, because " +
                  "no version of it predicted better than ignoring it."
                }
              >
                <b>{signedPct(proj.opponent_factor - 1, 1)}</b>
                <span>OPP vs LEAGUE · INFO ONLY</span>
              </div>
              <div>
                <b>{proj.games_of_history}</b>
                <span>GAMES USED</span>
              </div>
            </div>

            <div className="prop-form">
              <span className="dim" style={{ fontSize: 10, letterSpacing: "0.14em" }}>
                RECENT
              </span>
              <Form values={proj.recent_yards} />
              <span className="dim" style={{ fontSize: 10.5 }}>
                {proj.recent_yards.map((v) => v.toFixed(0)).join(" · ")}
              </span>
            </div>

            {proj.line != null && proj.prob_over != null ? (
              <div className="prop-verdict">
                <div>
                  <b className="pos">{pct(proj.prob_over, 1)}</b>
                  <span>OVER {proj.line}</span>
                </div>
                <div>
                  <b>{pct(proj.prob_under ?? 0, 1)}</b>
                  <span>UNDER {proj.line}</span>
                </div>
                <div>
                  <b>
                    {proj.implied_fair_american != null
                      ? american(proj.implied_fair_american)
                      : "—"}
                  </b>
                  <span>FAIR PRICE</span>
                </div>
                <p className="hint" style={{ margin: 0, flexBasis: "100%" }}>
                  Compare that fair price with what the book offers. A shorter price than
                  this is value; a longer one is not.
                </p>
              </div>
            ) : (
              <p className="factor-verdict">
                Enter a line above to price it against this distribution.
              </p>
            )}

            {proj.notes.length > 0 && (
              <p className="factor-verdict">{proj.notes.join(" · ")}</p>
            )}
          </div>
        </>
      )}
    </div>
  );
}
