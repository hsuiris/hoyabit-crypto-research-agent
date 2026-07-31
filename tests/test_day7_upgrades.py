"""Tests for the Day-7 upgrades: macro/announcement sources, the time-budget watchdog,
two-coin comparison, and signal-driven offline reasoning."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.comparison import attention_profile, build_profile, compare_profiles, liquidity_profile, risk_exposure_profile
from src.day1_mvp import mock_evidence
from src.day2_sources import collect_evidence, fetch_macro, fetch_official_announcements
from src.errors import AgentInputError
from src.orchestrator import _offline_reasoning, _signal_inventory, run, run_comparison


FNG_PAYLOAD = {"data": [
    {"value": str(30 - index), "value_classification": "Fear", "timestamp": str(1785214800 - index * 86400)}
    for index in range(14)
]}

RSS_FEED = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Ethereum Foundation Board Update</title><link>https://blog.ethereum.org/a</link><pubDate>Wed, 29 Jul 2026 00:00:00 GMT</pubDate></item>
<item><title>Protocol Update</title><link>https://blog.ethereum.org/b</link><pubDate>Tue, 28 Jul 2026 00:00:00 GMT</pubDate></item>
</channel></rss>"""

ATOM_FEED = b"""<?xml version="1.0"?><feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Bitcoin Optech Newsletter #415</title><link href="https://bitcoinops.org/en/newsletters/415/"/><updated>2026-07-29T00:00:00Z</updated></entry>
</feed>"""

FED_FEED = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Federal Reserve issues FOMC statement</title><link>https://federalreserve.gov/fomc</link><pubDate>Wed, 29 Jul 2026 18:00:00 GMT</pubDate></item>
</channel></rss>"""


class MacroSourceTest(unittest.TestCase):
    @patch("src.day2_sources._get_bytes", return_value=FED_FEED)
    @patch("src.day2_sources._get_json", return_value=FNG_PAYLOAD)
    def test_macro_combines_fear_greed_and_fed(self, _get_json, _get_bytes):
        evidence = fetch_macro("ETH")
        self.assertEqual(evidence.data_type, "macro")
        # data arrives newest-first and is flipped to chronological, so the newest value lands last
        self.assertEqual(evidence.content["fear_greed_value"], 30)
        self.assertEqual(len(evidence.content["fear_greed_history"]), 14)
        self.assertEqual(evidence.content["fed_status"], "available")
        self.assertEqual(evidence.content["latest_fomc_release"]["title"], "Federal Reserve issues FOMC statement")

    @patch("src.day2_sources._get_bytes", side_effect=TimeoutError("fed down"))
    @patch("src.day2_sources._get_json", return_value=FNG_PAYLOAD)
    def test_macro_degrades_when_fed_feed_is_down(self, _get_json, _get_bytes):
        evidence = fetch_macro("ETH")
        self.assertEqual(evidence.content["fear_greed_value"], 30)
        self.assertEqual(evidence.content["fed_status"], "unavailable:TimeoutError")
        self.assertIsNone(evidence.content["latest_fomc_release"])

    @patch("src.day2_sources._get_json", return_value={"data": []})
    def test_macro_raises_when_fear_greed_is_empty(self, _get_json):
        with self.assertRaises(ValueError):
            fetch_macro("ETH")


class AnnouncementSourceTest(unittest.TestCase):
    @patch("src.day2_sources._get_bytes", return_value=RSS_FEED)
    def test_first_party_rss_feed(self, _get_bytes):
        evidence = fetch_official_announcements("ETH")
        self.assertEqual(evidence.data_type, "announcement")
        self.assertTrue(evidence.content["first_party"])
        self.assertEqual(evidence.reliability_score, 0.85)
        self.assertEqual(evidence.content["items"][0]["url"], "https://blog.ethereum.org/a")

    @patch("src.day2_sources._get_bytes", return_value=ATOM_FEED)
    def test_atom_feed_is_parsed_like_rss(self, _get_bytes):
        evidence = fetch_official_announcements("BTC")
        self.assertEqual(evidence.content["items"][0]["title"], "Bitcoin Optech Newsletter #415")
        self.assertEqual(evidence.content["items"][0]["url"], "https://bitcoinops.org/en/newsletters/415/")

    @patch("src.day2_sources._get_bytes", return_value=RSS_FEED)
    def test_syndicated_feed_is_scored_lower_than_first_party(self, _get_bytes):
        evidence = fetch_official_announcements("XRP")
        self.assertFalse(evidence.content["first_party"])
        self.assertEqual(evidence.reliability_score, 0.55)


class WatchdogTest(unittest.TestCase):
    def test_expired_deadline_skips_every_remaining_source(self):
        evidence, log = collect_evidence("ETH", live=True, deadline=time.monotonic() - 1)
        self.assertTrue(all("skipped:deadline" in entry for entry in log))
        self.assertTrue(all(item.reliability_score == 0.20 for item in evidence))

    @patch("src.orchestrator.analyze_with_llm")
    def test_llm_is_skipped_when_budget_is_exhausted(self, mock_llm):
        with tempfile.TemporaryDirectory() as directory:
            run("ETH", "分析市場", Path(directory), live=False, use_llm=True,
                deadline=time.monotonic() - 1)
            log = json.loads((Path(directory) / "execution_log.json").read_text(encoding="utf-8"))
            step = next(entry for entry in log["steps"] if entry["name"] == "llm_reasoning")
            self.assertEqual(step["status"], "fallback:TimeBudgetExceeded")
        mock_llm.assert_not_called()

    @patch("src.orchestrator.analyze_with_llm", return_value={
        "market_judgment": "j", "confidence": 0.5, "facts": [], "inferences": [], "conclusion": "c",
        "counter_evidence": [], "observation_points": [], "cited_evidence_ids": [],
    })
    def test_llm_still_runs_with_budget_remaining(self, mock_llm):
        with tempfile.TemporaryDirectory() as directory:
            run("ETH", "分析市場", Path(directory), live=False, use_llm=True)
            log = json.loads((Path(directory) / "execution_log.json").read_text(encoding="utf-8"))
            step = next(entry for entry in log["steps"] if entry["name"] == "llm_reasoning")
            self.assertEqual(step["status"], "success")
        mock_llm.assert_called_once()


class OfflineReasoningTest(unittest.TestCase):
    def _reasoning(self, evidence):
        result = {"coin": "ETH", "evidence_ids": [item.evidence_id for item in evidence], "collection_log": []}
        return _offline_reasoning(result, evidence)

    def test_counter_evidence_cites_real_opposing_signals(self):
        evidence = mock_evidence("ETH")
        reasoning = self._reasoning(evidence)
        opposing = [line for line in reasoning["counter_evidence"] if line.startswith("反方訊號")]
        self.assertTrue(opposing, "expected at least one signal-derived counter-argument")
        # every counter-argument must name an evidence id that actually exists in this run
        for line in reasoning["counter_evidence"]:
            if line.startswith(("反方訊號", "未定訊號")):
                self.assertTrue(any(item.evidence_id in line for item in evidence), line)

    def test_counter_evidence_changes_with_the_data(self):
        bullish = mock_evidence("ETH")
        bearish = mock_evidence("ETH")
        for item in bearish:
            if item.data_type == "derivatives":
                item.content["bias"] = "short_crowded"
                item.content["funding_rate_pct"] = -0.09
            if item.data_type == "macro":
                item.content["fear_greed_value"] = 88
                item.content["risk_appetite_direction"] = "risk_appetite_deteriorating"
        self.assertNotEqual(
            self._reasoning(bullish)["counter_evidence"],
            self._reasoning(bearish)["counter_evidence"],
            "counter-evidence must be derived from the data, not templated",
        )

    def test_signal_inventory_separates_sides(self):
        signals = _signal_inventory({"coin": "ETH"}, mock_evidence("ETH"))
        self.assertTrue({item["side"] for item in signals} <= {"bull", "bear", "neutral"})
        self.assertTrue(any(item["side"] == "bear" for item in signals))

    def test_degraded_sources_appear_in_risk_factors_and_counter_evidence(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run("ETH", "分析市場", Path(directory), live=False)
            result["collection_log"] = ["market:fallback:TimeoutError", "social:skipped:deadline"]
            reasoning = _offline_reasoning(result, mock_evidence("ETH"))
            joined = " ".join(reasoning["counter_evidence"])
            self.assertIn("market", joined)
            self.assertIn("social", joined)


class ComparisonTest(unittest.TestCase):
    def _evidence(self, coin):
        return json.loads(json.dumps([item.__dict__ for item in mock_evidence(coin)]))

    def test_liquidity_uses_volume_and_reports_trend(self):
        profile = liquidity_profile(self._evidence("BTC"))
        self.assertIsNotNone(profile["avg_daily_volume_usd"])
        self.assertIsNotNone(profile["volume_trend_pct"])

    def test_missing_market_data_yields_none_not_zero(self):
        profile = liquidity_profile([])
        self.assertIsNone(profile["avg_daily_volume_usd"])
        self.assertIsNone(profile["volume_trend_pct"])

    def test_risk_components_are_dropped_when_source_is_a_fallback(self):
        evidence = self._evidence("ETH")
        for item in evidence:
            if item["data_type"] == "derivatives":
                item["content_reference"] = {"fallback": True, "error_type": "TimeoutError"}
        profile = risk_exposure_profile(evidence)
        self.assertNotIn("funding_stress", profile["components"])

    def test_risk_score_is_none_when_nothing_is_available(self):
        profile = risk_exposure_profile([])
        self.assertIsNone(profile["composite_score"])
        self.assertEqual(profile["components_available"], 0)

    def test_attention_counts_all_three_channels(self):
        profile = attention_profile(self._evidence("ETH"))
        self.assertEqual(profile["news_article_count"], 2)
        self.assertEqual(profile["official_announcement_count"], 1)
        self.assertEqual(profile["social_post_count"], 2)
        self.assertGreater(profile["social_engagement_total"], 0)

    def test_attention_ranks_on_engagement_not_capped_counts(self):
        # Regression test for a real live-run finding: news/announcement/post counts all saturate at
        # their fetch limits, so two busy coins tie on every counter. Ranking must use engagement.
        quiet, loud = self._evidence("ETH"), self._evidence("SOL")
        for evidence, engagement in ((quiet, 1), (loud, 500)):
            for item in evidence:
                if item["data_type"] == "social":
                    item["content"]["posts"] = [{"title": "t", "score": engagement, "comments": 0}] * 25
                if item["data_type"] == "news":
                    item["content"]["items"] = [{"title": "t", "url": "u"}] * 5
        profile_quiet, profile_loud = build_profile("ETH", quiet), build_profile("SOL", loud)
        self.assertEqual(profile_quiet["attention"]["news_article_count"],
                         profile_loud["attention"]["news_article_count"])
        comparison = compare_profiles(profile_quiet, profile_loud)
        self.assertEqual(comparison["attention"]["leader"], "SOL")
        self.assertIn("上限", comparison["attention"]["note"])

    def test_compare_picks_the_higher_volume_coin(self):
        comparison = compare_profiles(build_profile("BTC", self._evidence("BTC")),
                                      build_profile("XRP", self._evidence("XRP")))
        self.assertEqual(comparison["liquidity"]["leader"], "BTC")
        self.assertIn("BTC", comparison["summary"])

    def test_compare_tolerates_a_missing_side(self):
        comparison = compare_profiles(build_profile("BTC", self._evidence("BTC")),
                                      build_profile("XRP", []))
        self.assertEqual(comparison["liquidity"]["leader"], "BTC")
        self.assertIn("資料不可用", comparison["liquidity"]["verdict"])

    def test_run_comparison_writes_both_legs_and_a_comparison(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            payload = run_comparison("BTC", "XRP", "比較流動性與風險", output, live=False)
            self.assertEqual(payload["coins"], ["BTC", "XRP"])
            self.assertTrue((output / "BTC" / "report.md").exists())
            self.assertTrue((output / "XRP" / "report.md").exists())
            self.assertTrue((output / "comparison.md").exists())
            text = (output / "comparison.md").read_text(encoding="utf-8")
            self.assertIn("BTC vs XRP", text)
            self.assertIn("not investment advice", text)

    def test_run_comparison_rejects_identical_coins(self):
        with tempfile.TemporaryDirectory() as directory:
            with self.assertRaisesRegex(AgentInputError, "不同的幣種"):
                run_comparison("ETH", "ETH", "比較", Path(directory))

    def test_run_comparison_shares_one_deadline_across_both_legs(self):
        with tempfile.TemporaryDirectory() as directory:
            payload = run_comparison("BTC", "XRP", "比較", Path(directory), live=False, time_budget_seconds=120)
            for coin in payload["coins"]:
                log = json.loads((Path(directory) / coin / "execution_log.json").read_text(encoding="utf-8"))
                self.assertLessEqual(log["time_budget"]["llm_seconds_remaining_at_decision"], 120)


if __name__ == "__main__":
    unittest.main()
