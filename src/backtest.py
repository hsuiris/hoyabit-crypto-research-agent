"""Walk-forward backtest of the Vegas Channel + RSI strategy on daily OHLCV history.

Design note -- why this replays the strategy bar by bar instead of computing the signal series in
one pass: `analyze_vegas_channel` is called with `candles[:i + 1]`, so at every step the strategy
physically cannot see a bar it would not have had in real time. That makes look-ahead bias a
structural impossibility rather than something to be careful about, which matters more here than
speed: a backtest nobody trusts is worth nothing.

Known limitation, stated up front: the live agent runs this strategy on 4H/1H candles, but the
EMA987 tunnel needs ~1000 bars of warm-up and the only history long enough for that is the daily
CSV. These results therefore describe the rule set applied to *daily* bars, which is a different
regime from how the agent trades it. Treat them as evidence about the rules, not as a forecast.
"""

from __future__ import annotations

from .vegas_strategy import EMA_PERIODS, analyze_vegas_channel

# Binance spot taker fee, charged on entry and on exit. Ignoring costs is the most common way a
# backtest flatters a strategy that trades often, so it is on by default.
DEFAULT_FEE_PCT = 0.1
WARMUP_BARS = max(EMA_PERIODS) + 1

ENTRY_SIGNALS = {"bull_entry": "long", "bear_entry": "short"}
EXIT_SIGNALS = {"take_profit_bull": "long", "take_profit_bear": "short"}


def signal_series(candles: list[dict], **params) -> list[str | None]:
    """Replay the strategy over the history, returning the signal fired on each bar (or None)."""
    signals: list[str | None] = [None] * len(candles)
    for index in range(WARMUP_BARS, len(candles)):
        state = analyze_vegas_channel(candles[: index + 1], **params)
        # bars_since_signal == 0 means the signal belongs to this bar, not an older one.
        if state.get("bars_since_signal") == 0:
            signals[index] = state.get("last_signal")
    return signals


def _max_drawdown_pct(equity: list[float]) -> float:
    peak, worst = equity[0], 0.0
    for value in equity:
        peak = max(peak, value)
        if peak:
            worst = min(worst, (value - peak) / peak)
    return round(worst * 100, 2)


def run_backtest(candles: list[dict], dates: list[str], allow_short: bool = True,
                 fee_pct: float = DEFAULT_FEE_PCT, signals: list[str | None] | None = None,
                 **params) -> dict:
    """Trade the strategy's entry/exit signals and measure the result against buy-and-hold.

    Entries open a position at the close of the signal bar; the matching take-profit closes it. An
    opposite entry while already positioned reverses. Anything still open at the end is closed at
    the final close so the numbers describe completed round trips only.
    """
    if len(candles) <= WARMUP_BARS:
        raise ValueError(f"Backtest needs more than {WARMUP_BARS} bars, got {len(candles)}")
    if signals is None:
        signals = signal_series(candles, **params)

    closes = [bar["close"] for bar in candles]
    fee = fee_pct / 100
    equity_value = 1.0
    side: str | None = None
    entry_price = 0.0
    entry_index = 0
    trades: list[dict] = []
    equity_curve: list[float] = []
    bars_in_market = 0

    def close_position(index: int, reason: str):
        nonlocal equity_value, side
        exit_price = closes[index]
        raw = (exit_price - entry_price) / entry_price
        gross = raw if side == "long" else -raw
        net = (1 + gross) * (1 - fee) ** 2 - 1  # entry fee and exit fee
        equity_value *= 1 + net
        trades.append({
            "side": side,
            "entry_date": dates[entry_index], "exit_date": dates[index],
            "entry_price": round(entry_price, 4), "exit_price": round(exit_price, 4),
            "bars_held": index - entry_index,
            "return_pct": round(net * 100, 2),
            "exit_reason": reason,
        })
        side = None

    for index in range(WARMUP_BARS, len(candles)):
        signal = signals[index]
        if side is not None:
            bars_in_market += 1
            # Mark to market so the equity curve (and its drawdown) reflects open risk, not just
            # realised trades.
            raw = (closes[index] - entry_price) / entry_price
            open_gross = raw if side == "long" else -raw
            equity_curve.append(round(equity_value * (1 + open_gross) * (1 - fee) ** 2, 6))
        else:
            equity_curve.append(round(equity_value, 6))

        if signal in EXIT_SIGNALS and side == EXIT_SIGNALS[signal]:
            close_position(index, "take_profit")
        elif signal in ENTRY_SIGNALS:
            wanted = ENTRY_SIGNALS[signal]
            if wanted == "short" and not allow_short:
                continue
            if side == wanted:
                continue
            if side is not None:
                close_position(index, "reverse")
            side, entry_price, entry_index = wanted, closes[index], index

    if side is not None:
        close_position(len(candles) - 1, "end_of_data")
        equity_curve[-1] = round(equity_value, 6)

    test_closes = closes[WARMUP_BARS:]
    test_dates = dates[WARMUP_BARS:]
    buy_hold_pct = round((test_closes[-1] - test_closes[0]) / test_closes[0] * 100, 2)
    wins = [trade for trade in trades if trade["return_pct"] > 0]
    losses = [trade for trade in trades if trade["return_pct"] <= 0]
    gross_win = sum(trade["return_pct"] for trade in wins)
    gross_loss = abs(sum(trade["return_pct"] for trade in losses))

    return {
        "params": {"allow_short": allow_short, "fee_pct": fee_pct, **params},
        "window": {
            "date_start": test_dates[0], "date_end": test_dates[-1],
            "test_bars": len(test_closes), "warmup_bars": WARMUP_BARS, "total_bars": len(candles),
        },
        "strategy_return_pct": round((equity_value - 1) * 100, 2),
        "buy_hold_return_pct": buy_hold_pct,
        "excess_return_pct": round((equity_value - 1) * 100 - buy_hold_pct, 2),
        "trade_count": len(trades),
        "win_count": len(wins),
        "loss_count": len(losses),
        "win_rate_pct": round(len(wins) / len(trades) * 100, 1) if trades else None,
        "avg_win_pct": round(gross_win / len(wins), 2) if wins else None,
        "avg_loss_pct": round(-gross_loss / len(losses), 2) if losses else None,
        "profit_factor": round(gross_win / gross_loss, 2) if gross_loss else None,
        "max_drawdown_pct": _max_drawdown_pct(equity_curve) if equity_curve else 0.0,
        "exposure_pct": round(bars_in_market / len(test_closes) * 100, 1) if test_closes else 0.0,
        "avg_bars_held": round(sum(trade["bars_held"] for trade in trades) / len(trades), 1) if trades else None,
        "equity_curve": equity_curve,
        "equity_dates": test_dates,
        "trades": trades,
    }


def compare_variants(candles: list[dict], dates: list[str], fee_pct: float = DEFAULT_FEE_PCT) -> list[dict]:
    """Long-only (what spot can actually do) next to long+short, on one shared signal replay.

    The signal series is the expensive part and does not depend on position handling, so it is
    computed once and reused -- the variants differ only in whether shorts are taken.
    """
    signals = signal_series(candles)
    return [
        {"label": "多單 only（現貨可執行）", **run_backtest(candles, dates, allow_short=False, fee_pct=fee_pct, signals=signals)},
        {"label": "多空雙向（需合約）", **run_backtest(candles, dates, allow_short=True, fee_pct=fee_pct, signals=signals)},
    ]
