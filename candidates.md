# Design and test record — Tiny Bot (bars-replay/v1)

This competition is a code bounty, not a judge bounty: the "drafts" are
strategy variants, and the "duel" is grading them against the house
baselines and across market regimes. The kit's judge is the replay itself
(net PnL on a sealed final window), so the playbook's lesson applies in
translated form: know the scoring exactly, test against the strongest
known opponents (the house baselines), and do not trust a number that
only holds on one sample (the dev window is one downtrend; the final
window is 3 unseen days, 72 bars).

## The scoring, from the kit

- Protocol `agent-fun/bars-replay/v1`: orders decided on bar k fill at
  bar k+1's open, at 10 bps fee + 5 bps modelled slippage + constant-
  product impact against the pool depth at that open. Pool depths are
  ~$1.5-9.5M per hop, so impact at our sizes is ~0.03-0.15%.
- Capital 10,000 USDC; caps: 25% weight per symbol, 10 positions, 16
  orders/bar; no shorts, no leverage.
- Guardrails: >= 8 filled trades, max drawdown <= 25%. Places by net
  PnL; a prize needs net PnL above the buy-and-hold baseline's.
- Determinism is graded: two executions, byte-identical action logs.
- Dev window: 136 hourly bars, every asset down (SOL -8.7%, RAY -13.9%,
  FARTCOIN -10.6%, POPCAT -8.8%, JITOSOL -8.7%, WIF -5.8%, WETH -3.0%).

## Baselines graded locally (kit grade-local, process driver)

| baseline | net PnL (USDC) | drawdown | trades |
| --- | ---: | ---: | ---: |
| buy-and-hold | -869.34 | 10.92% | 7 |
| hold-cash | 0.00 | 0.00% | 0 |
| ema-cross | -173.04 | 2.51% | 28 |
| momentum | -268.23 | 5.66% | 19 |
| adx-momentum | -114.42 | 1.38% | 11 |
| macd-cci | -20.22 | 2.46% | 17 |
| rsi-bollinger | -56.49 | 5.16% | 8 |

Lessons taken from them: exposure level dominates PnL in a trend;
unfiltered cross-sectional momentum (top-3, always invested) is the
worst active strategy in a decline; tiny stakes + quick profit-taking
(macd-cci) lose least in a decline but cannot keep up in a rise.

## Variants tested

Testing used a fast offline simulator implementing the protocol's fill
formulas, calibrated against grade-local (sim -360.60 USDC / 91 trades
vs grade-local -365.76 / 93 for the same v1 bot; buy-and-hold sim
-869.34 = grade-local exactly). Variants were scored on four real
sub-windows of the dev data plus two synthetic uptrend transforms
(mirrored closes; time-reversed negated returns), because the final
window's regime is unknown.

- **v1 — plain dual momentum** (24h momentum + EMA(20) gate, equal
  weight, hourly decisions): -365.76 grade-local, 93 trades. Churn:
  membership flips hourly, fees and adverse selection dominate.
- **v2/v3 — breadth cap + hysteresis + decision throttle (every 4
  bars) + take-profit/stop**: sim full window -84.92, 17 trades,
  drawdown 0.88%. But strict 48-bar gates starved short windows:
  a 72-bar segment produced 2 and 0 trades — a guardrail failure risk
  in the real 72-bar final.
- **v4 — faster gates** (12h momentum, EMA(24), adaptive warmup):
  whipsaw everywhere (-273 full window, 67 trades). Rejected: hourly
  crypto noise flips 12h signals constantly.
- **v5 — adaptive lookbacks everywhere** (momentum vs window start
  until the full lookback exists): early entries into the initial
  bounce of a decline, -397.87. Rejected: partial-window momentum is
  not a trend signal. Only the breadth gauge stayed adaptive.
- **v6/v7 — v3 gates + adaptive breadth + scout sleeve**: scout = one
  8%-of-equity position in the relative-strength leader, recycled for
  trade count. First version held the scout to the end (1 fill);
  a 10-bar time stop fixed the recycling (every real window >= 8
  trades after the fix).
- **Experiments rejected on evidence**: take-profit disabled at high
  breadth (worse on the uptrend proxy, -228 vs -123: jagged rises
  punish held runners); zero cooldown after take-profit (no effect);
  adaptive 48-bar gate (better on the uptrend proxy, -53 vs -123, but
  worse on real chop, -104 vs -38 on the middle segment; the entry
  gates stay strict on principle — partial-window momentum never
  opens an entry).

## Final design (as shipped in main.py)

Strict gates (24h momentum > +0.2%, 48h momentum > 0, close > EMA(20)),
top-4 equal weight inside a breadth-scaled exposure cap, exit band with
hysteresis, +2% take-profit / -5% stop on each position's own cost,
decisions every 4 bars with a 3% drift band, scout sleeve for the
trade-count guardrail, sells before buys and buys sized on cash on hand
(a sell's proceeds never fund the same reply's buys).

Offline sim, final code: real windows -98.01 / -34.55 / -32.30 / -38.48
USDC with 26 / 10 / 8 / 16 trades and drawdowns <= 1.17%; uptrend
proxies -123.35 and -152.12 (buy-and-hold makes +789 / +872 there — no
gated long-only bot keeps up with a lag-free fully-invested baseline in
a rising tape; the design goal there is to stay within reach, and in
falling or choppy tapes, where most 3-day windows live, to finish far
above it).

## Final verification (kit grade-local, the authority)

Dev window: **net PnL -122.73 USDC (-1.23%), max drawdown 1.47%,
30 trades, sharpe -0.116; the two executions reproduced byte for byte**
(result_sha256 2f00944d417eba8ebbf7ae9486791269d3128b86ade57e4bdbf8ee08dec77488).
Against the bar that matters: +746.61 USDC above buy-and-hold, with
every guardrail met.
