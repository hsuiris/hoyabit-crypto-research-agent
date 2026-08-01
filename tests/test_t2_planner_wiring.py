"""T2：Planner 接進 Orchestrator 的接線測試。

`tests/test_planner.py` 驗證 planner 模組本身（A-E 五題、schema、deterministic fallback）。
本檔只驗證**接線**：

- `research_plan.json` 實際落地，內容與 `result["research_plan"]` 一致。
- Execution Log 的 `plan_research` step 記錄 provider／model／duration／fallback／時間窗假設。
- Planner 失敗或輸出不合法時，整趟 run 仍完成並產出既有三個檔案。
- 比較執行只規劃一次，兩腳共用同一份 plan（同一個 time window 與 comparison dimensions）。
- 接線沒有改變既有行為：離線預設不呼叫任何模型，證據順序與腳註來源不受影響。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src import orchestrator
from src.day2_sources import collect_evidence_detailed
from src.llm import llm_runtime_info
from src.orchestrator import run, run_comparison
from src.schemas import TASK_MODES, TIME_WINDOW_SOURCES

_PLAN_KEYS = {
    "coins", "task_modes", "primary_question", "time_window", "hypotheses",
    "required_domains", "comparison_dimensions", "assumptions", "stop_conditions",
}


class _RecordingClient:
    """回傳固定 payload 並記錄呼叫的 LLMClient；不觸碰網路。"""

    def __init__(self, response: dict) -> None:
        self.response = response
        self.calls: list[dict] = []

    def generate_json(self, *, prompt: str, schema: dict, schema_name: str, timeout_seconds: float) -> dict:
        self.calls.append({"prompt": prompt, "schema_name": schema_name, "timeout_seconds": timeout_seconds})
        return self.response


def _valid_plan_payload(coins: list[str], question: str) -> dict:
    return {
        "coins": coins,
        "task_modes": ["describe_market_state", "identify_risks"],
        "primary_question": question,
        "time_window": {"days": 14, "source": "default"},
        "hypotheses": [],
        "required_domains": ["market", "derivatives"],
        "comparison_dimensions": [],
        "assumptions": ["未指定時間範圍，沿用預設 14 天"],
        "stop_conditions": {"max_evidence": 36, "max_followup_rounds": 1},
    }


def _read(output: Path, name: str):
    return json.loads((output / name).read_text(encoding="utf-8"))


def _planner_step(output: Path) -> dict:
    steps = _read(output, "execution_log.json")["steps"]
    return next(step for step in steps if step["name"] == "plan_research")


class ResearchPlanArtifactTests(unittest.TestCase):
    def test_offline_run_writes_a_schema_shaped_research_plan(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run("ETH", "分析 ETH 當前市場狀況。", output)

            plan = _read(output, "research_plan.json")
            self.assertEqual(set(plan), _PLAN_KEYS)
            self.assertEqual(plan["coins"], ["ETH"])
            self.assertEqual(plan["primary_question"], "分析 ETH 當前市場狀況。")
            self.assertIn(plan["time_window"]["source"], TIME_WINDOW_SOURCES)
            for mode in plan["task_modes"]:
                self.assertIn(mode, TASK_MODES)
            self.assertEqual(result["research_plan"], plan, "result 與落地檔案必須是同一份 plan")

    def test_every_required_question_type_produces_a_legal_plan(self):
        """T2 的 A-D 四類題目經由 run() 都要產出合法 plan（E 由 fallback 測試覆蓋）。"""
        cases = [
            ("BTC", "分析 BTC 過去兩週市場表現，整合價格、鏈上、新聞與社群。",
             ("describe_market_state", "assess_consistency"), "explicit"),
            ("ETH", "市場認為 ETH 短期將維持盤整，請找支持與反對證據。",
             ("test_hypothesis",), None),
            ("SOL", "比較 SOL 與 BNB 在當前宏觀環境下的市場位置與風險。",
             ("compare_assets",), None),
            ("XRP", "分析 XRP 當前市場狀況。", ("describe_market_state",), "default"),
        ]
        for coin, question, expected_modes, window_source in cases:
            with self.subTest(coin=coin), tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                run(coin, question, output)
                plan = _read(output, "research_plan.json")

                self.assertEqual(set(plan), _PLAN_KEYS)
                for mode in expected_modes:
                    self.assertIn(mode, plan["task_modes"])
                if window_source:
                    self.assertEqual(plan["time_window"]["source"], window_source)
                if window_source == "default":
                    self.assertTrue(plan["assumptions"], "預設時間窗必須在 assumptions 明示")

    def test_hypothesis_question_keeps_support_and_contradiction_in_the_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run("ETH", "市場認為 ETH 短期將維持盤整，請找支持與反對證據。", output)
            hypotheses = _read(output, "research_plan.json")["hypotheses"]

            self.assertTrue(hypotheses)
            for hypothesis in hypotheses:
                self.assertTrue(hypothesis["support_questions"])
                self.assertTrue(hypothesis["contradiction_questions"])
                self.assertTrue(hypothesis["falsification_conditions"])


class ExecutionLogTests(unittest.TestCase):
    def test_plan_research_step_runs_before_collection(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run("ETH", "近期風險？", output)
            names = [step["name"] for step in _read(output, "execution_log.json")["steps"]]

            self.assertIn("plan_research", names)
            self.assertLess(names.index("parse_input"), names.index("plan_research"))
            self.assertLess(names.index("plan_research"), names.index("collect_evidence"))

    def test_offline_planning_is_logged_as_deterministic_not_as_a_provider(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run("ETH", "分析 ETH 當前市場狀況。", output, use_llm=False)
            step = _planner_step(output)

            self.assertEqual(step["status"], "fallback")
            self.assertEqual(step["path"], "fallback")
            self.assertTrue(step["fallback_used"])
            self.assertEqual(step["provider"], "deterministic")
            self.assertIsNone(step["model"])
            self.assertEqual(step["fallback_reason"], "no_client_injected")

    def test_step_records_duration_and_time_window_assumptions(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run("XRP", "分析 XRP 當前市場狀況。", output)
            step = _planner_step(output)

            for key in ("duration_ms", "time_window", "time_window_assumptions", "task_modes",
                        "required_domains", "hypothesis_count", "fallback_used", "fallback_reason"):
                self.assertIn(key, step)
            self.assertIsInstance(step["duration_ms"], float)
            self.assertEqual(step["time_window"]["source"], "default")
            self.assertTrue(step["time_window_assumptions"])

    @patch.dict(os.environ, {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "test-key"}, clear=False)
    def test_model_backed_plan_records_provider_and_model(self):
        question = "分析 ETH 當前市場狀況。"
        client = _RecordingClient(_valid_plan_payload(["ETH"], question))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            # use_llm=False 讓分析與稽核維持離線，只有 planner 使用注入的 client，因此不需要網路。
            run("ETH", question, output, use_llm=False, planner_client=client)
            step = _planner_step(output)

            self.assertEqual(step["status"], "success")
            self.assertEqual(step["path"], "llm")
            self.assertFalse(step["fallback_used"])
            self.assertEqual(step["provider"], llm_runtime_info()["provider"])
            self.assertEqual(step["model"], llm_runtime_info()["model"])
            self.assertEqual(len(client.calls), 1)
            self.assertEqual(client.calls[0]["schema_name"], "research_plan")

    def test_phase_timing_keys_are_unchanged_by_planning(self):
        """Planner 不新增 phase ceiling，也不改動既有的三階段計時鍵。"""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run("ETH", "近期風險？", output)
            budget = _read(output, "execution_log.json")["time_budget"]

            self.assertEqual(set(budget["phase_actual_ms"]), {"collection_ms", "reasoning_ms", "critic_ms"})
            self.assertEqual(budget["phase_ceilings_seconds"],
                             {"collection": 420.0, "reasoning": 180.0, "critic": 120.0})


class PlannerDegradationTests(unittest.TestCase):
    def test_invalid_plan_output_does_not_stop_the_run(self):
        client = _RecordingClient({"unexpected": "shape"})
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run("ETH", "分析 ETH 當前市場狀況。", output, planner_client=client)
            step = _planner_step(output)

            self.assertEqual(step["path"], "fallback")
            self.assertIsNotNone(step["fallback_reason"])
            for name in ("report.md", "evidence.json", "execution_log.json", "research_plan.json"):
                self.assertTrue((output / name).exists(), name)
            self.assertIn("describe_market_state", result["research_plan"]["task_modes"])

    def test_planner_exception_does_not_stop_the_run(self):
        class Raising:
            def generate_json(self, **_kwargs):
                raise TimeoutError("simulated planner timeout")

        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run("ETH", "分析 ETH 當前市場狀況。", output, planner_client=Raising())
            step = _planner_step(output)

            self.assertEqual(step["status"], "fallback")
            self.assertIn("simulated planner timeout", step["fallback_reason"])
            self.assertIn("## Conclusion", (output / "report.md").read_text(encoding="utf-8"))

    def test_expired_budget_skips_the_model_call_entirely(self):
        client = _RecordingClient(_valid_plan_payload(["ETH"], "分析 ETH 當前市場狀況。"))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run("ETH", "分析 ETH 當前市場狀況。", output, planner_client=client,
                deadline=time.monotonic() + 1.0)
            step = _planner_step(output)

            self.assertEqual(client.calls, [], "剩餘預算不足時不得啟動模型呼叫")
            self.assertEqual(step["path"], "fallback")
            self.assertEqual(step["fallback_reason"], "no_client_injected")

    def test_offline_default_run_never_calls_a_model_for_planning(self):
        with patch.object(orchestrator, "default_llm_client",
                          side_effect=AssertionError("offline run must not build an LLM client")):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                run("ETH", "分析 ETH 當前市場狀況。", output, live=False, use_llm=False)
                self.assertTrue((output / "research_plan.json").exists())


class SharedComparisonPlanTests(unittest.TestCase):
    def test_comparison_plans_once_and_both_legs_share_it(self):
        question = "比較 SOL 與 BNB 在當前宏觀環境下的市場位置與風險。"
        client = _RecordingClient(_valid_plan_payload(["SOL", "BNB"], question))
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            payload = run_comparison("SOL", "BNB", question, output, planner_client=client)

            self.assertEqual(len(client.calls), 1, "比較執行只能規劃一次")
            shared = _read(output, "research_plan.json")
            self.assertEqual(payload["research_plan"], shared)
            for coin in ("SOL", "BNB"):
                leg = _read(output / coin, "research_plan.json")
                self.assertEqual(leg, shared, "兩腳必須共用同一份 plan")
                self.assertEqual(leg["time_window"], shared["time_window"])
                self.assertEqual(leg["comparison_dimensions"], shared["comparison_dimensions"])
                self.assertEqual(_planner_step(output / coin)["path"], "shared")

    def test_comparison_fallback_plan_covers_both_coins(self):
        question = "比較 SOL 與 BNB 在當前宏觀環境下的市場位置與風險。"
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run_comparison("SOL", "BNB", question, output)
            shared = _read(output, "research_plan.json")

            self.assertEqual(shared["coins"], ["SOL", "BNB"])
            self.assertIn("compare_assets", shared["task_modes"])
            self.assertTrue(shared["comparison_dimensions"])


class NoRegressionTests(unittest.TestCase):
    def test_evidence_order_and_report_sources_are_unaffected(self):
        """腳註編號依證據位置決定，所以 run() 不得重排 Collector 交出的順序。"""
        expected, _, _ = collect_evidence_detailed("ETH", live=False)
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run("ETH", "分析 ETH 當前市場狀況。", output)

            written = _read(output, "evidence.json")
            self.assertEqual([item["evidence_id"] for item in written],
                             [item.evidence_id for item in expected])
            report = (output / "report.md").read_text(encoding="utf-8")
            self.assertIn("## Evidence Sources", report)
            self.assertIn("## Stance", report)

    def test_plan_does_not_gate_collection_in_this_task(self):
        """T2 刻意不改 Collector 選擇：不同 required_domains 的題目仍蒐集同一組來源。"""
        collected = {}
        for label, question in (("narrow", "分析 ETH 當前市場狀況。"),
                                ("wide", "分析 ETH 過去兩週的鏈上、新聞與社群訊號是否一致。")):
            with tempfile.TemporaryDirectory() as directory:
                output = Path(directory)
                run("ETH", question, output)
                collected[label] = ([item["data_type"] for item in _read(output, "evidence.json")],
                                    _read(output, "research_plan.json")["required_domains"])

        self.assertNotEqual(collected["narrow"][1], collected["wide"][1], "兩題的 plan 應該不同")
        self.assertEqual(collected["narrow"][0], collected["wide"][0],
                         "本任務不讓 plan 縮減蒐集範圍，證據組成必須一致")


if __name__ == "__main__":
    unittest.main()
