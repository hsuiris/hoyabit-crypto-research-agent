"""Day 8: discrete stance label, long-horizon price context, and chain TVL evidence."""

import json
import tempfile
import unittest
from pathlib import Path

from src.day2_sources import DEFILLAMA_CHAINS, _fallback_evidence, fetch_defillama_tvl
from src.ohlcv import downsample, load_ohlcv, price_windows
from src.orchestrator import _market_stance, _signal_inventory, run


def _signals(*sides_and_weights):
    return [
        {"side": side, "text": f"{side} signal {index}", "evidence_id": f"EV-{index}", "weight": weight}
        for index, (side, weight) in enumerate(sides_and_weights, 1)
    ]


class MarketStanceTest(unittest.TestCase):
    def test_clear_bull_majority_is_bullish_with_traceable_drivers(self):
        stance = _market_stance(_signals(("bull", 1.2), ("bull", 1.0), ("bear", 0.4)))
        self.assertEqual(stance["stance"], "bullish")
        self.assertEqual(stance["label_en"], "Bullish")
        self.assertEqual(stance["bull_weight"], 2.2)
        self.assertEqual(stance["bear_weight"], 0.4)
        # Drivers are ordered by weight so the headline reason is the strongest signal.
        self.assertEqual([driver["weight"] for driver in stance["drivers"]], [1.2, 1.0])
        self.assertTrue(all(driver["evidence_id"] for driver in stance["drivers"]))

    def test_clear_bear_majority_is_bearish(self):
        stance = _market_stance(_signals(("bear", 1.2), ("bear", 1.0), ("bull", 0.4)))
        self.assertEqual(stance["stance"], "bearish")
        self.assertEqual(stance["label_en"], "Bearish")

    def test_near_tie_stays_neutral_even_with_plenty_of_weight(self):
        stance = _market_stance(_signals(("bull", 1.2), ("bear", 1.0)))
        self.assertEqual(stance["stance"], "neutral")
        self.assertEqual(stance["drivers"], [])
        self.assertIn("門檻", stance["basis"])

    def test_thin_evidence_stays_neutral_even_when_one_sided(self):
        stance = _market_stance(_signals(("bull", 1.0)))
        self.assertEqual(stance["stance"], "neutral")
        self.assertIn("不足以形成方向判讀", stance["basis"])

    def test_no_signals_is_neutral_and_never_raises(self):
        stance = _market_stance([])
        self.assertEqual(stance["stance"], "neutral")
        self.assertEqual((stance["bull_weight"], stance["bear_weight"]), (0, 0))

    def test_run_exposes_stance_and_report_leads_with_it(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run("ETH", "近期風險？", output)
            stance = result["stance"]
            self.assertIn(stance["stance"], {"bullish", "neutral", "bearish"})
            report = (output / "report.md").read_text(encoding="utf-8")
            self.assertLess(report.index("## Stance"), report.index("## Market Judgment"))
            self.assertIn(stance["label"], report)

    def test_stance_is_deterministic_for_the_same_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            first = run("ETH", "近期風險？", output)["stance"]
            second = run("ETH", "近期風險？", output)["stance"]
            self.assertEqual(first, second)


class LongHorizonContextTest(unittest.TestCase):
    csv_path = Path("data/ETH.csv")

    def test_full_history_loads_when_days_is_none(self):
        rows = load_ohlcv(self.csv_path, "ETH", days=None)
        self.assertGreater(len(rows), 1000)
        self.assertLess(rows[0]["date"], rows[-1]["date"])

    def test_default_still_returns_the_short_window(self):
        self.assertEqual(len(load_ohlcv(self.csv_path, "ETH")), 14)

    def test_windows_cover_every_lookback_and_stay_self_consistent(self):
        windows = price_windows(load_ohlcv(self.csv_path, "ETH", days=None))
        self.assertEqual(set(windows), {"14d", "90d", "365d", "full"})
        for label, window in windows.items():
            with self.subTest(window=label):
                self.assertLessEqual(window["low"], window["end_price"])
                self.assertGreaterEqual(window["high"], window["end_price"])
                self.assertLessEqual(window["max_drawdown_pct"], 0)
                self.assertGreater(window["volatility_annualised_pct"], 0)
                self.assertTrue(0 <= window["percentile_in_range"] <= 100)
        self.assertEqual(windows["14d"]["days"], 14)
        self.assertGreater(windows["full"]["days"], windows["365d"]["days"])

    def test_return_matches_first_and_last_close(self):
        rows = load_ohlcv(self.csv_path, "ETH", days=90)
        window = price_windows(rows)["90d"]
        expected = (rows[-1]["close"] - rows[0]["close"]) / rows[0]["close"] * 100
        self.assertAlmostEqual(window["return_pct"], round(expected, 2), places=2)

    def test_downsample_keeps_endpoints_and_caps_points(self):
        rows = load_ohlcv(self.csv_path, "ETH", days=None)
        dates, closes = downsample(rows, points=260)
        self.assertEqual(len(dates), 260)
        self.assertEqual(len(closes), 260)
        self.assertEqual(dates[0], rows[0]["date"])
        self.assertEqual(dates[-1], rows[-1]["date"])

    def test_history_evidence_is_additive_not_a_replacement(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run("ETH", "近期風險？", output, history_path=self.csv_path)
            evidence = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
            by_type = [item["data_type"] for item in evidence]
            self.assertIn("price_history", by_type)
            self.assertIn("market", by_type)  # the 14-day live/fixture series survives
            history = next(item for item in evidence if item["data_type"] == "price_history")
            self.assertEqual(set(history["content"]["windows"]), {"14d", "90d", "365d", "full"})
            # T3: the adapter's hand-written 0.95 is now only a hint. The printed score comes from
            # the credibility engine, and the hint stays in the breakdown for audit.
            self.assertEqual(history["source_type"], "local_csv")
            self.assertEqual(history["score_breakdown"]["legacy_reliability_hint"], 0.95)
            self.assertEqual(history["reliability_score"], history["score_breakdown"]["final_score"])
            self.assertEqual(history["score_limiters"], [])

    def test_report_includes_long_horizon_section_only_with_history(self):
        with tempfile.TemporaryDirectory() as directory:
            with_history, without = Path(directory) / "a", Path(directory) / "b"
            run("ETH", "近期風險？", with_history, history_path=self.csv_path)
            run("ETH", "近期風險？", without)
            self.assertIn("## Long-horizon Context", (with_history / "report.md").read_text(encoding="utf-8"))
            self.assertNotIn("## Long-horizon Context", (without / "report.md").read_text(encoding="utf-8"))


class ChainTvlTest(unittest.TestCase):
    def test_every_supported_coin_has_a_chain_mapping(self):
        self.assertEqual(set(DEFILLAMA_CHAINS), {"BTC", "ETH", "SOL", "BNB", "XRP"})

    def test_unsupported_coin_raises_not_implemented(self):
        with self.assertRaises(NotImplementedError):
            fetch_defillama_tvl("DOGE")

    def test_tvl_fallback_is_degraded_and_clearly_marked(self):
        fallback = _fallback_evidence("tvl", "ETH", ConnectionError("boom"))
        self.assertEqual(fallback.reliability_score, 0.20)
        self.assertEqual(fallback.content["status"], "unavailable")
        self.assertEqual(fallback.content["direction"], "unknown")
        self.assertTrue(fallback.evidence_id.endswith("FALLBACK"))

    def test_tvl_signal_enters_the_inventory_with_a_side(self):
        from src.day1_mvp import Evidence

        evidence = [Evidence(
            "EV-TVL-ETH-001", "DefiLlama", "https://api.llama.fi/x", "2026-07-31T00:00:00+00:00",
            "tvl", "ETH", "90d",
            {"chain": "Ethereum", "tvl_usd": 4.0e10, "change_pct": -10.0, "change_30d_pct": 12.7,
             "direction": "expanding", "history": []}, 0.75,
        )]
        signals = _signal_inventory({"coin": "ETH"}, evidence)
        tvl_signals = [item for item in signals if item["evidence_id"] == "EV-TVL-ETH-001"]
        self.assertEqual(len(tvl_signals), 1)
        self.assertEqual(tvl_signals[0]["side"], "bull")
        self.assertIn("12.7", tvl_signals[0]["text"])


if __name__ == "__main__":
    unittest.main()
