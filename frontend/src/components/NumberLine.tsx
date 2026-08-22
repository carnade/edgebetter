/** The signature graphic: market total and model total on one axis, gap shaded.
 *
 *  This is the whole product in one picture — an edge is the distance between what
 *  the market says and what the model says. When there is no market price, it says so
 *  plainly rather than drawing a half-empty axis.
 */
import { num } from "../lib/format";

interface Props {
  market?: number | null;
  model?: number | null;
  unit: string;
  /** Half-width of the visible axis, in the same unit as the values. */
  span: number;
  noMarketNote?: string;
}

export function NumberLine({ market, model, unit, span, noMarketNote }: Props) {
  if (model == null && market == null) {
    return (
      <div className="numberline">
        <div className="nl-head">
          <span>MARKET VS MODEL</span>
        </div>
        <div className="nl-empty">no line, no projection</div>
      </div>
    );
  }

  // Centre the axis on whichever value exists, so a lone mark still sits mid-track.
  const centre = market ?? model ?? 0;
  const toPct = (v: number) => {
    const clamped = Math.max(-span, Math.min(span, v - centre));
    return 50 + (clamped / span) * 42; // leave 8% breathing room at each end
  };

  const marketPct = market != null ? toPct(market) : null;
  const modelPct = model != null ? toPct(model) : null;
  const gap = market != null && model != null ? model - market : null;

  return (
    <div className="numberline">
      <div className="nl-head">
        <span>MARKET VS MODEL</span>
        {gap != null && (
          <span style={{ color: Math.abs(gap) >= 1 ? "var(--green)" : "var(--muted-dim)" }}>
            {gap >= 0 ? "+" : ""}
            {num(gap, 1)} {unit}
          </span>
        )}
      </div>

      <div className="nl-track">
        <div className="nl-axis" />

        {marketPct != null && modelPct != null && (
          <div
            className="nl-gap"
            style={{
              left: `${Math.min(marketPct, modelPct)}%`,
              width: `${Math.abs(modelPct - marketPct)}%`,
            }}
          />
        )}

        {marketPct != null && (
          <div className="nl-mark" style={{ left: `${marketPct}%` }}>
            <i />
            <b>{num(market, 1)}</b>
            <span>MARKET</span>
          </div>
        )}

        {modelPct != null && (
          <div className="nl-mark model" style={{ left: `${modelPct}%` }}>
            <i />
            <b>{num(model, 1)}</b>
            <span>MODEL</span>
          </div>
        )}
      </div>

      {market == null && noMarketNote && (
        <div className="nl-empty" style={{ height: "auto", paddingTop: 24 }}>
          {noMarketNote}
        </div>
      )}
    </div>
  );
}
