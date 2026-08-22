/** Display helpers. Odds are shown the way a bettor reads them, not the way we store them. */

export function american(price: number): string {
  return price > 0 ? `+${price}` : `${price}`;
}

export function pct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return `${(value * 100).toFixed(digits)}%`;
}

export function signedPct(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  const v = value * 100;
  return `${v >= 0 ? "+" : ""}${v.toFixed(digits)}%`;
}

export function num(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return value.toFixed(digits);
}

export function signed(value: number | null | undefined, digits = 1): string {
  if (value === null || value === undefined) return "—";
  return `${value >= 0 ? "+" : ""}${value.toFixed(digits)}`;
}

export function gameTime(iso: string): string {
  return new Date(iso).toLocaleString(undefined, {
    weekday: "short",
    hour: "2-digit",
    minute: "2-digit",
    hour12: false,
  });
}

export function relativeTime(iso: string | null | undefined): string {
  if (!iso) return "never";
  const mins = Math.round((Date.now() - new Date(iso).getTime()) / 60000);
  if (mins < 1) return "just now";
  if (mins < 60) return `${mins}m ago`;
  const hours = Math.round(mins / 60);
  if (hours < 24) return `${hours}h ago`;
  return `${Math.round(hours / 24)}d ago`;
}

/** EV bands. Below half a percent the "edge" is inside our own estimation noise. */
export function evClass(ev: number | null | undefined): string {
  if (ev === null || ev === undefined) return "";
  if (ev >= 0.03) return "pos";
  if (ev >= 0.005) return "thin";
  return "neg";
}

export function marketLabel(market: string, point?: number | null): string {
  if (market === "totals") return point != null ? `Total ${point}` : "Total";
  if (market === "spreads") return point != null ? `Spread ${signed(point, 1)}` : "Spread";
  return "Moneyline";
}
