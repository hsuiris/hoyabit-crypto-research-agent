"""T2：Research Planner 測試。

覆蓋 T2-planner.md 的 A-E 五題（多源整合、假設驗證、比較、模糊題、Invalid LLM Output），
並額外驗證：plan 可序列化、plan 不含方向性結論字樣、fallback 是 deterministic、
MockLLMClient 收到的呼叫使用 keyword-only 參數。
"""

from __future__ import annotations

import json
import unittest
from dataclasses import asdict

from src.planner import (
    PLANNING_PATH_FALLBACK,
    PLANNING_PATH_LLM,
    RESEARCH_PLAN_SCHEMA,
    build_research_plan,
    plan_to_dict,
)
from src.schemas import TASK_MODES, TIME_WINDOW_SOURCE_DEFAULT, TIME_WINDOW_SOURCE_EXPLICIT


class MockLLMClient:
    """測試用 LLMClient：記錄每次呼叫，回傳預先準備好的回應，不觸碰任何網路。"""

    def __init__(self, *responses: dict) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def generate_json(self, *, prompt: str, schema: dict, schema_name: str, timeout_seconds: float) -> dict:
        self.calls.append({
            "prompt": prompt, "schema": schema,
            "schema_name": schema_name, "timeout_seconds": timeout_seconds,
        })
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


class RaisingLLMClient:
    """模擬模型呼叫直接拋出例外（例如逾時或連線錯誤）。"""

    def generate_json(self, *, prompt: str, schema: dict, schema_name: str, timeout_seconds: float) -> dict:
        raise RuntimeError("simulated model timeout")


_DIRECTIONAL_WORDS = ("看漲", "看跌", "上漲", "下跌", "買進", "賣出", "買入", "賣入", "牛市", "熊市")


def _assert_plan_is_direction_free(test_case: unittest.TestCase, plan) -> None:
    """Planner 不得產生任何市場結論或方向判斷；掃過所有字串欄位確認沒有方向性字樣。"""
    serialized = json.dumps(asdict(plan), ensure_ascii=False)
    for word in _DIRECTIONAL_WORDS:
        test_case.assertNotIn(word, serialized, f"plan 不應包含方向性字樣：{word}")


class FallbackPathTests(unittest.TestCase):
    """未注入 client，或注入的 client 失敗時，都必須走 deterministic keyword fallback。"""

    def test_question_a_multi_source_integration(self):
        question = "分析 BTC 過去兩週市場表現，整合價格、鏈上、新聞與社群。"
        plan, log = build_research_plan(question, ["BTC"])

        self.assertEqual(log["path"], PLANNING_PATH_FALLBACK)
        self.assertTrue(log["fallback_used"])
        self.assertIn("describe_market_state", plan.task_modes)
        self.assertIn("assess_consistency", plan.task_modes)
        self.assertEqual(plan.time_window, {"days": 14, "source": TIME_WINDOW_SOURCE_EXPLICIT})
        self.assertGreaterEqual(len(plan.required_domains), 2)
        for domain in ("market", "news", "onchain", "social"):
            self.assertIn(domain, plan.required_domains)
        _assert_plan_is_direction_free(self, plan)

    def test_question_b_hypothesis_is_symmetric(self):
        question = "市場認為 ETH 短期將維持盤整，請找支持與反對證據。"
        plan, log = build_research_plan(question, ["ETH"])

        self.assertIn("test_hypothesis", plan.task_modes)
        self.assertGreaterEqual(len(plan.hypotheses), 1)
        hypothesis = plan.hypotheses[0]
        self.assertTrue(hypothesis.support_questions)
        self.assertTrue(hypothesis.contradiction_questions)
        self.assertTrue(hypothesis.falsification_conditions)
        self.assertEqual(len(hypothesis.support_questions), len(hypothesis.contradiction_questions))
        _assert_plan_is_direction_free(self, plan)

    def test_question_c_comparison_shares_window_and_dimensions(self):
        question = "比較 SOL 與 BNB 在當前宏觀環境下的市場位置與風險。"
        plan, log = build_research_plan(question, ["SOL", "BNB"])

        self.assertIn("compare_assets", plan.task_modes)
        self.assertEqual(plan.coins, ["SOL", "BNB"])
        self.assertTrue(plan.comparison_dimensions)
        # 一份 plan 本身只有一個 time_window，兩個標的天然共用同一份，不需要另外同步。
        self.assertIn("days", plan.time_window)
        self.assertIn("source", plan.time_window)
        _assert_plan_is_direction_free(self, plan)

    def test_question_d_ambiguous_question_defaults_time_window(self):
        question = "分析 XRP 當前市場狀況。"
        plan, log = build_research_plan(question, ["XRP"])

        self.assertIn("describe_market_state", plan.task_modes)
        self.assertEqual(plan.time_window["source"], TIME_WINDOW_SOURCE_DEFAULT)
        self.assertTrue(plan.assumptions, "使用預設時間窗必須在 assumptions 中明示")
        self.assertTrue(any("14" in item or "天" in item for item in plan.assumptions))
        _assert_plan_is_direction_free(self, plan)

    def test_question_e_invalid_llm_output_falls_back_without_raising(self):
        client = MockLLMClient({"unexpected": "shape"})
        plan, log = build_research_plan("分析 ETH 當前市場狀況。", ["ETH"], client=client)

        self.assertEqual(log["path"], PLANNING_PATH_FALLBACK)
        self.assertTrue(log["fallback_used"])
        self.assertIsNotNone(log["fallback_reason"])
        self.assertIn("describe_market_state", plan.task_modes)

    def test_unknown_task_mode_from_llm_falls_back(self):
        client = MockLLMClient({
            "coins": ["ETH"],
            "task_modes": ["predict_price_direction"],
            "primary_question": "分析 ETH",
            "time_window": {"days": 14, "source": "default"},
            "hypotheses": [],
            "required_domains": ["market"],
            "comparison_dimensions": [],
            "assumptions": [],
            "stop_conditions": {"max_evidence": 36, "max_followup_rounds": 1},
        })
        plan, log = build_research_plan("分析 ETH 當前市場狀況。", ["ETH"], client=client)

        self.assertEqual(log["path"], PLANNING_PATH_FALLBACK)
        for mode in plan.task_modes:
            self.assertIn(mode, TASK_MODES)

    def test_llm_client_raising_exception_falls_back(self):
        plan, log = build_research_plan("分析 ETH 當前市場狀況。", ["ETH"], client=RaisingLLMClient())

        self.assertEqual(log["path"], PLANNING_PATH_FALLBACK)
        self.assertIn("simulated model timeout", log["fallback_reason"])
        self.assertIn("describe_market_state", plan.task_modes)

    def test_run_continues_and_execution_log_style_fields_are_present(self):
        """Log 必須含 planner provider/model 呼叫端可用的欄位：path、duration、fallback flag。"""
        plan, log = build_research_plan("分析 XRP 當前市場狀況。", ["XRP"])
        for key in ("path", "fallback_used", "fallback_reason", "duration_seconds"):
            self.assertIn(key, log)
        self.assertIsInstance(log["duration_seconds"], float)

    def test_fallback_is_deterministic_across_repeated_calls(self):
        question = "比較 SOL 與 BNB 在當前宏觀環境下的市場位置與風險。"
        first, _ = build_research_plan(question, ["SOL", "BNB"])
        second, _ = build_research_plan(question, ["SOL", "BNB"])

        self.assertEqual(plan_to_dict(first), plan_to_dict(second))

    def test_no_client_injected_uses_fallback_directly(self):
        plan, log = build_research_plan("分析 ETH 當前市場狀況。", "ETH")

        self.assertEqual(log["path"], PLANNING_PATH_FALLBACK)
        self.assertEqual(log["fallback_reason"], "no_client_injected")
        self.assertEqual(plan.coins, ["ETH"])


class LlmPathTests(unittest.TestCase):
    """提供合法 LLM 回應時，plan 應直接採用該回應，且呼叫使用 keyword-only 參數。"""

    def _valid_llm_response(self) -> dict:
        return {
            "coins": ["ETH"],
            "task_modes": ["describe_market_state", "identify_risks"],
            "primary_question": "分析 ETH 當前市場狀況。",
            "time_window": {"days": 14, "source": "default"},
            "hypotheses": [],
            "required_domains": ["market", "derivatives"],
            "comparison_dimensions": [],
            "assumptions": ["未指定時間範圍，沿用預設 14 天"],
            "stop_conditions": {"max_evidence": 36, "max_followup_rounds": 1},
        }

    def test_valid_llm_response_is_used_directly(self):
        client = MockLLMClient(self._valid_llm_response())
        plan, log = build_research_plan("分析 ETH 當前市場狀況。", ["ETH"], client=client)

        self.assertEqual(log["path"], PLANNING_PATH_LLM)
        self.assertFalse(log["fallback_used"])
        self.assertEqual(plan.task_modes, ["describe_market_state", "identify_risks"])
        self.assertEqual(plan.required_domains, ["market", "derivatives"])

    def test_mock_client_receives_keyword_only_call(self):
        client = MockLLMClient(self._valid_llm_response())
        build_research_plan("分析 ETH 當前市場狀況。", ["ETH"], client=client)

        call = client.calls[0]
        self.assertEqual(call["schema_name"], "research_plan")
        self.assertEqual(call["schema"], RESEARCH_PLAN_SCHEMA)
        self.assertIsInstance(call["timeout_seconds"], float)
        self.assertIn("ETH", call["prompt"])

    def test_llm_response_missing_hypotheses_for_test_hypothesis_mode_falls_back(self):
        """宣告 test_hypothesis 卻不給 hypotheses，屬於不合法輸出，必須降級不得中止。"""
        response = self._valid_llm_response()
        response["task_modes"] = ["test_hypothesis"]
        response["hypotheses"] = []
        client = MockLLMClient(response)

        plan, log = build_research_plan("市場認為 ETH 短期將維持盤整。", ["ETH"], client=client)

        self.assertEqual(log["path"], PLANNING_PATH_FALLBACK)
        self.assertTrue(plan.hypotheses)

    def test_llm_response_with_asymmetric_hypothesis_falls_back(self):
        response = self._valid_llm_response()
        response["task_modes"] = ["test_hypothesis"]
        response["hypotheses"] = [{
            "hypothesis_id": "H1",
            "statement": "ETH 將盤整",
            "support_questions": ["支持問題？"],
            "contradiction_questions": [],
            "falsification_conditions": ["條件"],
        }]
        client = MockLLMClient(response)

        plan, log = build_research_plan("市場認為 ETH 短期將維持盤整。", ["ETH"], client=client)

        self.assertEqual(log["path"], PLANNING_PATH_FALLBACK)


class PlanShapeTests(unittest.TestCase):
    """Plan 結構本身的一般性檢查：可序列化、單幣一份 plan、比較執行也是一份共享 plan。"""

    def test_plan_is_json_serializable(self):
        plan, _ = build_research_plan("分析 ETH 當前市場狀況。", ["ETH"])
        serialized = json.dumps(plan_to_dict(plan), ensure_ascii=False)
        restored = json.loads(serialized)
        self.assertEqual(restored["coins"], ["ETH"])

    def test_single_coin_run_produces_one_plan(self):
        plan, _ = build_research_plan("分析 ETH 當前市場狀況。", "ETH")
        self.assertEqual(plan.coins, ["ETH"])

    def test_comparison_run_produces_one_shared_plan(self):
        plan, _ = build_research_plan("比較 SOL 與 BNB 在當前宏觀環境下的市場位置與風險。", ["SOL", "BNB"])
        self.assertEqual(plan.coins, ["SOL", "BNB"])
        self.assertIsInstance(plan.time_window, dict)
        self.assertIsInstance(plan.comparison_dimensions, list)

    def test_empty_question_is_rejected(self):
        with self.assertRaises(ValueError):
            build_research_plan("   ", ["ETH"])

    def test_no_coins_is_rejected(self):
        with self.assertRaises(ValueError):
            build_research_plan("分析 ETH 當前市場狀況。", [])

    def test_stop_conditions_use_product_defaults(self):
        plan, _ = build_research_plan("分析 ETH 當前市場狀況。", ["ETH"])
        self.assertEqual(plan.stop_conditions, {"max_evidence": 36, "max_followup_rounds": 1})


if __name__ == "__main__":
    unittest.main()
