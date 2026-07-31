"""Day 9: three-phase budget, parallel collection agents, full-text crawl, critic review."""

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src import orchestrator
from src.day2_sources import (COLLECTION_AGENTS, FULLTEXT_DOMAINS, _SOURCE_LOADERS,
                              _extract_article_text, collect_evidence_detailed, enrich_with_fulltext)
from src.llm import CRITIC_SCHEMA, _parse_critic_output, build_critic_prompt
from src.orchestrator import (COLLECTION_PHASE_SECONDS, CRITIC_PHASE_SECONDS,
                              DEFAULT_TIME_BUDGET_SECONDS, REASONING_PHASE_SECONDS, run)


class PhaseBudgetTest(unittest.TestCase):
    def test_phases_sum_to_the_total_and_fit_the_competition_limit(self):
        self.assertEqual(
            DEFAULT_TIME_BUDGET_SECONDS,
            COLLECTION_PHASE_SECONDS + REASONING_PHASE_SECONDS + CRITIC_PHASE_SECONDS,
        )
        self.assertEqual((COLLECTION_PHASE_SECONDS, REASONING_PHASE_SECONDS, CRITIC_PHASE_SECONDS),
                         (420.0, 180.0, 120.0))
        self.assertLess(DEFAULT_TIME_BUDGET_SECONDS, 15 * 60, "must stay under the 15-minute ceiling")

    def test_execution_log_records_ceilings_and_actual_phase_times(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run("ETH", "近期風險？", output)
            budget = json.loads((output / "execution_log.json").read_text(encoding="utf-8"))["time_budget"]
            self.assertEqual(budget["phase_ceilings_seconds"],
                             {"collection": 420.0, "reasoning": 180.0, "critic": 120.0})
            self.assertEqual(set(budget["phase_actual_ms"]), {"collection_ms", "reasoning_ms", "critic_ms"})

    def test_critic_step_appears_in_the_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run("ETH", "近期風險？", output)
            steps = json.loads((output / "execution_log.json").read_text(encoding="utf-8"))["steps"]
            names = [step["name"] for step in steps]
            self.assertIn("critic_review", names)
            self.assertLess(names.index("llm_reasoning"), names.index("critic_review"))
            self.assertLess(names.index("critic_review"), names.index("generate_report"))

    def test_critic_is_skipped_without_llm_and_leaves_the_report_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run("ETH", "近期風險？", output, use_llm=False)
            self.assertIsNone(result["critique"])
            steps = json.loads((output / "execution_log.json").read_text(encoding="utf-8"))["steps"]
            critic = next(step for step in steps if step["name"] == "critic_review")
            self.assertEqual(critic["status"], "skipped:disabled")
            self.assertIn("## Conclusion", (output / "report.md").read_text(encoding="utf-8"))


class ParallelCollectionTest(unittest.TestCase):
    def test_every_source_belongs_to_exactly_one_agent(self):
        assigned = [label for _, _, labels in COLLECTION_AGENTS for label in labels]
        self.assertEqual(sorted(assigned), sorted(label for label, _ in _SOURCE_LOADERS))
        self.assertEqual(len(assigned), len(set(assigned)), "a source may not be collected twice")

    def test_offline_mode_reports_no_agents(self):
        evidence, log, agents = collect_evidence_detailed("ETH", live=False)
        self.assertEqual(log, ["mock_mode"])
        self.assertEqual(agents, [])
        self.assertTrue(evidence)

    def test_expired_deadline_skips_every_source_without_raising(self):
        evidence, log, agents = collect_evidence_detailed(
            "ETH", live=True, deadline=time.monotonic() - 1,
        )
        self.assertTrue(all("skipped:deadline" in entry for entry in log), log)
        self.assertTrue(all(item.reliability_score == 0.20 for item in evidence))
        self.assertEqual(len(agents), len(COLLECTION_AGENTS))
        self.assertTrue(all("duration_ms" in agent for agent in agents))

    def test_evidence_order_is_canonical_regardless_of_agent_finish_order(self):
        """Footnote numbering follows evidence order, so concurrency must not reorder it."""
        canonical = [label for label, _ in _SOURCE_LOADERS]
        evidence, _, _ = collect_evidence_detailed("ETH", live=True, deadline=time.monotonic() - 1)
        types = [item.data_type for item in evidence]
        self.assertEqual(types, [label for label in canonical if label in types])


class FullTextCrawlTest(unittest.TestCase):
    def test_non_whitelisted_domains_are_never_fetched(self):
        items = [{"url": "https://news.google.com/rss/articles/abc", "title": "x"}]
        with patch("src.day2_sources._get_bytes") as fetch:
            enrich_with_fulltext(items)
        fetch.assert_not_called()
        self.assertEqual(items[0]["fulltext_status"], "skipped:not_whitelisted")

    def test_robots_refusal_prevents_the_fetch(self):
        items = [{"url": "https://decrypt.co/article", "title": "x"}]
        with patch("src.day2_sources._robots_allows", return_value=False), \
             patch("src.day2_sources._get_bytes") as fetch:
            enrich_with_fulltext(items)
        fetch.assert_not_called()
        self.assertEqual(items[0]["fulltext_status"], "skipped:robots")

    def test_expired_deadline_stops_the_crawl(self):
        items = [{"url": "https://decrypt.co/article", "title": "x"}]
        with patch("src.day2_sources._get_bytes") as fetch:
            enrich_with_fulltext(items, deadline=time.monotonic() - 1)
        fetch.assert_not_called()
        self.assertEqual(items[0]["fulltext_status"], "skipped:deadline")

    def test_one_article_failing_does_not_affect_the_others(self):
        items = [{"url": "https://decrypt.co/a", "title": "a"}, {"url": "https://decrypt.co/b", "title": "b"}]
        html = b"<html><body><p>" + b"Ethereum rallied after the Federal Reserve held rates steady. " * 3 + b"</p></body></html>"
        with patch("src.day2_sources._robots_allows", return_value=True), \
             patch("src.day2_sources._get_bytes", side_effect=[TimeoutError("slow"), html]):
            enrich_with_fulltext(items)
        self.assertTrue(items[0]["fulltext_status"].startswith("failed:"))
        self.assertEqual(items[1]["fulltext_status"], "ok")
        self.assertIn("Ethereum rallied", items[1]["fulltext"])

    def test_extractor_drops_price_ticker_paragraphs(self):
        page = ("<p>0.76% TRX $0.3269 0.57% LINK $8.26 2.40% ZEC $458.71 3.43% ADA $0.1697 2.06% XRP $1.07</p>"
                "<p>" + "The Federal Reserve held its benchmark rate steady on Wednesday, and traders read "
                "the decision as broadly neutral for risk assets. " * 2 + "</p>")
        text = _extract_article_text(page)
        self.assertIn("Federal Reserve", text)
        self.assertNotIn("TRX", text)

    def test_whitelist_excludes_google_news(self):
        self.assertNotIn("news.google.com", FULLTEXT_DOMAINS)


class CriticTest(unittest.TestCase):
    def _critique(self, **overrides):
        base = {"verdict": "concerns", "summary": "檢查了引用與反證", "confidence_adjustment": -0.1,
                "findings": [{"severity": "medium", "category": "over_claim", "claim": "c",
                              "issue": "i", "evidence_id": "EV-001"}]}
        return json.dumps({**base, **overrides}, ensure_ascii=False)

    def test_parses_a_well_formed_critique(self):
        parsed = _parse_critic_output(self._critique())
        self.assertEqual(parsed["verdict"], "concerns")
        self.assertEqual(parsed["confidence_adjustment"], -0.1)

    def test_rejects_invalid_verdict(self):
        with self.assertRaises(ValueError):
            _parse_critic_output(self._critique(verdict="looks_good"))

    def test_rejects_positive_confidence_adjustment(self):
        """The critic may lower confidence, never raise it."""
        with self.assertRaises(ValueError):
            _parse_critic_output(self._critique(confidence_adjustment=0.2))

    def test_rejects_empty_output(self):
        with self.assertRaises(ValueError):
            _parse_critic_output("")

    def test_prompt_carries_the_analysis_and_the_evidence(self):
        result = {"reasoning": {"conclusion": "偏多結論", "confidence": 0.6, "facts": ["f"],
                                "inferences": ["i"], "counter_evidence": ["c"], "market_judgment": "j"},
                  "stance": {"stance": "bullish", "label": "偏多"}, "risk_factors": ["r"]}
        evidence = [{"evidence_id": "EV-001", "source": "CoinGecko", "data_type": "market",
                     "reliability_score": 0.9, "content": {"return_pct": 3.2}}]
        prompt = build_critic_prompt("ETH", "問題", result, evidence)
        self.assertIn("挑戰", prompt)
        self.assertIn("偏多結論", prompt)
        self.assertIn("EV-001", prompt)
        self.assertIn("ignored_counter", prompt)

    def test_schema_constrains_verdict_and_severity(self):
        self.assertEqual(CRITIC_SCHEMA["properties"]["verdict"]["enum"], ["pass", "concerns", "fail"])
        severity = CRITIC_SCHEMA["properties"]["findings"]["items"]["properties"]["severity"]
        self.assertEqual(severity["enum"], ["high", "medium", "low"])

    def test_critique_lowers_confidence_and_records_both_values(self):
        fake = {"verdict": "concerns", "summary": "s", "confidence_adjustment": -0.2, "findings": []}
        with patch.object(orchestrator, "analyze_with_llm", side_effect=RuntimeError("skip llm")), \
             patch.object(orchestrator, "critique_with_llm", return_value=dict(fake)), \
             tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run("ETH", "近期風險？", output, use_llm=True)
            critique = result["critique"]
            self.assertEqual(critique["verdict"], "concerns")
            self.assertAlmostEqual(
                critique["adjusted_confidence"], max(0.0, critique["original_confidence"] - 0.2), places=4
            )
            self.assertEqual(result["reasoning"]["confidence"], critique["adjusted_confidence"])
            self.assertIn("## Critic Review", (output / "report.md").read_text(encoding="utf-8"))

    def test_critic_failure_degrades_without_breaking_the_run(self):
        with patch.object(orchestrator, "analyze_with_llm", side_effect=RuntimeError("skip llm")), \
             patch.object(orchestrator, "critique_with_llm", side_effect=TimeoutError("slow")), \
             tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run("ETH", "近期風險？", output, use_llm=True)
            self.assertIsNone(result["critique"])
            steps = json.loads((output / "execution_log.json").read_text(encoding="utf-8"))["steps"]
            critic = next(step for step in steps if step["name"] == "critic_review")
            self.assertEqual(critic["status"], "fallback:TimeoutError")

    def test_critic_citing_unknown_evidence_is_rejected(self):
        bad = {"verdict": "fail", "summary": "s", "confidence_adjustment": -0.3,
               "findings": [{"severity": "high", "category": "unsupported", "claim": "c",
                             "issue": "i", "evidence_id": "EV-DOES-NOT-EXIST"}]}
        with patch.object(orchestrator, "analyze_with_llm", side_effect=RuntimeError("skip llm")), \
             patch.object(orchestrator, "critique_with_llm", return_value=bad), \
             tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run("ETH", "近期風險？", output, use_llm=True)
            self.assertIsNone(result["critique"])
            steps = json.loads((output / "execution_log.json").read_text(encoding="utf-8"))["steps"]
            critic = next(step for step in steps if step["name"] == "critic_review")
            self.assertEqual(critic["status"], "fallback:ValueError")


if __name__ == "__main__":
    unittest.main()
