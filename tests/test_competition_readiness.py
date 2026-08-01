"""T8：競賽最終 readiness gate。

所有測試只使用離線 fixture 或注入的失敗 client；絕不呼叫外部 API。
本檔驗證現場最重要的閉環：題型、降級、可追溯性、輸出完整性與展示入口。
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.day1_mvp import mock_evidence
from src.orchestrator import run, run_comparison
from src.schemas import ARTIFACT_FILENAMES, VERDICT_INSUFFICIENT_EVIDENCE


BTC_QUESTION = "分析 BTC 過去兩週市場表現，整合價格、鏈上、主要新聞與討論熱度，說明訊號一致程度。"
ETH_QUESTION = "市場認為 ETH 短期將維持盤整，請蒐集支持與反對證據。"
SOL_BNB_QUESTION = "比較 SOL 與 BNB 的流動性、市場關注度及風險敞口。"
XRP_QUESTION = "分析 XRP 當前市場狀態、主要風險與後續觀察條件。"
REQUIRED_EVIDENCE_FIELDS = {"evidence_id", "source", "source_url", "fetched_at", "content_reference"}


class _FailingClient:
    def __init__(self, error: Exception):
        self.error = error

    def generate_json(self, **_kwargs):
        raise self.error


def _read(path: Path, filename: str) -> dict | list:
    return json.loads((path / filename).read_text(encoding="utf-8"))


def _assert_bundle(test: unittest.TestCase, output: Path, result: dict) -> None:
    for filename in ARTIFACT_FILENAMES.values():
        test.assertTrue((output / filename).is_file(), filename)
    manifest = _read(output, "manifest.json")
    evidence = _read(output, "evidence.json")
    claims = _read(output, "claims.json")
    test.assertEqual(manifest["validation"]["citation_gate_status"], "PASS")
    test.assertEqual(result["citation_gate"]["status"], "PASS")
    test.assertTrue(evidence)
    for item in evidence:
        test.assertTrue(REQUIRED_EVIDENCE_FIELDS <= set(item), item.get("evidence_id"))
        test.assertTrue(all(item[field] for field in REQUIRED_EVIDENCE_FIELDS), item["evidence_id"])
    evidence_ids = {item["evidence_id"] for item in evidence}
    for claim in claims["claims"]:
        cited = set(claim["supporting_evidence_ids"]) | set(claim["contradicting_evidence_ids"])
        for fact in claim["facts"]:
            cited.update(fact["evidence_ids"])
        test.assertTrue(cited <= evidence_ids, cited - evidence_ids)
    for entry in manifest["files"]:
        actual = hashlib.sha256((output / entry["path"]).read_bytes()).hexdigest()
        test.assertEqual(actual, entry["sha256"], entry["path"])


class RequiredQuestionReadinessTests(unittest.TestCase):
    def test_single_coin_question_types_produce_traceable_offline_bundles(self):
        for coin, question in (("BTC", BTC_QUESTION), ("ETH", ETH_QUESTION), ("XRP", XRP_QUESTION)):
            with self.subTest(coin=coin), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                result = run(coin, question, output, live=False, use_llm=False)
                _assert_bundle(self, output, result)
                if coin == "ETH":
                    hypotheses = _read(output, "research_plan.json")["hypotheses"]
                    self.assertTrue(hypotheses)
                    self.assertTrue(all(item["support_questions"] and item["contradiction_questions"]
                                        for item in hypotheses))
                # 離線 fixture 不得被包裝成具有方向性的充分證據。
                self.assertTrue(all(claim["verdict"] == VERDICT_INSUFFICIENT_EVIDENCE
                                    for claim in result["claims"]))

    def test_sol_bnb_comparison_shares_window_and_keeps_leg_bundles(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            payload = run_comparison("SOL", "BNB", SOL_BNB_QUESTION, output,
                                     live=False, use_llm=False)
            manifest = _read(output, "manifest.json")
            self.assertEqual(manifest["shared_time_window"], payload["research_plan"]["time_window"])
            for coin in ("SOL", "BNB"):
                _assert_bundle(self, output / coin, payload["results"][coin])
                plan = _read(output / coin, "research_plan.json")
                self.assertEqual(plan["time_window"], manifest["shared_time_window"])
                profile = payload["profiles"][coin]
                self.assertTrue(profile["liquidity"]["evidence_id"])
                self.assertTrue(profile["attention"]["cited_evidence_ids"])
                self.assertTrue(profile["risk_exposure"]["cited_evidence_ids"])


class FailureInjectionReadinessTests(unittest.TestCase):
    def test_bedrock_timeout_and_invalid_json_fall_back_to_a_complete_report(self):
        for label, error in (("timeout", TimeoutError("simulated Bedrock timeout")),
                             ("invalid_json", ValueError("simulated invalid JSON"))):
            with self.subTest(label=label), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                result = run("ETH", ETH_QUESTION, output, live=False, use_llm=True,
                             analysis_client=_FailingClient(error))
                _assert_bundle(self, output, result)
                log = _read(output, "execution_log.json")
                step = next(item for item in log["steps"] if item["name"] == "llm_reasoning")
                self.assertEqual(step["status"], f"fallback:{type(error).__name__}")

    def test_planner_and_semantic_critic_failures_do_not_stop_the_bundle(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run("ETH", ETH_QUESTION, output, live=False, use_llm=True,
                         planner_client=_FailingClient(TimeoutError("planner timeout")),
                         analysis_client=_FailingClient(TimeoutError("analysis timeout")),
                         critic_client=_FailingClient(TimeoutError("critic timeout")))
            _assert_bundle(self, output, result)
            steps = {item["name"]: item for item in _read(output, "execution_log.json")["steps"]}
            self.assertEqual(steps["plan_research"]["status"], "fallback")
            self.assertEqual(steps["critic_review"]["status"], "fallback:TimeoutError")
            self.assertIn("語意稽核未執行", (output / "report.md").read_text(encoding="utf-8"))

    def test_news_onchain_social_collector_fallbacks_remain_publishable(self):
        def failed_collect(coin, **_kwargs):
            evidence = mock_evidence(coin)
            return evidence, [
                "news:fallback:TimeoutError",
                "whale:fallback:RuntimeError",
                "social:fallback:empty_result",
            ], []

        with tempfile.TemporaryDirectory() as directory, \
             patch("src.orchestrator.collect_evidence_detailed", side_effect=failed_collect):
            output = Path(directory)
            result = run("ETH", ETH_QUESTION, output, live=True, use_llm=False)
            _assert_bundle(self, output, result)
            self.assertIn("collector_fallback:news", result["degradation_reasons"])
            self.assertIn("collector_fallback:whale", result["degradation_reasons"])
            self.assertIn("collector_fallback:social", result["degradation_reasons"])

    def test_deadline_finalization_writes_artifacts_without_starting_models(self):
        with tempfile.TemporaryDirectory() as directory, \
             patch("src.orchestrator.analyze_with_llm") as analyze, \
             patch("src.orchestrator.critique_with_llm") as critic:
            output = Path(directory)
            result = run("ETH", ETH_QUESTION, output, live=False, use_llm=True,
                         time_budget_seconds=900, finalization_deadline_seconds=840,
                         deadline=time.monotonic() + 40)
            _assert_bundle(self, output, result)
            analyze.assert_not_called()
            critic.assert_not_called()
            self.assertEqual(result["deadline"]["reason"], "finalization_window_reached")


if __name__ == "__main__":
    unittest.main()
