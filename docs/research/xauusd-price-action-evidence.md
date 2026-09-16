# XAUUSD price action: what the evidence supports

Evidence review for the Qlip EA, September 2026. Full readable report:
https://claude.ai/artifact/EmX3Gcpeq3LynLLYdzkygS

All figures are in MT5 points (`$0.01/oz`) or dollars per ounce. The word "pip" is
ambiguous on gold — one broker's pip is `$0.01`, another's is `$0.10`, and most
published gold "pip" figures were written when gold was $1,800–2,400 and copied
forward unchanged. Never port a pip figure from an article.

Grades: **(a)** quantitative with disclosed methodology · **(b)** practitioner
consensus, no data · **(c)** marketing, discard.

## The finding that drives everything else

Cost is a first-order term, not a rounding error. Friction divided by the average
range of one bar decides whether a timeframe is tradeable at all:

| Timeframe | Session | Friction as % of bar range |
|---|---|---|
| M1 | Asia | 28–49% |
| M1 | London/NY | 10–15% |
| M5 | Asia | 12–20% |
| M5 | London/NY | 4.4–6.3% |
| M15 | London/NY | 2.6–3.7% |

Duvinage (2013) tested 83 candlestick rules on 5-minute bars where this ratio was
~22%: after costs, 5 survived and none beat buy-and-hold. Mesfin (2026) tested 14
signal families on 5-minute MNQ where the ratio was ~6%: gross edge 0.07–1.50
points against 2.0 points of friction, nothing passed. **Trade only hours where
`friction / ATR(M5) < 0.08`.**

## Encodable rules

### Gates, before any signal logic

- `spread_points <= 20` (raw) / `<= 35` (standard).
- Session window from exchange-local time, not fixed UTC and not server hours.
  Hard-block 21:00–23:00 UTC and ±15 min around high-impact US releases.
- `ATR(14, M5) >= 250 points`.
- M5 for signals, M15/H1 for levels, M1 only for execution refinement — never for
  signal generation.

### Per trade

- `stop_distance >= 600 points ($6.00)`. Below that, friction exceeds 4.3% of R and
  eats the entire realistic intraday edge (best published: 0.13–0.18R gross).
- `stop_distance >= 10 * current_spread`.
- Signal bar must be **closed**. Level breaks must be closes beyond, by `>= 3 * spread`.
- Wick-based signals additionally need `wick_length >= 8 * spread`.
- Shorts need `+spread` of extra stop buffer: charts show bid, a short's SL triggers
  on `Ask >= SL`. Without this the EA has an undiagnosed short-side bleed.
- Don't place stops at or just beyond round numbers — stop clusters there propagate
  trends, so you get a bad fill on the way through (Osler 2005, complete RBS order
  book, 9,655 orders). Place targets just *before* round numbers, where take-profit
  clusters sit. At gold $4,300 only $50 and $100 increments still matter.

### Setup priority

1. **Displacement bar at an M15 level.** Body >= 70% of range, range >= 2.0x ATR(14),
   close beyond `L`, tick-volume z >= +1.5. Limit at the 50% body retracement, valid
   3 bars. Stop beyond the bar's origin or 1.0x ATR, whichever is wider.
2. **Opening range break** on the London or NY open. Only when OR width > 0.6x ATR
   (wide → 77.5% continuation, narrow → 62.9% — the opposite of the popular claim).
   Require a 5-minute close, not a wick touch. Time-out the pending order.
3. **Break and retest**, only between 1.0x and 1.35x extension, only on a fast move,
   only in the first third of the session. Past ~1.4x, fading beats following.
4. **Engulfing at a level**, with size and location filters. Note the study measured
   against Open/High/Low, not Close.

Everything else — FVG, order blocks, doji, hammer, star, three soldiers — is a
context filter or a veto, never an entry.

### Exits

- `E[P&L] = μ · E[τ] − costs` for any exit rule. **Exits do not create edge.** They
  set how long you are exposed to whatever drift exists.
- Geometry follows the process: mean-reverting → wide stop, tight target;
  momentum → the reverse; random walk → all geometries identical and any tuning is
  overfitting.
- **Do not move stops to break-even on a fixed-R trigger.** From +0.5R with a 3R
  target, BE cuts P(reach target) from 37.5% to 16.7%. Move to a *structural* level
  instead (a newly formed higher low).
- **Trailing stops get worse as they tighten**, monotonically: Sharpe 0.54 at a 12%
  trailing stop down to −0.11 at 3%, net of costs, with no level beating the
  underlying trend rule (Clare et al. 2013).
- Short-horizon stops have negative expected value under the one paper most often
  cited to justify them: 3-, 5- and 10-day windows produced negative stopping
  premiums; only monthly-and-longer worked (Kaminski & Lo 2014). Sufficient
  condition for a stop to add value: `ρ/(1−ρ) > SR`.
- **Scaling out is variance reduction, not expectancy improvement.** The cleanest
  public test: CAGR 15.81% → 10.50–11.31%, drawdown equal or worse, even though
  per-trade expectancy rose — the loss comes from capital lockup.
- Use a **time barrier**. It has the best evidence-to-complexity ratio of any exit.

### Layering

- Worst-case loss scales as `k(k−1)/2`. Three layers, four maximum.
- Spacing `max(0.75 * ATR(H1), 20 * spread)`, read once at plan time and frozen.
  Never fixed-point: when volatility doubles, a fixed grid fills twice as fast at the
  same lot, doubling exposure exactly when the market is most dangerous.
- Flat or decreasing lots. Never increasing.
- Add only on favourable excursion, with the scenario score at least as strong as at
  entry, structure unbroken (close, not wick), and `ATR <= 1.5 * ATR_at_plan_time`.
- Add sizing: `required_advance_in_N = 2 * (add_size / base_size)`.
- Partial closes: proportional shaving first, most-losing leg second, most-profitable
  leg **never** — closing the winner raises the remaining basket's average entry.
- Recompute basket TP from *live* risk on every fill:
  `TP = RR * Σ(lot_i * 100 * |price_i − basket_SL|)`.
- Circuit breakers on **equity drawdown**, not margin level — a 20-layer basket at
  1:500 can destroy 67% of an account while margin level still reads 1,948%.
  Breakers must cancel pendings as well as close positions.

## ATR(14) on M15 is the wrong estimator for gold

Measured on 4,500 M15 bars and 2,513 daily bars of COMEX gold futures, not taken
from literature. Actual M15 range spans **2.91x** across the day; ATR(14) readings
span only **1.62x**, because a 14-bar lookback on M15 is 3.5 hours and smears across
every session boundary. The bias is systematic and runs exactly backwards:

| UTC | ATR(14) reads | Actual TR | Ratio |
|---|---|---|---|
| 12:00 | $7.94 | $14.04 | **0.57 — too tight at the US open** |
| 13:00 | $9.70 | $14.80 | 0.66 — too tight |
| 01:00 | $8.21 | $11.82 | 0.69 — too tight |
| 08:00 | $7.99 | $8.06 | 0.99 |
| 04:00 | $8.02 | $5.55 | 1.44 — too wide |
| 20:00 | $8.28 | $5.08 | **1.63 — too wide in the evening lull** |

Replacing ATR(14) with the mean true range of the **same 15-minute slot over the
prior 10 sessions** cuts the cross-hour calibration spread from **2.85x to 1.08x**.
Per-bar mean absolute error improves only 9.5% — most M15 range variance is
day-to-day noise, not seasonal — but that is the point: noise averages out across
trades, bias compounds across every trade taken at a given hour.

Other measured constants:

- **Express thresholds as % of price.** Daily ATR has run 0.75%–2.98% of price over a
  decade while gold tripled. Stable conversion: `ATR_daily ≈ 1.4 × σ_daily` (1.15–1.57
  across 11 years). Current M15 ATR ≈ $8.12 = **0.19% of price**.
- **2026 is a ~2x volatility outlier** with a **1.6-month half-life**. Anything tuned
  to today's $108 daily ATR will be roughly double-wide after reversion.
- **The 12:30 UTC bar needs its own regime, not a parameter**: median $13.40, mean
  $21.89, max $104.50, and 20% of them exceed $25. Note 08:30 ET is 13:30 GMT only
  under EST — in summer it is **12:30 UTC**, and MT5 server clocks usually follow EU
  DST, which transitions on different dates from US DST.
- **Asia is not uniformly quiet.** 00:00–02:00 UTC runs 1.17–1.37x (Shanghai morning);
  the real lull is 03:00–05:00 UTC at 0.64–0.76x. Median Asian range $47.70 = 1.12% of
  price, or 0.70x the London+NY range.
- **Gold's Hurst exponent sits closer to 0.5** than equity indices. The gold intraday
  efficiency study uses `|1 − VR|` — the *absolute* deviation — so it says the Asian
  session departs most from a random walk and is **silent on the direction**. Running a
  *signed* variance ratio on our own XAUUSD data is the highest-value experiment
  available to us.

## Sample size

`N = 4R / (δ²(R+1)²)` trades for a t of 2. At a 3pp edge that is ~833–1,111; at a
2pp edge, ~1,875–2,500. The dashboard's 1,000-basket threshold is too lenient below
a 3pp edge. Separately: with zero blow-ups in *n* baskets, the 95% upper bound on
per-basket blow-up probability is ~`3/n` — a flawless 500-basket curve is consistent
with near-certain ruin over the next 1,000.

## Open items for our EA

- V5's `$5` TP against `$30` SL is an inverted 1:6 geometry — a bet that XAUUSD is
  strongly mean-reverting on a ~2-minute horizon. Measure the autocorrelation before
  tuning any parameter.
- V5 has no session window. Adding one is likely the single largest available
  improvement.
- `economic_calendar.py` always returns `blackout: False`. Until it is real, hardcode
  a static UTC table for CPI, NFP and FOMC — a stub that always says "safe" reads as
  a control in review.
- V4's `+$3.00` partial, immediate break-even move, and `+$10` runner cap truncate
  the distribution three times. Express all three as ATR multiples.
- V5 uses market orders exclusively, 9 per basket. Taiwan's complete trading record
  1992–2006 found virtually all individual trading losses trace to aggressive orders.

## Claims to discard

- "FXCM study: 62% win rate with multi-timeframe confirmation vs 45% single-frame" —
  appears fabricated; FXCM's real study is 43M trades about risk:reward and leverage.
- "Tighter Asian range means a more explosive London breakout" — the only large-sample
  test found the opposite.
- "Exits matter more than entries; random entry with a 3-ATR trailing stop wins 100%
  of the time" — the only serious replication produced 100 losing runs out of 100.
- Any win rate above ~70% with no sample size, date range, or cost assumption.
- "M15 ATR $12 while daily ATR $45" — arithmetically impossible.

## Two XAUUSD facts that break common rules

**Volume in MT5 is not volume.** On spot instruments it is a tick count — quote
updates your broker received — and `real_volume` is empty. It varies with how many
liquidity providers the broker connects. Every "confirm with 1.5x average volume"
rule is unimplementable in any meaningful sense; use it only as a z-score relative to
your own feed.

**Spot XAUUSD has no order book.** London gold is bilateral OTC between bullion banks
with no central limit order book, and your broker's feed is that broker's synthetic
price. There is no consolidated tape in which an institutional order block could be
visible.

## What has no evidence at all

No study establishes an ATR multiple for gold M15. No large-sample MAE or stop-hunt
study exists for gold or intraday FX. No rigorous head-to-head of fixed-R vs
structure vs ATR vs trailing targets. No quantitative test of scaling out on intraday
gold. No peer-reviewed study of SMC/ICT, BOS, CHoCH or order blocks — at all. No
controlled comparison of pyramiding against flat sizing.

The practitioner corpus specifies entries and initial stops in detail and says
essentially nothing testable about exits. Our exit logic has to be justified on our
own data.
