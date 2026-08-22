/** The prop markets, defined once.
 *
 * This lives here rather than in a page because the two pages that use it have already
 * drifted apart once: the scanner kept a hardcoded list of three markets while the props
 * page had five, so receptions and rush attempts were priced on one page and invisible on
 * the other. One definition, imported by both.
 */

export interface MarketDef {
  key: string;
  label: string;
  /** Short label for dense filter controls. */
  short: string;
  /** What the volume tile counts for this market. */
  volume: string;
  /** What the efficiency tile means, or null when the market has no efficiency term. */
  per: string | null;
  /** Outcomes are integers, so medians and lines render without decimals. */
  counts: boolean;
}

export const MARKETS: MarketDef[] = [
  { key: "recv_yds", label: "Receiving yards", short: "Recv yds", volume: "TARGETS", per: "YDS EACH", counts: false },
  { key: "rush_yds", label: "Rushing yards", short: "Rush yds", volume: "CARRIES", per: "YDS EACH", counts: false },
  { key: "pass_yds", label: "Passing yards", short: "Pass yds", volume: "ATTEMPTS", per: "YDS EACH", counts: false },
  { key: "receptions", label: "Receptions", short: "Receptions", volume: "TARGETS", per: "CATCH RATE", counts: true },
  // Rush attempts has no efficiency term: the market IS the volume, so the ratio is
  // always 1.0 and showing it would be noise.
  { key: "rush_att", label: "Rush attempts", short: "Rush att", volume: "CARRIES", per: null, counts: true },
];

export const marketDef = (key: string): MarketDef =>
  MARKETS.find((m) => m.key === key) ?? MARKETS[0];

export const isCountMarket = (key: string): boolean => marketDef(key).counts;

export const NFL_TEAMS = [
  "ARI","ATL","BAL","BUF","CAR","CHI","CIN","CLE","DAL","DEN","DET","GB","HOU","IND",
  "JAX","KC","LA","LAC","LV","MIA","MIN","NE","NO","NYG","NYJ","PHI","PIT","SEA","SF",
  "TB","TEN","WAS",
];
