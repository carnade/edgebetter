# EdgeBetter

Finds edges between sportsbook prices and independent projections, for MLB and NBA.
React frontend, FastAPI backend, Postgres, all under Docker Compose. Every data source
is free.

## Quick start

```bash
cp .env.example .env          # optionally add THE_ODDS_API_KEY
./scripts/start_dev.sh
```

- Web UI — http://localhost:5174
- API docs — http://localhost:8001/docs

Ports are 5174 and 8001 rather than the usual 5173/8000 because both of those were
already in use on the development machine. Every port and bind address lives in `.env`
(`WEB_PORT`, `API_PORT`, `DB_PORT`, and a `*_BIND` for each); the defaults in
`docker-compose.yml` match, so the stack comes up with no `.env` port settings at all.

By default only the web UI is reachable from the network. The database and API bind to
`127.0.0.1`, because the api and worker reach Postgres over the internal compose network
and nothing outside the host needs either port.

## Running it: development vs production

| | `start_dev.sh` | `start_prod.sh` |
|---|---|---|
| frontend | Vite dev server, hot reload | static bundle behind nginx |
| backend | `uvicorn --reload` | no reload |
| source | live-mounted, edits apply instantly | baked into the image |
| restart policy | none | `unless-stopped`, survives reboot |
| use it on | the laptop, while working | the NAS, running unattended |

```bash
./scripts/start_dev.sh            # development
./scripts/start_prod.sh           # production
./scripts/start_prod.sh --logs    # ...and follow the logs
./scripts/start_dev.sh --down     # stop (the database volume is kept)
```

Production layers `docker-compose.prod.yml` over the base file rather than duplicating it.
One detail worth knowing if you edit either: Compose **concatenates** `ports` and `volumes`
across layered files instead of replacing them, so an overlay cannot remove a mapping the
base file declares. That is why nginx listens on 5173 — the same port the dev server used —
so a single port mapping serves both modes.

The `/api` route is the other thing the two modes handle differently. In development it is
`vite.config.ts`'s proxy; that proxy exists only while `npm run dev` runs, so production
replaces it with the equivalent block in `frontend/nginx.conf`. A production build without
that file renders fine and then 404s on every request it makes.

Updating a production deployment is `git pull && ./scripts/start_prod.sh` — the frontend is
compiled, so a code change needs the rebuild that script performs.

## Backups and moving between machines

`pgdata` is a **named Docker volume**, not a folder in this repo. Cloning the repo onto
another machine gives you the code and an empty database. The dump is the only thing that
carries collected data across — and prop lines in particular cannot be re-fetched, since
no free historical feed for them exists.

```bash
./scripts/backup.sh                    # -> ./backups/edgebetter-<timestamp>.sql.gz
./scripts/backup.sh /path/to/dir       # somewhere else
KEEP=30 ./scripts/backup.sh            # keep the 30 newest (default 14)

./scripts/restore.sh                   # restore the newest backup
./scripts/restore.sh <file>            # restore a specific one
```

`backup.sh` verifies its own output before keeping it — valid gzip, and at least one table
present — because a dump that fails midway still exits 0 through a pipe. `restore.sh`
replaces the database, so it prompts unless given `--force`, and it stops the api and
worker first: they hold open connections, and `DROP` waits on those.

Moving from one machine to another:

```bash
# on the old machine
./scripts/backup.sh

# on the new one, after cloning
cp .env.example .env                   # then add THE_ODDS_API_KEY and the DB password
./scripts/start_prod.sh
./scripts/restore.sh path/to/edgebetter-<timestamp>.sql.gz
```

`.env` is gitignored, so it is the one thing a clone will not bring with it.

### QNAP deployment, step by step

Written for QTS. Models and Container Station versions differ, so each step says what to
check rather than assuming — the three preflight checks exist to find blockers in the
first minute rather than halfway through.

**Before connecting.** Control Panel → Telnet / SSH → enable SSH, and note the port.

```bash
ssh admin@<nas-ip>            # or your own account
```

**1. Preflight.** All three must pass before anything else is worth doing.

```bash
docker --version              # Container Station provides this
docker compose version        # must be v2 — "docker-compose" v1 will not work
git --version
```

- `docker` not found: Container Station is not installed, or its binaries are not on the
  SSH PATH. Try `source /etc/profile`, or use the full path Container Station reports.
- `docker compose` missing while `docker-compose` exists: that is v1. Update Container
  Station; the scripts and the production overlay both assume v2.
- `git` not found: install it via Entware (`opkg install git git-http`), or clone on the
  laptop and copy the folder across with `scp -r`.

**2. Clone into a data volume.** Not `/root`, `/tmp`, or anywhere on the OS partition —
those are small and do not survive a firmware update. `/share/Container` is where Container
Station keeps its stacks, so alongside them is the natural home:

```bash
cd /share/Container
git clone git@github.com:carnade/edgebetter.git
cd edgebetter
```

HTTPS is fine too; SSH needs a deploy key on the NAS.

`/share/Container/edgebetter` is what `REMOTE_DIR` defaults to in `pull_remote.sh`. Clone
somewhere else and set `REMOTE_DIR` to match.

**3. Bring `.env` across.** It is gitignored, holds the API key and database password, and
is the one file the clone will not bring. From the laptop:

```bash
scp .env admin@<nas-ip>:/share/Container/edgebetter/.env
```

Then on the NAS, set a real `POSTGRES_PASSWORD`, and leave `REMOTE_HOST` blank — this
machine *is* the live stack.

**4. Start it.** The first build compiles the frontend and can take several minutes on
NAS hardware, longer on ARM models. That is normal, not a hang.

```bash
./scripts/start_prod.sh
```

**5. Restore the data.** From the laptop:

```bash
./scripts/backup.sh                                    # take a fresh one first
scp backups/edgebetter-<stamp>.sql.gz admin@<nas-ip>:/share/Container/edgebetter/backups/
```

Then on the NAS:

```bash
./scripts/restore.sh backups/edgebetter-<stamp>.sql.gz
```

**6. Verify.**

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml ps    # four services up
curl -s -o /dev/null -w '%{http_code}\n' http://localhost:5174/      # 200
docker compose -f docker-compose.yml -f docker-compose.prod.yml \
  exec -T db psql -U edgebetter -d edgebetter -tAc \
  'SELECT count(*) FROM nfl_player_games;'                            # 107252
```

Then open `http://<nas-ip>:5174` from any machine on the network.

**7. Enable SSH pull from the laptop** (optional, for development later):

```bash
ssh-copy-id admin@<nas-ip>        # run on the laptop
```

and set `REMOTE_HOST=admin@<nas-ip>` in the laptop's `.env`.

### What to check after the first week

The prop poll runs Tuesdays at 16:00 UTC and is the thing that cannot be caught up on:

```bash
docker compose -f docker-compose.yml -f docker-compose.prod.yml logs worker | grep nfl_props
```

### Developing against the live data

Once the NAS is the machine collecting data, development on the laptop wants a copy of
what it has gathered. Set `REMOTE_HOST` in `.env` and pull it down:

```bash
./scripts/pull_remote.sh --check     # verify SSH and that the remote db is up
./scripts/pull_remote.sh             # dump the remote database and restore it here
./scripts/pull_remote.sh --keep      # download only, restore later
```

**Do not point `DATABASE_URL` at the live database instead.** `backend/entrypoint.sh` runs
`alembic upgrade head` on every boot, so starting a dev stack against it while on a branch
carrying a new migration would rewrite the production schema before you ran a command.
Development also means experiments and the occasional destructive CLI run, and prop lines
are the one thing here that cannot be re-fetched — there is no historical feed for them.

So data flows one way, live host to laptop, and code flows the other way through git:

```
   NAS ──(pull_remote.sh)──►  laptop        data
   NAS  ◄──(git pull)─────── GitHub ◄── laptop      code
```

The app runs without an odds API key: stats, ratings, and projections all work, and the
UI says plainly that edges need a key.

## Loading data

Migrations run automatically on boot. Then:

```bash
# Baseball — today's slate with probable pitchers, plus season stats
docker compose exec api python -m app.cli ingest-mlb --date today --stats

# Basketball — backfill the completed 2025-26 season (a few minutes)
docker compose exec api python -m app.cli ingest-nba --backfill 2026

# Odds (needs a key) and edge computation
docker compose exec api python -m app.cli ingest-odds --sport mlb
docker compose exec api python -m app.cli edges --sport mlb
```

The `worker` container runs all of this on a schedule; the commands above are for
first load and debugging.

## Data sources

| Source | Auth | Used for |
|---|---|---|
| `statsapi.mlb.com` | none | schedule, probable pitchers, team hitting/pitching, pitcher game logs |
| ESPN public API | none | NBA team stats (pace, possessions), scoreboards, standings |
| The Odds API | key | h2h / totals / spreads across US books |
| `stats.nba.com` | none, bot-gated | **optional** official OFF/DEF rating and pace |

`stats.nba.com` is off by default (`ENABLE_NBA_STATS_ENRICH=false`). It works, but
throttles hard and unpredictably, so it is a nightly single-shot enricher that falls
back silently to the ESPN-derived numbers. The app is fully correct without it.

## Credit budget

The Odds API free tier is **500 credits/month**, and a call costs `markets × regions`.

| Sport | Markets | Cost/call | Polls/day | Credits/day |
|---|---|---|---|---|
| MLB | `h2h,totals` | 2 | 3 | 6 |
| NBA | `h2h,totals,spreads` | 3 | 3 | 9 |
| Both in season | | | | **15/day ≈ 465/month** |

Three guards keep it there: skip when no game starts within `ODDS_LOOKAHEAD_HOURS`
(this is what stops the NBA offseason burning credits), refuse below
`ODDS_CREDIT_RESERVE`, and record the provider's own quota headers after every call.
**The frontend never triggers an odds call** — it only reads stored snapshots.

## How an edge is computed

Two independent layers:

1. **Devigged market consensus.** Each book's implied probabilities are normalised to
   sum to 1, then the median across books is the fair probability. EV is measured
   against the best available price. This needs no model to be correct, so it is the
   primary signal. Stake is quarter Kelly.
2. **Projection model.** An independent estimate, shown alongside. Where both layers
   agree the row is marked `AGREE`, which is stronger than either alone.

### What the models are actually worth

Both were calibrated by walk-forward backtests (`cli backtest nba|mlb`) that use only
games played before each fixture, so nothing leaks from the future.

**NBA** — MAE 15.33 points against a 16.27 league-average baseline: about 5.8% better
than guessing, explaining ~9% of variance. Calibration tracks within 0.03 across the
range. Two constants were wrong on first build and are now measured, not assumed:

- `TOTAL_SIGMA` was set to 11.5 on the common claim that NBA totals have a sigma
  around 11–13. The real forecast RMSE is **19.3**. At 11.5 every probability was
  wildly overconfident — the calibration curve was off by up to 14 points in the tails.
- The ratio form over-projected totals by 1.21 points, now corrected explicitly.

**MLB** — team-level projections show **no skill** over a league-average baseline
(MAE 3.61 vs 3.59). In baseball the starting pitcher dominates, and team rate stats
barely move a single game. Treat the MLB model as a weak signal and lean on the
devigged consensus. The pitcher component is damped (`PITCHER_INDEX_DAMPING`) because
undamped ERA scaling produced home win probabilities up to 0.785, well outside what
books ever price.

Neither model is validated for profit. The devig layer is where reliable edges come
from; the projection is a second opinion, and the UI labels it as an estimate.

## What a live poll looks like

One MLB poll costs 2 credits and returned 602 snapshots across 21 games from 9 books.
Of 52 devigged totals outcomes, the best expected value was **-0.17%** — every totals
market was priced at or slightly worse than fair. Three moneyline edges cleared the
0.5% threshold, at +0.5% to +0.8%.

That ratio is normal and worth internalising: heavily-bet markets are efficient, and
real edges are thin and sporadic. A tool that regularly shows large edges is usually
finding a bug, a stale line, or a market it has misunderstood.

## Phase 2: per-event markets

Three markets beyond full-game moneyline: **first 5 innings** (moneyline + total),
**pitcher strikeouts**, and **team totals**. All three live only on
`/events/{id}/odds`, which bills `markets × regions` **per game** — one market across a
15-game slate costs 15 credits. Full-slate props are therefore unaffordable on the free
tier, so `services/credit_budget.py` allocates the monthly quota and the mismatch
ranking chooses which games are worth paying for.

The allocator adapts by season, verified:

| Month | Game-level cost | Props games/day | Projected month |
|---|---|---|---|
| Sept (MLB only) | 6/day | 2 | 420 |
| Oct (MLB + NBA) | 15/day | **0** | 465 |
| Nov (NBA only) | 9/day | 1 | 390 |

October degrades props to zero automatically rather than overrunning.

### What the models were worth: nothing

Both new models were built, backtested walk-forward, and **failed their gate**:

- **First 5 innings** — MAE 2.60 runs vs a 2.57 league-average baseline: **−1.1%**. The
  thesis was that removing bullpen variance would let the pitcher signal show. It did
  not. Measured identically, the full-game model scores −0.7%, so F5 is no better.
- **Pitcher strikeouts** — MAE 1.56 vs 1.57 for the pitcher's own running average:
  **+1.0%**. Calibration is genuinely excellent (within ±0.009 across the range) but
  there is no edge over a naive baseline. The backtest also chose Poisson over negative
  binomial on log loss, so `DISPERSION` is 1.0.

Neither drives a signal. The Markets tab prices purely by devig and line shopping, and
labels every projection `unvalidated`.

Taken with Phase 1 (NBA totals +5.8%, MLB full-game −0.7%), four independent modelling
attempts have produced nothing that beats the vig. **The consistent lesson is that we
cannot out-model these markets; any real edge is price discrepancy between books.**

### A real bug this surfaced

On an F5 moneyline, seven books priced Houston between −175 and −186 while one book had
the sides inverted at +135. The median-based fair probability shrugged it off, but
best-price selection grabbed the outlier and reported a **+43% edge**. Devig was robust;
price selection was not.

`services/devig.py` now quarantines any book whose implied probability sits more than
15 points from the median before shopping for the best price, and reports which books
were discarded. This affected the moneyline Edges tab too, not just props.

## NFL

A different tool from the baseball side: historical base rates rather than book-vs-book
price comparison. Data is `nflverse` (free, no key) — 1,887 games and 3,774 team-games
from 2020–2026, with play-by-play EPA for every season and the 2026 schedule preloaded.

```bash
docker compose exec api python -m app.cli nfl-ingest --from 2020 --pbp --injuries
docker compose exec api python -m app.cli nfl-splits --outdoor --wind-min 15
docker compose exec api python -m app.cli nfl-backtest
```

### What the data says

**Wind suppresses scoring, as advertised.** Outdoor games with wind ≥15mph average 42.7
total points against 44.1 for all outdoor games; team totals drop 22.0 → 21.4. Real, and
recovering it was the gate that proved the pipeline correct.

**Backup quarterbacks cost 4.6 points — and the market charges 3.9 for it.** Over 562
backup starts, teams scored 19.1 vs 23.7 with their established starter. Fading them went
52.5% against a 52.4% break-even: a lean, not an edge.

**The first half is genuinely steadier.** It carries 50.9% of scoring with 11% lower
variability than the second (CV 0.387 vs 0.437), so the scripted-drives idea holds up.

**The projection model does not beat the closing line.** 10.81 MAE on totals against the
market's 10.26 over 1,310 walk-forward games; 10.34 vs 9.82 on margin. Betting our side
went 50.3% on totals and 49.2% ATS.

### The bias bug worth knowing about

The first model over-projected totals by +1.82 points, and by season the error ran +1.17,
+3.04, +3.68, +4.04 — while the closing line was within half a point. `TeamRating`
accumulated every game since 2020 with equal weight, so a 2023 rating was a four-year
average dominated by much higher-scoring 2020–21 football. NFL scoring swung six points
across those seasons.

Exponential decay with a 10-game half-life cut bias to +0.48 and closed a third of the gap
to the market. A sensitivity sweep confirms MAE is flat across half-lives 8–12 (10.81–10.82)
and degrades to 11.19 with no decay, so the value sits on a plateau rather than a fitted
point.

### Line movement and closing line value

Every model here loses to the closing line. But a bet is placed at the price on offer
when you place it, not at the close — so the useful question is whether the market drifts
toward the number we would have taken at the open.

`nfl_line_history` appends every observation with the model's view **at that moment**
(stored, not recomputed later, so hindsight ratings cannot leak in). Openers are seeded
from nflverse, and the odds job polls daily at ~3 credits a call, roughly 90/month.

A game only resolves for CLV when the model disagreed with the opener by 1+ points and
the line then moved 0.5+. Games where we agreed with the market, or where nothing moved,
are excluded rather than counted as wins.

The panel refuses to report a number until 30 games have resolved — currently 4, so it
says so instead of showing a meaningless 75%.

### Why the model differential is not bettable

The obvious idea is to bet where the model disagrees most with the line. Measured across
1,296 games, it goes the other way:

| Disagreement | Win rate | n |
|---|---|---|
| 0–1 pts | 50.3% | 302 |
| 1–3 pts | 52.7% | 505 |
| 3–5 pts | 49.4% | 312 |
| 5–7 pts | 45.9% | 111 |
| **7+ pts** | **33.3%** | 66 |

The model gets monotonically worse the more it disagrees. A large differential means the
model broke, not that an edge appeared.

Fading it looked spectacular in exploration — 71.7% on 60 games — and collapsed to 16.7%
on the 2025 holdout. That is exactly what the holdout is for.

### Player props

Passing, rushing, and receiving yards, built from `stats_player_week` and `snap_counts`
— 107,252 player-games, 4,048 players, 2020–2025.

Deliberately **not** book-vs-book. You bring a line from anywhere and the tool prices it
from the player's own distribution, so no devigging and no second book are needed.

**Structure: usage is predictable, efficiency is not.** Yards are projected as volume ×
efficiency, with volume trusted (snap share and target share carry real week-to-week
signal) and efficiency regressed hard toward the positional mean. Opponent effects come
from every game a defence played, not from the two a given player faced them — a player
has 17 games a season, so his own conditional splits are always noise.

**The distribution is measured, not assumed.** Passing yards are near-symmetric (mean 232,
median 230); rushing and receiving are right-skewed (54/52 against medians of 46/44) with
tails at +1.40 SD where a normal predicts +1.28. A gamma covers both — near-symmetric at
low variability, properly skewed at high.

**Two bugs the calibration test caught:**

*Zero-inflation.* 16% of RB games have zero carries and 14% of WR games zero targets. A
gamma with a positive mean cannot produce that spike, and including those games wrecked
calibration below the mean — we claimed 37% to clear our own projection when reality was
27%. Everything is now conditioned on participation, which is also what the market does:
books void a prop if the player does not play.

*Too narrow a spread.* Fitted a 1.15 CV multiplier on 2021–2024 and checked it on 2025.

**Calibration, measured on held-out 2025:**

| Market | n | Worst gap | Status |
|---|---|---|---|
| Receiving | 1,959 | 0.022 | **validated** — inside sampling error |
| Rushing | 882 | 0.071 | provisional — ~3× SE, understates overs |
| Passing | 274 | 0.103 | provisional — ~3× SE, thin sample |

Only receiving is honest enough to price a bet from. The UI labels each market and warns
on the provisional ones rather than presenting all three as equal.

A hypothesis worth recording as **refuted**: game script does not explain the gap. QB
attempts run 28.2 → 30.2 → 27.5 from big favourite to big underdog — an inverted U, not
the monotonic rise the theory predicts — and RB carries move only 0.9 across the range.

### The prop scanner and its grade

`nfl-scan` grades every posted line on the slate. All three markets get identical
analysis; what differs is the bar an edge must clear, set by that market's own measured
calibration error — receiving 2.2 points, rushing 7.1, passing 10.3.

That single rule is the whole design. The same +5 point edge grades **A** on receiving and
**C** on passing, because on passing it sits inside our own error bars. Ranking uses the
edge-to-bar ratio rather than the raw edge, so a modest receiving edge correctly outranks
a larger passing one.

| Grade | Meaning |
|---|---|
| A | edge is more than double the bar |
| B | edge clears the bar |
| C | edge is positive but inside our error |
| D | no edge at this price |

A thin sample cannot earn an actionable grade whatever the numbers say.

Polling costs 3 markets × 16 games = **48 credits a week**, about 206/month, and runs
Thursdays once books have posted the slate.

### Venue and weather, measured within player

Domes and cold are in the projection, measured by comparing the **same player across
venues** so team quality and talent cancel out. Raw league averages overstate these badly:
cold-weather receiving looks 10.7% down in aggregate but only 3.6% within player, because
cold games are late-season games involving different teams.

| Effect | Receiving | Rushing | Passing |
|---|---|---|---|
| Indoors | +10% | +2% | +5% |
| Cold (<40F) | -3.5% | **+5%** | -4% |

Cold shifts offences toward the run, which is why rushing moves the other way. Opponent
defence versus position was already in the model, capped at +/-25%.

### Three bugs the recent-games column exposed

Adding "last 6 games versus this line" to the scanner immediately surfaced projections that
disagreed with a player's actual record, and two turned out to be real defects:

**Gamma shape below 1.** The spread cap was applied before the multiplier, so the effective
ceiling was 1.38 and shapes fell under 1 -- putting the distribution's mode at *zero yards*
for a starting receiver. Capped after the multiplier; two regression tests pin it.

**Mean and efficiency on different clocks.** Volume was recency-weighted while efficiency
was a flat career rate, so a back averaging 49 yards a game projected to 42. Both are now
decayed on the same half-life, and volume x efficiency reconciles with observed yards per
game by construction.

**Showing the mean, not the median.** Yardage is right-skewed, so the 50/50 point sits well
below the average -- a receiver can average 30 yards and still go under 28.5 most weeks. The
table showed only the average, which made picks look inconsistent with their own
projection. It now shows both.

Note the calibration test never caught the first two: it measures at offsets from our own
mean, where these errors partly cancel. A column showing what actually happened did.

### The under lean, investigated

The first scan returned six actionable picks, all Unders. That is the kind of one-sided
result that usually means a broken model, so it was checked rather than shipped.

It is not a bug. Real WR receiving has a median/mean ratio of **0.747** — a receiver's
median game is a quarter below his average — and our gamma reproduces that at **0.767**.
So a lean toward unders follows from a validated distribution shape, not from drift.

The scanner still warns when a scan lands 80%+ on one side, because a market-wide
one-sided signal is more often a drifting model than an edge nobody else noticed.

### Guarding against spurious splits

With only ~1,600 games, conditioning twice can leave a dozen — enough to produce a
convincing 9-3 record that means nothing. Three rules are structural, not optional:

- Every rate ships with a **Wilson confidence interval**, never a bare percentage.
- **Sample banding**: under 30 is labelled noise, under 100 suggestive, over 300 meaningful.
- **2025 is held out.** Anything found on earlier seasons must still hold there.

Break-even at −110 is **52.4%**, not 50%, and the verdict wording reflects that.

## Tests

```bash
docker compose exec api pytest -q          # 83 tests
docker compose exec api python -m app.cli backtest nba --season 2026
docker compose exec api python -m app.cli backtest mlb --season 2026
```

## Notes

- ESPN's API is undocumented; providers are deliberately thin so a shape change is a
  one-file fix.
- The NBA Cup championship is tagged as regular season by ESPN but does not count
  toward official stats, so it is excluded — otherwise the two finalists get an 83rd
  game and skewed ratings.
- MLB writes innings pitched in thirds (`5.1` is 5⅓, not 5.1); see
  `services/parsing.py`.
- Both upstreams report 0-0 for unplayed games; scores are only stored once a game
  has actually started.
- Only **pre-game** odds are stored. Once a game starts, books switch to in-play
  pricing — a total of 3.5 on a game whose full-game number was 9 is not a line that
  moved, it is a different market. Storing both would corrupt the line-movement
  history and invite meaningless comparisons against a full-game projection.
- This is read-only analysis. It does not place bets or connect to any sportsbook.
