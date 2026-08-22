/** Typed client for the EdgeBetter API. The browser only ever reads our cache —
 *  it never contacts the odds provider, which is what keeps credit use bounded. */

const BASE = "/api";

export type Sport = "mlb" | "nba";

export interface Team {
  id: number;
  abbrev: string;
  name: string;
  location?: string | null;
  conference?: string | null;
  division?: string | null;
}

export interface Pitcher {
  id: number;
  name: string;
  era?: number | null;
  whip?: number | null;
  k_per_9?: number | null;
  bb_per_9?: number | null;
  wins?: number | null;
  losses?: number | null;
  innings_pitched?: number | null;
  games_started?: number | null;
  innings_per_start?: number | null;
  recent_form: number[];
}

export interface Projection {
  home_score: number;
  away_score: number;
  total: number;
  margin: number;
  prob_home_win: number;
  blended: boolean;
  possessions?: number | null;
}

export interface Price {
  bookmaker: string;
  market: string;
  outcome: string;
  american: number;
  decimal: number;
  point?: number | null;
  fetched_at: string;
}

export interface Edge {
  id: number;
  game_id: number;
  sport: string;
  market: string;
  selection: string;
  point?: number | null;
  best_book: string;
  best_price_american: number;
  best_price_decimal: number;
  fair_prob: number;
  book_count: number;
  ev: number;
  kelly_quarter: number;
  model_prob?: number | null;
  model_ev?: number | null;
  model_line?: number | null;
  signals_agree?: boolean | null;
  matchup?: string | null;
  start_time?: string | null;
}

export interface Game {
  id: number;
  sport: string;
  external_id: string;
  start_time: string;
  status: string;
  is_final: boolean;
  home: Team;
  away: Team;
  home_score?: number | null;
  away_score?: number | null;
  home_pitcher?: Pitcher | null;
  away_pitcher?: Pitcher | null;
  projection?: Projection | null;
  best_total?: number | null;
  best_total_book?: string | null;
  top_edge_ev?: number | null;
  edge_count: number;
}

export interface GameDetail extends Game {
  prices: Price[];
  edges: Edge[];
  line_history: Price[];
}

export interface TeamStats {
  team: Team;
  season: number;
  games_played?: number | null;
  points_for?: number | null;
  points_against?: number | null;
  off_rating?: number | null;
  def_rating?: number | null;
  net_rating?: number | null;
  pace?: number | null;
  runs_for?: number | null;
  runs_against?: number | null;
  team_era?: number | null;
  team_whip?: number | null;
  team_ops?: number | null;
  source?: string | null;
}

export interface RotationSlot {
  player_id: number;
  name: string;
  rank: number;
  rotation_size: number;
  era?: number | null;
  regressed_era: number;
  whip?: number | null;
  k_per_9?: number | null;
  games_started: number;
  is_top_two: boolean;
  is_bottom_two: boolean;
}

export interface Mismatch {
  game_id: number;
  start_time: string;
  matchup: string;
  favourite: string;
  underdog: string;
  favourite_is_home: boolean;
  score: number;
  strict: boolean;
  team_gap: number;
  era_gap: number;
  favourite_team_rank: number;
  underdog_team_rank: number;
  favourite_team_tier: string;
  underdog_team_tier: string;
  favourite_pythagorean: number;
  underdog_pythagorean: number;
  favourite_pitcher?: RotationSlot | null;
  underdog_pitcher?: RotationSlot | null;
  model_win_prob?: number | null;
  market_fair_prob?: number | null;
  best_american?: number | null;
  best_book?: string | null;
  risk_to_win_one?: number | null;
  ev?: number | null;
  model_ev?: number | null;
  kelly_quarter?: number | null;
  book_count: number;
  verdict: string;
  model_disagrees: boolean;
  grade: "bet" | "near miss" | "pass" | "unpriced";
  checks: GateCheck[];
  passed_checks: number;
  blocking_reason?: string | null;
  break_even_prob?: number | null;
  band_label?: string | null;
  band_win_rate?: number | null;
  band_break_even?: number | null;
  band_sample?: number | null;
}

export interface GateCheck {
  key: string;
  label: string;
  passed: boolean;
  detail: string;
}

export interface MismatchBand {
  label: string;
  wins: number;
  games: number;
  win_rate: number;
  break_even_american?: number | null;
}

export interface MismatchEvidence {
  bands: MismatchBand[];
  strict_wins: number;
  strict_games: number;
  strict_win_rate?: number | null;
  strict_break_even_american?: number | null;
  baseline_home_win_rate: number;
  caveat: string;
}

export interface MarketRow {
  game_id: number;
  matchup: string;
  start_time: string;
  market: string;
  market_label: string;
  subject?: string | null;
  selection: string;
  point?: number | null;
  best_book: string;
  best_american: number;
  fair_prob: number;
  break_even_prob: number;
  book_count: number;
  ev: number;
  kelly_quarter: number;
  outliers: string[];
  model_value?: number | null;
  model_unvalidated: boolean;
}

export interface Budget {
  remaining: number;
  days_left: number;
  daily_allowance: number;
  reserve: number;
  game_level_cost_today: number;
  props_allowance: number;
  props_markets_per_game: number;
  props_games_today: number;
  reason: string;
}

export interface OddsStatus {
  enabled: boolean;
  reason: string;
  credits_remaining?: number | null;
  credits_used?: number | null;
  last_poll?: string | null;
  last_poll_ok?: boolean | null;
}

export interface SportStatus {
  sport: string;
  season: number;
  season_display: string;
  season_started: boolean;
  prior_season: number;
  upcoming_games: number;
  teams_with_stats: number;
}

// ----------------------------------------------------------------- NFL
export interface NflRate {
  hits: number;
  n: number;
  rate: number;
  lower: number;
  upper: number;
  band: "noise" | "suggestive" | "moderate" | "meaningful";
  verdict: string;
  beats_break_even: boolean;
}

export interface NflHoldout {
  season: number;
  rate: NflRate;
  status: string;
  direction_held: boolean;
  survives: boolean;
  gap: number;
}

export interface NflMarketSplit {
  market: string;
  label: string;
  result: NflRate;
  holdout?: NflHoldout | null;
  mean_value?: number | null;
  mean_low?: number | null;
  mean_high?: number | null;
}

export interface NflSplitReport {
  description: string;
  n_team_games: number;
  break_even: number;
  holdout_season: number;
  markets: NflMarketSplit[];
  baseline: NflMarketSplit[];
}

export interface NflGame {
  game_id: string;
  season: number;
  week: number;
  gameday: string;
  home_team: string;
  away_team: string;
  home_score?: number | null;
  away_score?: number | null;
  total_line?: number | null;
  spread_line?: number | null;
  roof?: string | null;
  surface?: string | null;
  temp?: number | null;
  wind?: number | null;
  div_game: boolean;
  home_qb_name?: string | null;
  away_qb_name?: string | null;
  home_rest?: number | null;
  away_rest?: number | null;
  projected_total?: number | null;
  projected_margin?: number | null;
  projection_thin: boolean;
}

export interface NflTeamRating {
  team: string;
  games: number;
  points_for?: number | null;
  points_against?: number | null;
  off_epa_per_play?: number | null;
  def_epa_per_play?: number | null;
  net_epa?: number | null;
  plays_per_game?: number | null;
}

export interface NflStatus {
  seasons: number[];
  completed_games: number;
  scheduled_games: number;
  upcoming_season?: number | null;
  holdout_season: number;
  model_beats_market: boolean;
  model_note: string;
}

export interface NflQbImpact {
  backup_games: number;
  starter_games: number;
  backup_points: number;
  starter_points: number;
  points_swing: number;
  backup_spread: number;
  starter_spread: number;
  line_swing: number;
  backup_ats: NflRate;
  starter_ats: NflRate;
  fade_rate: number;
  verdict: string;
}

export interface NflPartials {
  games: number;
  first_quarter_mean: number;
  first_half_mean: number;
  second_half_mean: number;
  full_mean: number;
  first_half_share: number;
  first_half_cv: number;
  second_half_cv: number;
  first_half_more_stable: boolean;
  scoreless_first_quarter: NflRate;
  verdict: string;
}

export interface NflMovement {
  game_id: string;
  matchup: string;
  week: number;
  observations: number;
  open_total?: number | null;
  latest_total?: number | null;
  total_drift?: number | null;
  open_spread?: number | null;
  latest_spread?: number | null;
  spread_drift?: number | null;
  model_total_at_open?: number | null;
  model_disagreement?: number | null;
  moved_our_way?: boolean | null;
}

export interface NflClv {
  tracked_games: number;
  games_with_movement: number;
  resolved: number;
  ready: boolean;
  clv_rate?: NflRate | null;
  mean_drift: number;
  mean_abs_drift: number;
  verdict: string;
  movements: NflMovement[];
}

export interface PropCandidate {
  player_id: string;
  player_name: string;
  position?: string | null;
  team?: string | null;
  games: number;
}

export interface PropProjection {
  player_id: string;
  player_name: string;
  position?: string | null;
  team?: string | null;
  opponent: string;
  market: string;
  market_label: string;
  expected: number;
  expected_median?: number | null;
  sd: number;
  games_of_history: number;
  band: string;
  trustworthy: boolean;
  projected_volume: number;
  projected_efficiency: number;
  opponent_factor: number;
  context_factor: number;
  snap_pct?: number | null;
  target_share?: number | null;
  recent_yards: number[];
  notes: string[];
  calibration: "validated" | "provisional";
  calibration_note: string;
  worst_calibration_gap: number;
  line?: number | null;
  prob_over?: number | null;
  prob_under?: number | null;
  implied_fair_american?: number | null;
}

export interface GradedProp {
  player_name: string;
  player_id?: string | null;
  team?: string | null;
  opponent: string;
  market: string;
  market_label: string;
  side: string;
  line: number;
  book: string;
  price_american: number;
  projected: number;
  projected_median?: number | null;
  model_prob: number;
  break_even: number;
  edge: number;
  required_edge: number;
  edge_ratio: number;
  expected_value: number;
  grade: "A" | "B" | "C" | "D";
  grade_description: string;
  reason: string;
  games_of_history: number;
  band: string;
  calibration: string;
  recent_yards: number[];
  recent_over: number;
  recent_counted: number;
  books_posting: number;
  line_span?: number | null;
  coverage_warning?: string | null;
}

export interface PropScan {
  season?: number | null;
  week?: number | null;
  lines_seen: number;
  graded_count: number;
  actionable_count: number;
  players_without_history: number;
  one_sided_warning?: string | null;
  coverage_warning?: string | null;
  missing_games_warning?: string | null;
  games_in_week: number;
  games_with_lines: number;
  grade_counts: Record<string, number>;
  props: GradedProp[];
}

export interface SplitQuery {
  outdoor?: boolean;
  wind_min?: number;
  wind_max?: number;
  temp_min?: number;
  temp_max?: number;
  roof?: string;
  surface?: string;
  div_game?: boolean;
  is_home?: boolean;
  is_favourite?: boolean;
  rest_advantage_min?: number;
  team?: string;
  team_total_line?: number;
}

async function get<T>(path: string): Promise<T> {
  const res = await fetch(`${BASE}${path}`);
  if (!res.ok) {
    throw new Error(`${res.status} ${res.statusText} — ${path}`);
  }
  return res.json() as Promise<T>;
}

export const api = {
  oddsStatus: () => get<OddsStatus>("/odds/status"),
  sportStatus: (s: Sport) => get<SportStatus>(`/${s}/status`),
  slate: (s: Sport, hours = 36) => get<Game[]>(`/${s}/slate?hours_ahead=${hours}`),
  game: (id: number) => get<GameDetail>(`/games/${id}`),
  edges: (s: Sport, agreeOnly = false) =>
    get<Edge[]>(`/${s}/edges?min_ev=0&agree_only=${agreeOnly}`),
  teams: (s: Sport) => get<TeamStats[]>(`/${s}/teams`),
  pitchers: () => get<Pitcher[]>("/mlb/pitchers?limit=80"),
  mismatches: (strictOnly = false) =>
    get<Mismatch[]>(`/mlb/mismatches?hours_ahead=48&strict_only=${strictOnly}`),
  mismatchEvidence: () => get<MismatchEvidence>("/mlb/mismatches/evidence"),
  markets: () => get<Record<string, MarketRow[]>>("/mlb/markets"),
  budget: () => get<Budget>("/odds/budget"),

  nflStatus: () => get<NflStatus>("/nfl/status"),
  nflRatings: (season?: number) =>
    get<NflTeamRating[]>(`/nfl/ratings${season ? `?season=${season}` : ""}`),
  nflSchedule: (season: number, week?: number, project = true) =>
    get<NflGame[]>(
      `/nfl/schedule?season=${season}${week ? `&week=${week}` : ""}&project=${project}`,
    ),
  nflQbImpact: () => get<NflQbImpact>("/nfl/qb-impact"),
  nflPropScan: (week?: number, minGrade = "D") =>
    get<PropScan>(`/nfl/props/scan?min_grade=${minGrade}${week ? `&week=${week}` : ""}&limit=150`),
  nflPropPlayers: (market: string, search = "") =>
    get<PropCandidate[]>(
      `/nfl/props/players?market=${market}&limit=60${search ? `&search=${encodeURIComponent(search)}` : ""}`,
    ),
  nflPropProject: (playerId: string, opponent: string, market: string, line?: number) =>
    get<PropProjection>(
      `/nfl/props/project?player_id=${encodeURIComponent(playerId)}&opponent=${opponent}` +
        `&market=${market}${line != null ? `&line=${line}` : ""}`,
    ),
  nflMovement: (season = 2026) => get<NflClv>(`/nfl/movement?season=${season}`),
  nflPartials: () => get<NflPartials>("/nfl/partials"),
  nflSplits: (q: SplitQuery) => {
    const params = new URLSearchParams();
    for (const [k, v] of Object.entries(q)) {
      if (v !== undefined && v !== null && v !== "") params.set(k, String(v));
    }
    return get<NflSplitReport>(`/nfl/splits?${params.toString()}`);
  },
};
