# Tiny Bot: Breadth-Scaled Momentum

[![make.fun bounty](https://make.fun/badge/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1.svg)](https://make.fun/b/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1?utm_source=github&utm_medium=badge&utm_campaign=tiny-bot-gorun)
[![rank](https://make.fun/badge/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1/Hagai17/tiny-bot-gorun/rank.svg)](https://make.fun/b/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1?utm_source=github&utm_medium=badge&utm_campaign=tiny-bot-gorun)
[![reward](https://make.fun/badge/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1/Hagai17/tiny-bot-gorun/reward.svg)](https://make.fun/b/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1?utm_source=github&utm_medium=badge&utm_campaign=tiny-bot-gorun)

Entry for the make.fun bounty [Tiny Bot, Easy $2: Beat Buy-and-Hold on Solana](https://make.fun/b/u-gorun-gadget-tiny-bot-easy-2-beat-a48f3ce1?utm_source=github&utm_medium=readme&utm_campaign=tiny-bot-gorun).

`main.py` is a paper-trading bot for the `agent-fun/bars-replay/v1` harness
(hourly bars of FARTCOIN, JITOSOL, POPCAT, RAY, SOL, WETH, WIF against USDC),
vendoring the kit's Python SDK in `vendor/makefun_bars.py`. It is long only
and deterministic: a pure function of the bars seen.

## Strategy

The final window is 3 unseen days, so the bot is regime-first rather than
fitted to the dev window:

1. **Breadth-scaled exposure** — the share of the universe with positive
   48-bar momentum caps how much equity may be invested. Falling market:
   mostly USDC. Rising market: fully invested.
2. **Strict momentum gates** — a symbol qualifies with 24-bar momentum
   above +0.2%, 48-bar momentum positive and its close above EMA(20);
   the top 4 by 24-bar momentum are held at equal weight (never past the
   harness 25% weight cap). Exit band with hysteresis, plus a +2%
   take-profit and a -5% stop read off each position's own cost.
3. **Scout sleeve** — if the trade count is still short of the 8-trade
   guardrail late in the window, one small (8% of equity) position in the
   relative-strength leader is recycled under a +2.5% / -3% / 10-bar exit
   set, a bounded-cost way to stay compliant in a market with nothing to
   hold.
4. **Throttled turnover** — full decisions every 4 bars, drift under 3% of
   equity ignored, so fees (10 bps + slippage + pool impact) are not
   churned away.

## Dev-window result

Graded with the kit's own `grade-local` (process driver) on the published
dev window, next to the house baselines graded the same way:

| entry | net PnL (USDC) | max drawdown | trades |
| --- | ---: | ---: | ---: |
| **this bot** | **-122.73** | **1.47%** | **30** |
| buy-and-hold (the bar) | -869.34 | 10.92% | 7 |
| hold-cash | 0.00 | 0.00% | 0 |
| ema-cross | -173.04 | 2.51% | 28 |
| momentum | -268.23 | 5.66% | 19 |
| adx-momentum | -114.42 | 1.38% | 11 |
| macd-cci | -20.22 | 2.46% | 17 |
| rsi-bollinger | -56.49 | 5.16% | 8 |

The dev window is a broad downtrend (every asset -3% to -14%), so the
defensive design goal shows: lose a little, stay compliant, and finish
far above buy-and-hold. The two executions the grader runs reproduced
byte for byte. The full design and variant record is in
[candidates.md](candidates.md).

## Run

The grader runs it as `python3 -P -s main.py` (Python 3.13, standard
library only); it speaks the bars-replay line protocol on stdin/stdout
through the vendored SDK.
