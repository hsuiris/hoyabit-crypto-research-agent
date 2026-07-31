"""Day 8: walk-forward backtest engine and lede-level feed summaries."""

import unittest
from pathlib import Path

from src.backtest import WARMUP_BARS, compare_variants, run_backtest, signal_series
from src.day2_sources import _mentions_coin, _parse_feed_entries, _strip_html
from src.ohlcv import load_ohlcv


def _flat_candles(count: int, price: float = 100.0) -> list[dict]:
    return [{"close": price, "high": price, "low": price, "volume": 1000.0} for _ in range(count)]


class BacktestEngineTest(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.rows = load_ohlcv(Path("data/ETH.csv"), "ETH", days=None)
        cls.dates = [row["date"] for row in cls.rows]
        cls.signals = signal_series(cls.rows)

    def test_rejects_history_shorter_than_the_warmup(self):
        with self.assertRaises(ValueError):
            run_backtest(_flat_candles(WARMUP_BARS), ["d"] * WARMUP_BARS)

    def test_signals_never_fire_inside_the_warmup(self):
        self.assertTrue(all(signal is None for signal in self.signals[:WARMUP_BARS]))

    def test_signals_are_known_strategy_labels(self):
        fired = {signal for signal in self.signals if signal}
        self.assertTrue(fired <= {"bull_entry", "bear_entry", "take_profit_bull", "take_profit_bear"})

    def test_replay_is_free_of_look_ahead(self):
        """A signal at bar i must be reproducible from candles[:i+1] alone."""
        fired = [index for index, signal in enumerate(self.signals) if signal]
        self.assertTrue(fired, "expected at least one signal in five years of data")
        for index in fired[:3]:
            with self.subTest(bar=index):
                truncated = signal_series(self.rows[: index + 1])
                self.assertEqual(truncated[index], self.signals[index])

    def test_result_shape_and_internal_consistency(self):
        result = run_backtest(self.rows, self.dates, signals=self.signals)
        self.assertEqual(result["win_count"] + result["loss_count"], result["trade_count"])
        self.assertEqual(len(result["equity_curve"]), len(result["equity_dates"]))
        self.assertEqual(result["window"]["test_bars"], len(self.rows) - WARMUP_BARS)
        self.assertLessEqual(result["max_drawdown_pct"], 0)
        self.assertAlmostEqual(
            result["excess_return_pct"],
            round(result["strategy_return_pct"] - result["buy_hold_return_pct"], 2),
            places=2,
        )
        if result["trade_count"]:
            self.assertAlmostEqual(
                result["win_rate_pct"], round(result["win_count"] / result["trade_count"] * 100, 1), places=1
            )

    def test_every_trade_closes_and_is_chronological(self):
        result = run_backtest(self.rows, self.dates, signals=self.signals)
        for trade in result["trades"]:
            self.assertLess(trade["entry_date"], trade["exit_date"])
            self.assertGreater(trade["bars_held"], 0)
            self.assertIn(trade["side"], {"long", "short"})
            self.assertIn(trade["exit_reason"], {"take_profit", "reverse", "end_of_data"})

    def test_fees_reduce_returns(self):
        free = run_backtest(self.rows, self.dates, fee_pct=0.0, signals=self.signals)
        charged = run_backtest(self.rows, self.dates, fee_pct=0.5, signals=self.signals)
        if free["trade_count"]:
            self.assertLess(charged["strategy_return_pct"], free["strategy_return_pct"])

    def test_long_only_takes_no_short_trades(self):
        result = run_backtest(self.rows, self.dates, allow_short=False, signals=self.signals)
        self.assertTrue(all(trade["side"] == "long" for trade in result["trades"]))

    def test_compare_variants_returns_both_labelled_runs(self):
        variants = compare_variants(self.rows[:1200], self.dates[:1200])
        self.assertEqual(len(variants), 2)
        self.assertFalse(variants[0]["params"]["allow_short"])
        self.assertTrue(variants[1]["params"]["allow_short"])
        self.assertTrue(all(variant["label"] for variant in variants))

    def test_buy_hold_matches_the_test_window_not_the_full_file(self):
        result = run_backtest(self.rows, self.dates, signals=self.signals)
        closes = [row["close"] for row in self.rows][WARMUP_BARS:]
        expected = round((closes[-1] - closes[0]) / closes[0] * 100, 2)
        self.assertAlmostEqual(result["buy_hold_return_pct"], expected, places=2)


class FeedLedeTest(unittest.TestCase):
    def test_strip_html_unwraps_markup_and_entities(self):
        self.assertEqual(_strip_html("<p>Fed  holds &amp; waits</p>"), "Fed holds & waits")

    def test_strip_html_truncates_long_ledes_with_ellipsis(self):
        text = _strip_html("word " * 300, max_chars=100)
        self.assertLessEqual(len(text), 101)
        self.assertTrue(text.endswith("…"))

    def test_rss_entries_carry_the_description_as_summary(self):
        raw = b"""<?xml version="1.0"?><rss><channel>
        <item><title>ETH rallies</title><link>https://e.com/1</link>
        <pubDate>Thu, 30 Jul 2026</pubDate>
        <description>&lt;p&gt;Ether rose 4% after the Fed held rates.&lt;/p&gt;</description></item>
        </channel></rss>"""
        entry = _parse_feed_entries(raw)[0]
        self.assertEqual(entry["title"], "ETH rallies")
        self.assertEqual(entry["summary"], "Ether rose 4% after the Fed held rates.")

    def test_atom_entries_fall_back_to_summary_then_content(self):
        raw = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
        <entry><title>Board update</title><link href="https://e.com/2"/>
        <updated>2026-07-29</updated><summary>pc joins the board.</summary></entry></feed>"""
        entry = _parse_feed_entries(raw)[0]
        self.assertEqual(entry["url"], "https://e.com/2")
        self.assertEqual(entry["summary"], "pc joins the board.")

    def test_missing_description_yields_empty_summary_not_none(self):
        raw = b"""<?xml version="1.0"?><rss><channel>
        <item><title>No lede here</title><link>https://e.com/3</link></item></channel></rss>"""
        self.assertEqual(_parse_feed_entries(raw)[0]["summary"], "")

    def test_coin_matching_uses_names_as_well_as_tickers(self):
        self.assertTrue(_mentions_coin("Ethereum staking hits a record", "ETH"))
        self.assertTrue(_mentions_coin("Ripple settles with the SEC", "XRP"))
        self.assertFalse(_mentions_coin("Cardano upgrade ships", "ETH"))


if __name__ == "__main__":
    unittest.main()
