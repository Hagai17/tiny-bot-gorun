"""Tiny Bot entry: breadth-scaled momentum rotation for agent-fun/bars-replay/v1.

Long only, hourly bars. The final window is only 3 days (72 bars) of prices
nobody has seen, so the bot is built regime-first, not fitted to the dev
window: its job is to lose almost nothing when the market falls, keep up
when it rises, and always meet the guardrails (>= 8 trades, drawdown
<= 25%, and a net PnL above buy-and-hold's for a paid place).

  1. Exposure follows market breadth. The share of the universe whose
     48-bar momentum is positive caps how much of the equity may be
     invested at all: in a falling market the bot mostly sits in USDC
     (buy-and-hold cannot), in a rising one it is fully invested.
  2. Inside that budget it holds the strongest qualifying symbols: 24-bar
     momentum above +0.2%, 48-bar momentum positive and the close above
     its EMA(20), ranked by 24-bar momentum, equal weight, never past the
     harness weight cap. Entry gates are strict (full lookbacks); only the
     breadth gauge reads momentum adaptively, and a cap alone never opens
     an entry. A held symbol is sold when it leaves the exit band
     (hysteresis: 24-bar momentum under -0.4% or close under the EMA).
  3. Positions also realise mechanically: a +2% take-profit and a -5%
     stop, read off the position's own cost (fees included, as the SDK
     reports it), checked on every bar.
  4. Scout sleeve: if the trade count is still short of the guardrail
     minimum late in the window, the bot recycles one small (8% of
     equity) position in the relative-strength leader under a tight
     +2.5% / -3% / 10-bar exit set, a bounded-cost way to stay compliant
     in a market that offers nothing to hold.
  5. Turnover is throttled: full decisions land every 4 bars and target
     drift inside 3% of equity is ignored, so the 10 bps fee + slippage +
     pool impact of every fill is not churned away.

Deterministic: a pure function of the bars seen; no clock, no environment,
no randomness. Orders fill at the next bar's open, a sell's proceeds never
fund a buy of the same reply (buys are sized on cash on hand, so a rotation
completes over two bars), and the replay flattens what is left at the end.
"""

import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(os.path.abspath(__file__)), "vendor"))
import makefun_bars

LOOKBACK = 24          # ranking momentum in bars (24h)
CONFIRM = 48           # trend momentum in bars (48h)
CONFIRM_MIN = 0.0      # trend momentum must exceed this to qualify
ENTER = 0.002          # ranking momentum must exceed this to qualify
EXIT_MOM = -0.004      # a held symbol is dropped below this ranking momentum
EMA_SPAN = 20          # trend EMA span in bars
EMA_BAND = 0.0         # held symbols tolerate close this far under the EMA
TOPK = 4               # hold at most this many symbols (0 = no limit)
EVERY = 4              # full decisions (targets + entries) every EVERY bars
DRIFT = 0.03           # ignore target drift under 3% of equity
BREADTH = 1            # 1: cap invested share by universe breadth; 0: no cap
TAKE_PROFIT = 0.02     # sell a position at this gain over its cost (0 = off)
STOP_LOSS = -0.05      # sell a position at this loss under its cost (0 = off)
COOLDOWN = 3           # bars a sold symbol waits before it can be re-bought
SCOUT_WEIGHT = 0.08    # equity share of one scout position
SCOUT_TP = 0.025       # scout take-profit over its cost
SCOUT_STOP = -0.03     # scout stop under its cost
SCOUT_MIN_TRADES = 8   # scout only while fewer orders than this were sent
SCOUT_AFTER = 16       # scout only from this bar on
SCOUT_MAX = 4          # at most this many scout positions per run
SCOUT_HOLD = 10        # bars after which a live scout is sold regardless
MAX_SLIPPAGE_BPS = 500

_closes = {}           # symbol -> [close, ...] oldest first
_last_t = {}           # symbol -> t of the last close recorded
_ema = {}              # symbol -> float EMA of closes
_cool = {}             # symbol -> bars of entry cooldown remaining
_scout = None          # symbol of the live scout position, if any
_scout_seq = -1        # bar the live scout was entered on
_scouts_used = 0
_orders_sent = 0


def _record(state, batch):
    """Fold this batch's bars into the per-symbol close history."""
    for bar in batch["bars"]:
        sym = bar["symbol"]
        t = bar["t"]
        if _last_t.get(sym) == t:
            continue
        _last_t[sym] = t
        c = int(bar["c"])
        hist = _closes.setdefault(sym, [])
        hist.append(c)
        k = 2.0 / (EMA_SPAN + 1)
        _ema[sym] = c if sym not in _ema else _ema[sym] + k * (c - _ema[sym])


def _momentum(sym, lookback, adaptive=False):
    """Momentum over `lookback` bars. Strict (the default) needs the full
    lookback; adaptive falls back to the whole history once at least 8 bars
    are in. The entry gates are strict; only the breadth gauge is
    adaptive, and a cap alone never opens an entry."""
    hist = _closes.get(sym, [])
    if len(hist) < 2:
        return None
    if len(hist) > lookback:
        use = lookback
    elif adaptive and len(hist) - 1 >= 8:
        use = len(hist) - 1
    else:
        return None
    return hist[-1] / hist[-1 - use] - 1.0


def on_batch(state, batch):
    global _scout, _scout_seq, _scouts_used, _orders_sent
    _record(state, batch)
    if state.warming_up:
        return []
    for name in list(_cool):
        _cool[name] -= 1
        if _cool[name] <= 0:
            del _cool[name]

    equity = state.equity
    if equity <= 0:
        return []
    min_notional = int(state.hello["fees"]["min_notional_quote_units"])
    weight_cap = int(state.hello["caps"]["max_weight_bps"]) * equity // 10_000
    universe = [sym["symbol"] for sym in state.hello["symbols"]]

    mom1, momc, values = {}, {}, {}
    for name in universe:
        held = state.positions.get(name)
        values[name] = state.value(name, held.qty) if held else 0
        mom1[name] = _momentum(name, LOOKBACK)
        momc[name] = _momentum(name, CONFIRM)

    # (1) Breadth: share of the universe with positive trend momentum
    # (adaptive, so the cap is meaningful before 48 bars exist; the cap
    # alone never opens an entry, the strict gates below do).
    trending = [n for n in universe
                if _momentum(n, CONFIRM, adaptive=True) is not None
                and _momentum(n, CONFIRM, adaptive=True) > 0]
    breadth = len(trending) / len(universe)
    invested_cap = equity * breadth if BREADTH else equity

    # (2) Desired set: fresh qualifiers plus held symbols still inside the
    # exit band (hysteresis), strongest ranking momentum first.
    desired = []
    for name in universe:
        held = state.positions.get(name)
        close = state.close(name)
        if mom1[name] is None or momc[name] is None or close is None:
            continue
        fresh = mom1[name] > ENTER and momc[name] > CONFIRM_MIN and close > _ema[name]
        keeps = bool(held) and mom1[name] > EXIT_MOM and \
            close > _ema[name] * (1.0 - EMA_BAND)
        if fresh or keeps:
            desired.append((mom1[name], name))
    desired.sort(reverse=True)
    if TOPK:
        desired = desired[:TOPK]
    names = [name for _, name in desired]

    # The scout is sold for real qualifiers once the trade count is safe.
    drop = set()
    if _scout is not None and names and _orders_sent >= SCOUT_MIN_TRADES:
        drop.add(_scout)
        _scout = None

    targets = {}
    if names:
        share = min(int(invested_cap) // len(names), weight_cap)
        for name in names:
            targets[name] = share

    # (3) Scout entry: the trade count is still short of the guardrail and
    # either nothing qualifies or the window is already half over.
    scout_buy = None
    if _scout is None and _scouts_used < SCOUT_MAX \
            and _orders_sent < SCOUT_MIN_TRADES and state.seq >= SCOUT_AFTER \
            and (not names or state.seq >= 48):
        ranked = [(mom1[n], n) for n in universe if mom1[n] is not None]
        if ranked:
            ranked.sort(reverse=True)
            if ranked[0][0] > -0.06:
                scout_buy = ranked[0][1]

    orders = []
    cash_left = state.cash
    sold = set()

    # Forced exits run on every bar: take-profit / stop on a position's own
    # cost (the scout under its tighter pair). Between decision bars they
    # are the only orders.
    forced = set()
    for name in universe:
        val = values[name]
        if val <= 0:
            continue
        held = state.positions[name]
        roi = val / held.cost - 1.0 if held.cost else 0.0
        tp, sl = (SCOUT_TP, SCOUT_STOP) if name == _scout else (TAKE_PROFIT, STOP_LOSS)
        if (tp and roi >= tp) or (sl and roi <= sl):
            forced.add(name)
        if name == _scout and state.seq - _scout_seq >= SCOUT_HOLD:
            forced.add(name)  # time stop: recycle the scout for trade count
    if _scout is not None and _scout in forced:
        _scout = None
    if state.seq % EVERY != 0 and scout_buy is None:
        for name in forced:
            held = state.positions[name]
            if values[name] >= min_notional:
                orders.append({"side": "sell", "symbol": name, "qty": held.qty,
                               "max_slippage_bps": MAX_SLIPPAGE_BPS})
                _cool[name] = COOLDOWN
        _orders_sent += len(orders)
        return orders[: int(state.hello["caps"]["max_orders_per_bar"])]

    # Decision bar: drops of non-desired symbols and trims of overweight
    # ones, then buys of underweight desired symbols on cash on hand.
    for name in universe:
        val = values[name]
        if val <= 0:
            continue
        held = state.positions[name]
        target = 0 if (name in forced or name in drop) else targets.get(name, 0)
        if name == _scout and name not in targets and name not in forced and name not in drop:
            target = int(equity * SCOUT_WEIGHT)  # hold the scout
        if target == 0:
            if val >= min_notional:
                orders.append({"side": "sell", "symbol": name, "qty": held.qty,
                               "max_slippage_bps": MAX_SLIPPAGE_BPS})
                sold.add(name)
                if name == _scout:
                    _scout = None
        elif val - target > DRIFT * equity:
            qty = held.qty * (val - target) // val
            if qty > 0 and state.value(name, qty) >= min_notional:
                orders.append({"side": "sell", "symbol": name, "qty": qty,
                               "max_slippage_bps": MAX_SLIPPAGE_BPS})
    for name in sold | forced:
        _cool[name] = COOLDOWN

    for name in names:
        if name in forced:
            continue
        deficit = targets[name] - values[name]
        if values[name] == 0 and name in _cool:
            continue
        if values[name] == 0 or deficit > DRIFT * equity:
            room = state.weight_room(name)
            spend = min(deficit, cash_left, room)
            if spend >= min_notional:
                orders.append({"side": "buy", "symbol": name,
                               "notional_quote_units": spend,
                               "max_slippage_bps": MAX_SLIPPAGE_BPS})
                cash_left -= spend

    if scout_buy is not None:
        spend = min(int(equity * SCOUT_WEIGHT), cash_left, state.weight_room(scout_buy))
        if spend >= min_notional:
            orders.append({"side": "buy", "symbol": scout_buy,
                           "notional_quote_units": spend,
                           "max_slippage_bps": MAX_SLIPPAGE_BPS})
            _scout = scout_buy
            _scout_seq = state.seq
            _scouts_used += 1

    orders = orders[: int(state.hello["caps"]["max_orders_per_bar"])]
    _orders_sent += len(orders)
    return orders


if __name__ == "__main__":
    makefun_bars.run(on_batch)
