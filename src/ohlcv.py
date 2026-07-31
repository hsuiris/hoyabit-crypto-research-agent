"""Loader for the competition-provided daily OHLCV CSV package."""

from __future__ import annotations

import csv
from pathlib import Path


REQUIRED_COLUMNS = {"date", "open", "high", "low", "close", "volume"}

# Windows the report contextualises the current move against. The 14-day view is what every other
# live source already covers, so anything longer is exactly what the CSV adds that free APIs cannot.
LOOKBACK_WINDOWS = (("14d", 14), ("90d", 90), ("365d", 365), ("full", None))


def load_ohlcv(path: Path, coin: str, days: int | None = 14) -> list[dict]:
    """Read the daily CSV, oldest first. `days=None` returns the entire history."""
    if not path.exists():
        raise FileNotFoundError(path)
    with path.open(newline="", encoding="utf-8-sig") as stream:
        rows = list(csv.DictReader(stream))
    if not rows or not REQUIRED_COLUMNS.issubset(rows[0]):
        raise ValueError(f"OHLCV CSV must contain {sorted(REQUIRED_COLUMNS)}")
    rows = sorted(rows, key=lambda row: row["date"])
    if days is not None:
        rows = rows[-days:]
    for row in rows:
        for column in REQUIRED_COLUMNS - {"date"}:
            row[column] = float(row[column])
        row["coin"] = coin.upper()
    if len(rows) < 2:
        raise ValueError("OHLCV CSV needs at least two rows")
    return rows


def _max_drawdown_pct(closes: list[float]) -> float:
    """Worst peak-to-trough decline inside the window, as a negative percentage."""
    peak, worst = closes[0], 0.0
    for close in closes:
        peak = max(peak, close)
        if peak:
            worst = min(worst, (close - peak) / peak)
    return round(worst * 100, 2)


def _annualised_volatility_pct(closes: list[float]) -> float | None:
    """Stdev of daily returns scaled to a year (365 days -- crypto trades without weekends)."""
    returns = [(later - earlier) / earlier for earlier, later in zip(closes, closes[1:]) if earlier]
    if len(returns) < 2:
        return None
    mean = sum(returns) / len(returns)
    variance = sum((value - mean) ** 2 for value in returns) / (len(returns) - 1)
    return round((variance ** 0.5) * (365 ** 0.5) * 100, 2)


def price_windows(rows: list[dict]) -> dict:
    """Return / volatility / drawdown per lookback window, plus where price sits in that range.

    `percentile_in_range` is the cheap version of "is this expensive?": where the latest close falls
    between the window's own low and high -- context a 14-day series structurally cannot provide.
    """
    windows = {}
    for label, size in LOOKBACK_WINDOWS:
        window = rows if size is None else rows[-size:]
        if len(window) < 2:
            continue
        closes = [row["close"] for row in window]
        low, high = min(closes), max(closes)
        windows[label] = {
            "days": len(window),
            "date_start": window[0]["date"],
            "date_end": window[-1]["date"],
            "start_price": round(closes[0], 4),
            "end_price": round(closes[-1], 4),
            "return_pct": round((closes[-1] - closes[0]) / closes[0] * 100, 2) if closes[0] else None,
            "volatility_annualised_pct": _annualised_volatility_pct(closes),
            "max_drawdown_pct": _max_drawdown_pct(closes),
            "low": round(low, 4),
            "high": round(high, 4),
            "percentile_in_range": round((closes[-1] - low) / (high - low) * 100, 1) if high > low else None,
        }
    return windows


def downsample(rows: list[dict], points: int = 260) -> tuple[list[str], list[float]]:
    """Evenly thin a long series for charting; 1826 daily points is more than a sparkline can show."""
    if len(rows) <= points:
        selected = rows
    else:
        step = (len(rows) - 1) / (points - 1)
        selected = [rows[round(index * step)] for index in range(points)]
    return [row["date"] for row in selected], [round(row["close"], 4) for row in selected]
