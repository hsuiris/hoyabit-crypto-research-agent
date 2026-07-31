"""Python port of the user-supplied 'FibEMA / Vegas Channel + RSI' Pine Script strategy.

Reproduces, bar-by-bar, the same state machine as the original indicator:
- Vegas tunnel = max/min of EMA144 and EMA987 (Fibonacci-spaced EMA ribbon: 144/233/377/610/987).
- Trend alignment: bullish when EMA144>EMA233>EMA377>EMA610>EMA987 (and reverse for bearish).
- RSI(6) oversold/overbought crossovers, each required to have a volume-EMA(144) spike inside the
  oversold/overbought excursion before the crossover counts (mirrors the Pine `[1]`-referenced
  confirmation trick).
- Four independent signals: bull/bear entries (need trend + tunnel containment) and
  take-profit-bull/take-profit-bear exits (crossover/crossunder only, no trend/tunnel filter).

This needs a lot of history to seed EMA987 (the widest EMA in the ribbon), so it is meant to run
over a much longer OHLCV window (~1000 bars) than the rest of this project's 14-day quick metrics.
"""

from __future__ import annotations

EMA_PERIODS = (144, 233, 377, 610, 987)


def _padded_ema(values: list[float], period: int) -> list[float | None]:
    """EMA aligned to `values`: None until the SMA seed is available, then the recurrence."""
    if len(values) < period:
        return [None] * len(values)
    series: list[float | None] = [None] * (period - 1)
    seed = sum(values[:period]) / period
    series.append(seed)
    k = 2 / (period + 1)
    prev = seed
    for value in values[period:]:
        prev = value * k + prev * (1 - k)
        series.append(prev)
    return series


def _rsi_series(prices: list[float], period: int = 6) -> list[float | None]:
    """Wilder-smoothed RSI aligned to `prices` (matches Pine's ta.rsi), None during warm-up."""
    if len(prices) <= period:
        return [None] * len(prices)
    changes = [prices[i] - prices[i - 1] for i in range(1, len(prices))]

    def rsi_from(avg_gain: float, avg_loss: float) -> float:
        if avg_loss == 0:
            return 100.0
        if avg_gain == 0:
            return 0.0
        return 100 - 100 / (1 + avg_gain / avg_loss)

    series: list[float | None] = [None] * period
    avg_gain = sum(max(change, 0) for change in changes[:period]) / period
    avg_loss = sum(max(-change, 0) for change in changes[:period]) / period
    series.append(rsi_from(avg_gain, avg_loss))
    for change in changes[period:]:
        avg_gain = (avg_gain * (period - 1) + max(change, 0)) / period
        avg_loss = (avg_loss * (period - 1) + max(-change, 0)) / period
        series.append(rsi_from(avg_gain, avg_loss))
    return series


def analyze_vegas_channel(
    candles: list[dict],
    rsi_len: int = 6,
    oversold: int = 20,
    overbought: int = 80,
    vol_len: int = 144,
    vol_mult: float = 2.0,
) -> dict:
    """candles: oldest-to-newest list of {"close", "high", "low", "volume"}. Returns the latest
    tunnel/trend/RSI state plus the most recent bull/bear/take-profit signal, if any."""
    min_len = max(EMA_PERIODS) + 1
    if len(candles) < min_len:
        raise ValueError(f"At least {min_len} bars are required for the Vegas channel system")

    closes = [bar["close"] for bar in candles]
    highs = [bar["high"] for bar in candles]
    lows = [bar["low"] for bar in candles]
    volumes = [bar["volume"] for bar in candles]

    ema = {period: _padded_ema(closes, period) for period in EMA_PERIODS}
    rsi_values = _rsi_series(closes, rsi_len)
    vol_avg = _padded_ema(volumes, vol_len)

    n = len(candles)
    tunnel_top: list[float | None] = [None] * n
    tunnel_bottom: list[float | None] = [None] * n
    bull_trend: list[bool] = [False] * n
    bear_trend: list[bool] = [False] * n
    in_tunnel: list[bool] = [False] * n
    vol_spike: list[bool] = [False] * n

    for i in range(n):
        e144, e233, e377, e610, e987 = (ema[period][i] for period in EMA_PERIODS)
        if e144 is not None and e987 is not None:
            tunnel_top[i] = max(e144, e987)
            tunnel_bottom[i] = min(e144, e987)
            in_tunnel[i] = (
                tunnel_bottom[i] <= closes[i] <= tunnel_top[i]
                or tunnel_bottom[i] <= highs[i] <= tunnel_top[i]
                or tunnel_bottom[i] <= lows[i] <= tunnel_top[i]
            )
        if None not in (e144, e233, e377, e610, e987):
            bull_trend[i] = e144 > e233 > e377 > e610 > e987
            bear_trend[i] = e144 < e233 < e377 < e610 < e987
        if vol_avg[i] is not None:
            vol_spike[i] = volumes[i] > vol_avg[i] * vol_mult

    def crossover(series: list[float | None], i: int, level: float) -> bool:
        return i > 0 and series[i - 1] is not None and series[i] is not None and series[i - 1] < level <= series[i]

    def crossunder(series: list[float | None], i: int, level: float) -> bool:
        return i > 0 and series[i - 1] is not None and series[i] is not None and series[i - 1] > level >= series[i]

    signals: list[str | None] = [None] * n
    in_oversold = in_overbought = in_oversold_tp = in_overbought_tp = False
    bull_vol_confirmed = bear_vol_confirmed = tp_bull_vol_confirmed = tp_bear_vol_confirmed = False

    for i in range(n):
        rsi_value = rsi_values[i]
        prev_bull_vc, prev_bear_vc = bull_vol_confirmed, bear_vol_confirmed
        prev_tp_bull_vc, prev_tp_bear_vc = tp_bull_vol_confirmed, tp_bear_vol_confirmed

        if rsi_value is not None:
            if rsi_value < oversold:
                in_oversold = True
                in_oversold_tp = True
                if vol_spike[i]:
                    bull_vol_confirmed = True
                    tp_bear_vol_confirmed = True
            if rsi_value >= oversold and in_oversold:
                in_oversold, bull_vol_confirmed = False, False
            if rsi_value >= oversold and in_oversold_tp:
                in_oversold_tp, tp_bear_vol_confirmed = False, False

            if rsi_value > overbought:
                in_overbought = True
                in_overbought_tp = True
                if vol_spike[i]:
                    bear_vol_confirmed = True
                    tp_bull_vol_confirmed = True
            if rsi_value <= overbought and in_overbought:
                in_overbought, bear_vol_confirmed = False, False
            if rsi_value <= overbought and in_overbought_tp:
                in_overbought_tp, tp_bull_vol_confirmed = False, False

        if crossover(rsi_values, i, oversold) and bull_trend[i] and in_tunnel[i] and prev_bull_vc:
            signals[i] = "bull_entry"
        elif crossunder(rsi_values, i, overbought) and bear_trend[i] and in_tunnel[i] and prev_bear_vc:
            signals[i] = "bear_entry"
        elif crossunder(rsi_values, i, overbought) and prev_tp_bull_vc:
            signals[i] = "take_profit_bull"
        elif crossover(rsi_values, i, oversold) and prev_tp_bear_vc:
            signals[i] = "take_profit_bear"

    last_signal, bars_since_signal = None, None
    for offset, i in enumerate(range(n - 1, -1, -1)):
        if signals[i] is not None:
            last_signal, bars_since_signal = signals[i], offset
            break

    trend = "bullish_aligned" if bull_trend[-1] else "bearish_aligned" if bear_trend[-1] else "mixed"
    return {
        "trend": trend,
        "tunnel_top": round(tunnel_top[-1], 4) if tunnel_top[-1] is not None else None,
        "tunnel_bottom": round(tunnel_bottom[-1], 4) if tunnel_bottom[-1] is not None else None,
        "in_tunnel": in_tunnel[-1],
        "rsi": round(rsi_values[-1], 4) if rsi_values[-1] is not None else None,
        "volume_spike": vol_spike[-1],
        "last_signal": last_signal,
        "bars_since_signal": bars_since_signal,
    }


def trend_direction(trend: str) -> str:
    return {"bullish_aligned": "long", "bearish_aligned": "short"}.get(trend, "neutral")


def check_timeframe_alignment(higher_label: str, higher_trend: str, lower_label: str, lower_trend: str) -> dict:
    """Pass only when both timeframes agree on a non-neutral direction; otherwise say which side differs."""
    higher_dir, lower_dir = trend_direction(higher_trend), trend_direction(lower_trend)
    direction_labels = {"long": "多頭", "short": "空頭", "neutral": "訊號不一(mixed)"}
    passed = higher_dir == lower_dir and higher_dir != "neutral"
    if passed:
        note = f"{higher_label}與{lower_label}方向一致（{direction_labels[higher_dir]}）"
    else:
        note = f"{higher_label}為{direction_labels[higher_dir]}，{lower_label}為{direction_labels[lower_dir]}，方向不一致"
    return {"passed": passed, f"{higher_label}_direction": higher_dir, f"{lower_label}_direction": lower_dir, "note": note}
