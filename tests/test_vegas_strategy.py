import unittest

from src.vegas_strategy import analyze_vegas_channel, check_timeframe_alignment


def _make_candles(closes, spike_index=None):
    candles = []
    for i, close in enumerate(closes):
        volume = 5000.0 if i == spike_index else 1000.0
        candles.append({"close": close, "high": close * 1.001, "low": close * 0.999, "volume": volume})
    return candles


def _uptrend(bars, growth=1.001, start=100.0):
    closes, price = [], start
    for _ in range(bars):
        price *= growth
        closes.append(price)
    return closes


class VegasStrategyTest(unittest.TestCase):
    def test_raises_when_history_too_short(self):
        with self.assertRaises(ValueError):
            analyze_vegas_channel(_make_candles(_uptrend(500)))

    def test_clean_uptrend_is_bullish_aligned_with_no_spurious_signal(self):
        candles = _make_candles(_uptrend(1000))
        result = analyze_vegas_channel(candles)
        self.assertEqual(result["trend"], "bullish_aligned")
        self.assertIsNone(result["last_signal"])

    def test_oversold_dip_with_volume_spike_triggers_bull_entry(self):
        closes = _uptrend(990)
        last = closes[-1]
        dip_and_recovery = [last * 0.97, last * 0.94, last * 0.90, last * 0.87, last * 0.85,
                             last * 0.86, last * 0.90, last * 0.95, last * 0.99, last * 1.02]
        closes.extend(dip_and_recovery)
        spike_index = len(closes) - 5  # deepest bar of the dip
        result = analyze_vegas_channel(_make_candles(closes, spike_index=spike_index))
        self.assertEqual(result["last_signal"], "bull_entry")
        self.assertIsNotNone(result["bars_since_signal"])

    def test_overbought_spike_with_volume_confirms_take_profit_bull(self):
        # A prior bull run followed by an overbought spike (with volume) and pullback should
        # raise a take-profit-bull exit signal, independent of trend/tunnel containment.
        closes = _uptrend(990)
        last = closes[-1]
        spike_and_pullback = [last * 1.03, last * 1.06, last * 1.10, last * 1.13, last * 1.15,
                               last * 1.14, last * 1.10, last * 1.05, last * 1.01, last * 0.98]
        closes.extend(spike_and_pullback)
        spike_index = len(closes) - 5
        result = analyze_vegas_channel(_make_candles(closes, spike_index=spike_index))
        self.assertEqual(result["last_signal"], "take_profit_bull")


class TimeframeAlignmentTest(unittest.TestCase):
    def test_passes_when_both_timeframes_agree(self):
        result = check_timeframe_alignment("4h", "bullish_aligned", "1h", "bullish_aligned")
        self.assertTrue(result["passed"])

    def test_fails_and_explains_which_side_disagrees(self):
        result = check_timeframe_alignment("4h", "bullish_aligned", "1h", "bearish_aligned")
        self.assertFalse(result["passed"])
        self.assertIn("4h", result["note"])
        self.assertIn("1h", result["note"])

    def test_fails_when_either_side_is_mixed(self):
        result = check_timeframe_alignment("4h", "bullish_aligned", "1h", "mixed")
        self.assertFalse(result["passed"])


if __name__ == "__main__":
    unittest.main()
