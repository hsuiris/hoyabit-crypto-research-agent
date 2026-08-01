"""T4 接線測試：Claim–Evidence Graph 進入既有流程之後的行為。

`tests/test_claim_graph.py` 驗的是引擎本身的規則（cap、verdict、同源收斂、Critic 不得加分）；
這個檔案驗的是**接線**：

* 每次 run 是否都產出 `claims.json`，且至少有一個主要市場 Claim；
* Claim ID 是否寫回 `Evidence.related_claim_ids`，而且只寫真正被引用的證據；
* 模型只提案、程式計分的邊界在流程裡是否成立（未知 ID／驗證失敗一律降級，不中斷）；
* Critic 的調整只能往下、不能往上；
* Execution Log 是否說清楚 Claim 由誰提案、由誰計分、哪些上限生效；
* 同一批輸入是否永遠得到同一組信心分數；
* 比較題兩腳是否共用同一時間窗，缺失維度不得被補成 0。

所有測試離線執行：模型呼叫一律走注入的 mock client，不觸網路。
"""

from __future__ import annotations

import json
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src.claim_graph import (GRAPH_SOURCE_FALLBACK, GRAPH_SOURCE_LLM,
                            LIMITER_CRITIC_POSITIVE_REJECTED, LIMITER_CRITIC_REDUCTION)
from src.comparison import compare_profiles
from src.day1_mvp import Evidence
from src.llm import (ANALYSIS_SCHEMA, CLAIM_PROPOSAL_FIELD, CLAIM_PROPOSAL_RESULT_FIELD,
                     analyze_with_llm, build_prompt, normalise_analysis_claims)
from src.orchestrator import _build_claims, _claim_client, _claims_markdown, run, run_comparison
from src.schemas import (CONFIDENCE_COMPONENT_KEYS, CONFIDENCE_TYPE_HEURISTIC, CONFIDENCE_WEIGHTS,
                         VERDICT_INSUFFICIENT_EVIDENCE, VERDICTS)
from src.validation import validate_claims

FETCHED_AT = "2026-07-30T00:00:00+00:00"


def make_evidence(evidence_id, data_type, quality=0.90, *, relevance=0.80, independence=1.0,
                  source_type="major_media", lineage=None, verification_status="unverified"):
    """帶 T3 credibility 欄位的證據；只調整測試關心的維度。"""
    return Evidence(
        evidence_id, "TestSource", f"https://example.com/{evidence_id}", FETCHED_AT,
        data_type, "ETH", "14d", {"note": "fixture"}, quality,
        source_type=source_type,
        verification_status=verification_status,
        source_lineage_id=lineage or evidence_id,
        claim_relevance=relevance,
        independence_factor=independence,
        score_breakdown={"final_score": quality},
    )


def fake_result(evidence, question="ETH 是否偏多？"):
    """_build_claims 需要的最小 result 形狀。"""
    return {
        "coin": "ETH", "question": question,
        "stance": {"stance": "bullish", "basis": "多方權重領先", "bull_weight": 2.0, "bear_weight": 0.5},
        "evidence_ids": [item.evidence_id for item in evidence],
        "reasoning": {},
    }


def signals_for(bull=(), bear=()):
    return ([{"side": "bull", "text": f"多方訊號 {item}", "evidence_id": item, "weight": 1.0}
             for item in bull]
            + [{"side": "bear", "text": f"空方訊號 {item}", "evidence_id": item, "weight": 1.0}
               for item in bear])


class ProposalClient:
    """讀取 claim prompt 內的 Evidence 清單，回傳依測試需求組出的提案。"""

    def __init__(self, builder):
        self.builder = builder
        self.calls = []

    def generate_json(self, *, prompt, schema, schema_name, timeout_seconds):
        payload = json.loads(prompt[prompt.index("{"):])
        self.calls.append({"schema_name": schema_name, "timeout_seconds": timeout_seconds,
                           "evidence_ids": [item["evidence_id"] for item in payload["evidence"]]})
        return {"claims": self.builder(payload)}


def market_proposal(payload, *, extra=None):
    """用 prompt 裡真實存在的第一筆證據組一個合法提案。"""
    first = payload["evidence"][0]["evidence_id"]
    proposal = {
        "statement": "ETH 短期動能偏多",
        "facts": [{"statement": "期間報酬為正", "evidence_ids": [first]}],
        "inference": "價格動能為正，構成偏多解讀。",
        "conclusion": "本次證據偏多。",
        "supporting_evidence_ids": [first],
        "contradicting_evidence_ids": [],
    }
    proposal.update(extra or {})
    return [proposal]


def offline_run(directory, question="T4 claim graph 離線接線", **kwargs):
    return run("ETH", question, Path(directory), live=False, use_llm=False, **kwargs)


class OfflineArtifactTests(unittest.TestCase):
    """離線流程必須照樣產出 claims.json，並誠實輸出 insufficient_evidence。"""

    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        cls.output = Path(cls._directory.name)
        cls.result = offline_run(cls.output)
        cls.document = json.loads((cls.output / "claims.json").read_text(encoding="utf-8"))
        cls.log = json.loads((cls.output / "execution_log.json").read_text(encoding="utf-8"))
        cls.evidence = json.loads((cls.output / "evidence.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()

    def test_claims_json_is_written_next_to_the_other_artifacts(self):
        for name in ("report.md", "evidence.json", "execution_log.json",
                     "research_plan.json", "claims.json"):
            self.assertTrue((self.output / name).exists(), name)

    def test_at_least_one_market_judgment_claim_exists(self):
        claims = self.document["claims"]
        self.assertTrue(claims)
        self.assertIn("market_judgment", [claim["claim_type"] for claim in claims])
        for claim in claims:
            self.assertIn(claim["verdict"], VERDICTS)

    def test_offline_fixture_support_produces_insufficient_evidence(self):
        # 離線模式的證據全是 fallback fixture，因此不得給出方向；這是刻意的誠實降級。
        for claim in self.document["claims"]:
            self.assertEqual(claim["verdict"], VERDICT_INSUFFICIENT_EVIDENCE)
            self.assertIn("fallback_only_primary_support", claim["confidence"]["limiters"])

    def test_every_claim_is_traceable_and_layered(self):
        evidence_ids = set(self.result["evidence_ids"])
        for claim in self.document["claims"]:
            self.assertTrue(claim["inference"])
            self.assertTrue(claim["conclusion"])
            self.assertTrue(claim["facts"])
            cited = set(claim["supporting_evidence_ids"]) | set(claim["contradicting_evidence_ids"])
            for fact in claim["facts"]:
                self.assertTrue(fact["evidence_ids"])
                cited |= set(fact["evidence_ids"])
            self.assertTrue(cited <= evidence_ids, cited - evidence_ids)

    def test_confidence_is_declared_heuristic_with_the_frozen_components(self):
        self.assertEqual(self.document["confidence_type"], CONFIDENCE_TYPE_HEURISTIC)
        for claim in self.document["claims"]:
            confidence = claim["confidence"]
            self.assertEqual(confidence["type"], CONFIDENCE_TYPE_HEURISTIC)
            self.assertEqual(set(confidence["components"]), set(CONFIDENCE_COMPONENT_KEYS))
            self.assertTrue(0.0 <= confidence["score"] <= 1.0)

    def test_related_claim_ids_are_written_back_only_for_cited_evidence(self):
        claim_ids = {claim["claim_id"] for claim in self.document["claims"]}
        cited = set()
        for claim in self.document["claims"]:
            cited |= set(claim["supporting_evidence_ids"]) | set(claim["contradicting_evidence_ids"])
        self.assertTrue(cited)
        for record in self.evidence:
            if record["evidence_id"] in cited:
                self.assertTrue(record["related_claim_ids"], record["evidence_id"])
                self.assertTrue(set(record["related_claim_ids"]) <= claim_ids)
            else:
                self.assertEqual(record["related_claim_ids"], [], record["evidence_id"])

    def test_execution_log_step_records_who_proposed_and_who_scored(self):
        names = [step["name"] for step in self.log["steps"]]
        self.assertIn("build_claims", names)
        # Claim 依賴 Critic 的調整，因此必須排在 critic_review 之後、報告產出之前。
        self.assertLess(names.index("critic_review"), names.index("build_claims"))
        self.assertLess(names.index("build_claims"), names.index("generate_report"))
        step = next(item for item in self.log["steps"] if item["name"] == "build_claims")
        self.assertEqual(step["scored_by"], "deterministic_rules")
        self.assertEqual(step["proposed_by"], "deterministic_rules")
        self.assertEqual(step["claim_source"], GRAPH_SOURCE_FALLBACK)
        self.assertEqual(step["confidence_weights"], dict(CONFIDENCE_WEIGHTS))
        self.assertEqual(step["claim_count"], len(self.document["claims"]))
        self.assertIsInstance(step["duration_ms"], float)

    def test_claim_building_does_not_add_a_fourth_phase_ceiling(self):
        budget = self.log["time_budget"]
        self.assertEqual(set(budget["phase_actual_ms"]),
                         {"collection_ms", "reasoning_ms", "critic_ms"})

    def test_report_shows_the_three_layers_and_the_limiters(self):
        report = (self.output / "report.md").read_text(encoding="utf-8")
        self.assertIn("## Claims", report)
        self.assertIn("- 推論：", report)
        self.assertIn("- 結論：", report)
        self.assertIn("- 生效上限：", report)
        self.assertIn("heuristic evidence score", report)

    def test_result_exposes_claims_for_downstream_consumers(self):
        self.assertEqual(self.result["claims"], self.document["claims"])
        self.assertEqual(self.result["claim_graph"]["source"], GRAPH_SOURCE_FALLBACK)
        self.assertGreater(self.result["claim_graph"]["linked_evidence_count"], 0)

    def test_claims_document_is_json_serialisable(self):
        self.assertEqual(json.loads(json.dumps(self.document, ensure_ascii=False)), self.document)


class DeterminismTests(unittest.TestCase):
    def test_two_offline_runs_produce_identical_claims(self):
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            offline_run(first)
            offline_run(second)
            left = json.loads((Path(first) / "claims.json").read_text(encoding="utf-8"))
            right = json.loads((Path(second) / "claims.json").read_text(encoding="utf-8"))
        self.assertEqual(left, right)


class LLMBoundaryTests(unittest.TestCase):
    """模型只提案：文字可以來自模型，verdict 與 confidence 一律由程式決定。"""

    def test_injected_client_proposals_become_the_claim_text(self):
        client = ProposalClient(market_proposal)
        with tempfile.TemporaryDirectory() as directory:
            result = offline_run(directory, claim_client=client)
            log = json.loads((Path(directory) / "execution_log.json").read_text(encoding="utf-8"))

        self.assertEqual(result["claim_graph"]["source"], GRAPH_SOURCE_LLM)
        self.assertEqual([claim["statement"] for claim in result["claims"]], ["ETH 短期動能偏多"])
        step = next(item for item in log["steps"] if item["name"] == "build_claims")
        self.assertEqual(step["proposed_by"], "llm")
        self.assertEqual(step["scored_by"], "deterministic_rules")
        self.assertEqual(client.calls[0]["schema_name"], "claim_graph")

    def test_model_supplied_confidence_and_verdict_are_discarded(self):
        client = ProposalClient(lambda payload: market_proposal(
            payload, extra={"confidence": 0.99, "verdict": "supported"}))
        with tempfile.TemporaryDirectory() as directory:
            result = offline_run(directory, claim_client=client)

        claim = result["claims"][0]
        self.assertNotEqual(claim["confidence"]["score"], 0.99)
        # 支持證據只有離線 fixture，所以程式的結論必須是資料不足，而不是模型說的 supported。
        self.assertEqual(claim["verdict"], VERDICT_INSUFFICIENT_EVIDENCE)

    def test_unknown_evidence_id_degrades_to_deterministic_claims(self):
        client = ProposalClient(lambda payload: market_proposal(
            payload, extra={"supporting_evidence_ids": ["EV-DOES-NOT-EXIST"]}))
        with tempfile.TemporaryDirectory() as directory:
            result = offline_run(directory, claim_client=client)
            log = json.loads((Path(directory) / "execution_log.json").read_text(encoding="utf-8"))

        self.assertEqual(result["claim_graph"]["source"], GRAPH_SOURCE_FALLBACK)
        self.assertIn("unknown evidence ids", result["claim_graph"]["fallback_reason"])
        self.assertTrue(result["claims"])  # 降級不是中斷：報告照樣有 Claim
        step = next(item for item in log["steps"] if item["name"] == "build_claims")
        self.assertEqual(step["status"], "fallback")

    def test_model_failure_degrades_without_stopping_the_run(self):
        class BrokenClient:
            def generate_json(self, **kwargs):
                raise TimeoutError("claim call timed out")

        with tempfile.TemporaryDirectory() as directory:
            result = offline_run(directory, claim_client=BrokenClient())

        self.assertEqual(result["claim_graph"]["source"], GRAPH_SOURCE_FALLBACK)
        self.assertIn("TimeoutError", result["claim_graph"]["fallback_reason"])

    def test_claim_validation_failure_rebuilds_deterministically(self):
        evidence = [make_evidence("EV-1", "market"), make_evidence("EV-2", "news")]
        result = fake_result(evidence)
        client = ProposalClient(market_proposal)
        # 第一次驗證失敗（模擬模型路徑產生了不可稽核的圖），第二次是重建後的 deterministic 圖。
        with patch("src.orchestrator.validate_claims", side_effect=[["boom"], []]):
            graph = _build_claims(result, evidence, {}, signals_for(bull=["EV-1"], bear=["EV-2"]),
                                  None, client=client, deadline=time.monotonic() + 60)

        self.assertEqual(graph["source"], GRAPH_SOURCE_FALLBACK)
        self.assertIn("claim validation failed", graph["fallback_reason"])

    def test_broken_deterministic_graph_stops_the_run(self):
        evidence = [make_evidence("EV-1", "market")]
        with patch("src.orchestrator.validate_claims", return_value=["structural problem"]):
            with self.assertRaisesRegex(ValueError, "Claim validation failed"):
                _build_claims(fake_result(evidence), evidence, {}, signals_for(bull=["EV-1"]),
                              None, client=None, deadline=time.monotonic() + 60)


class AnalysisSchemaCompatibilityTests(unittest.TestCase):
    """分析回應可以額外帶 claims，但既有欄位與最終計分責任都不變。"""

    def test_analysis_schema_keeps_its_original_fields(self):
        self.assertEqual(set(ANALYSIS_SCHEMA["required"]), {
            "market_judgment", "confidence", "facts", "inferences", "conclusion",
            "counter_evidence", "observation_points", "cited_evidence_ids",
        })
        self.assertNotIn(CLAIM_PROPOSAL_FIELD, ANALYSIS_SCHEMA["properties"])

    def test_claims_in_the_analysis_response_are_quarantined_as_proposals(self):
        analysis = {"market_judgment": "m", "confidence": 0.5, "facts": [], "inferences": [],
                    "conclusion": "c", "counter_evidence": [], "observation_points": [],
                    "cited_evidence_ids": [], CLAIM_PROPOSAL_FIELD: [{"statement": "s"}]}
        normalised = normalise_analysis_claims(analysis)

        self.assertNotIn(CLAIM_PROPOSAL_FIELD, normalised)
        self.assertEqual(normalised[CLAIM_PROPOSAL_RESULT_FIELD], [{"statement": "s"}])
        for key in ("market_judgment", "conclusion", "facts", "inferences"):
            self.assertIn(key, normalised)

    def test_analyze_with_llm_passes_claims_through_as_proposals(self):
        class AnalysisClient:
            def generate_json(self, **kwargs):
                return {"market_judgment": "m", "confidence": 0.5, "facts": [], "inferences": [],
                        "conclusion": "c", "counter_evidence": [], "observation_points": [],
                        "cited_evidence_ids": [], CLAIM_PROPOSAL_FIELD: [{"statement": "s"}]}

        analysis = analyze_with_llm("ETH", "q", [], client=AnalysisClient())
        self.assertEqual(analysis[CLAIM_PROPOSAL_RESULT_FIELD], [{"statement": "s"}])

    def test_analysis_prompt_states_that_the_program_computes_confidence(self):
        prompt = build_prompt("ETH", "q", [])
        self.assertIn("最終信心分數由程式", prompt)
        self.assertIn("不得隱藏", prompt)

    def test_existing_analysis_response_is_untouched(self):
        analysis = {"market_judgment": "m", "confidence": 0.5}
        self.assertIs(normalise_analysis_claims(analysis), analysis)


class ClaimClientSelectionTests(unittest.TestCase):
    def test_analysis_proposals_are_replayed_instead_of_calling_the_model_again(self):
        reasoning = {CLAIM_PROPOSAL_RESULT_FIELD: [{"statement": "s"}]}
        client, reason = _claim_client(reasoning, use_llm=True, client=None,
                                      deadline=time.monotonic() + 600)

        self.assertEqual(reason, "analysis_response_claims")
        self.assertEqual(client.generate_json(prompt="{}", schema={}, schema_name="claim_graph",
                                              timeout_seconds=1.0),
                         {"claims": [{"statement": "s"}]})

    def test_offline_mode_uses_no_client(self):
        self.assertEqual(_claim_client({}, use_llm=False, client=None,
                                       deadline=time.monotonic() + 600)[1], "llm_disabled")

    def test_expired_budget_skips_the_model(self):
        with patch("src.orchestrator.llm_is_configured", return_value=True):
            _, reason = _claim_client({}, use_llm=True, client=None, deadline=time.monotonic() + 1)
        self.assertIn("time_budget_remaining", reason)

    def test_missing_provider_skips_the_model(self):
        with patch("src.orchestrator.llm_is_configured", return_value=False):
            _, reason = _claim_client({}, use_llm=True, client=None,
                                      deadline=time.monotonic() + 600)
        self.assertEqual(reason, "no_provider_configured")


class CriticInfluenceTests(unittest.TestCase):
    """Critic 可以下調 Claim 信心，不能上調。"""

    def setUp(self):
        self.evidence = [
            make_evidence("EV-M", "market", 0.92, source_type="market_api"),
            make_evidence("EV-N", "news", 0.80),
            make_evidence("EV-C", "onchain", 0.88, source_type="blockchain_raw"),
        ]
        self.signals = signals_for(bull=["EV-M", "EV-C"], bear=["EV-N"])
        self.baseline = self._graph(None)

    def _graph(self, critique):
        return _build_claims(fake_result(self.evidence), self.evidence, {}, self.signals,
                             critique, client=None, deadline=time.monotonic() + 60)

    def test_negative_adjustment_lowers_the_claim_confidence(self):
        graph = self._graph({"confidence_adjustment": -0.2})
        before = self.baseline["claims"][0]["confidence"]
        after = graph["claims"][0]["confidence"]

        self.assertLess(after["score"], before["score"])
        self.assertIn(LIMITER_CRITIC_REDUCTION, after["limiters"])

    def test_positive_adjustment_is_rejected_and_recorded(self):
        graph = self._graph({"confidence_adjustment": 0.4})
        before = self.baseline["claims"][0]["confidence"]
        after = graph["claims"][0]["confidence"]

        self.assertEqual(after["score"], before["score"])
        self.assertIn(LIMITER_CRITIC_POSITIVE_REJECTED, after["limiters"])


class HypothesisWiringTests(unittest.TestCase):
    """假設題：支持與反對強度分開計算，不是數證據筆數。"""

    def _graph(self, evidence, signals):
        plan = {"required_domains": ["market", "news", "onchain"], "hypotheses": [
            {"hypothesis_id": "H1", "statement": "ETH 的漲勢由鏈上資金流入推動",
             "falsification_conditions": ["鏈上資金流出但價格續漲"]},
        ]}
        return _build_claims(fake_result(evidence), evidence, plan, signals, None,
                             client=None, deadline=time.monotonic() + 60)

    def test_hypothesis_with_consistent_support_is_supported(self):
        evidence = [
            make_evidence("EV-M", "market", 0.92, source_type="market_api"),
            make_evidence("EV-C", "onchain", 0.90, source_type="blockchain_raw"),
            make_evidence("EV-N", "news", 0.82),
        ]
        graph = self._graph(evidence, signals_for(bull=["EV-M", "EV-C", "EV-N"]))
        assessment = graph["hypothesis_assessments"][0]

        self.assertEqual(assessment["hypothesis_id"], "H1")
        self.assertEqual(assessment["verdict"], "supported")
        self.assertGreater(assessment["support_strength"], assessment["contradiction_strength"])
        self.assertEqual(assessment["contradiction_strength"], 0.0)

    def test_strong_counter_evidence_moves_the_hypothesis_to_mixed(self):
        evidence = [
            make_evidence("EV-M", "market", 0.92, source_type="market_api"),
            make_evidence("EV-C", "onchain", 0.90, source_type="blockchain_raw"),
            make_evidence("EV-D", "derivatives", 0.90, source_type="derivatives_api"),
            make_evidence("EV-N", "news", 0.88),
        ]
        graph = self._graph(evidence, signals_for(bull=["EV-M", "EV-C"], bear=["EV-D", "EV-N"]))
        assessment = graph["hypothesis_assessments"][0]

        self.assertIn(assessment["verdict"], {"mixed", "partially_supported"})
        self.assertGreater(assessment["contradiction_strength"], 0.0)
        # 反方存在時信心必須低於同樣證據但無反方的情況。
        clean = self._graph(evidence, signals_for(bull=["EV-M", "EV-C"]))
        self.assertLessEqual(assessment["confidence"],
                             clean["hypothesis_assessments"][0]["confidence"])

    def test_twenty_syndicated_items_do_not_beat_one_high_quality_counter(self):
        evidence = [make_evidence(f"EV-S{index:02d}", "news", 0.35, source_type="secondary_media",
                                  lineage="wire-story-1") for index in range(20)]
        evidence.append(make_evidence("EV-C", "onchain", 0.95, source_type="blockchain_raw"))
        graph = self._graph(evidence, signals_for(bull=[item.evidence_id for item in evidence[:20]],
                                                  bear=["EV-C"]))
        assessment = graph["hypothesis_assessments"][0]

        self.assertLess(assessment["support_strength"], assessment["contradiction_strength"])
        self.assertEqual(assessment["independent_support_chains"], 1)


class ComparisonWiringTests(unittest.TestCase):
    """比較題：兩腳共用同一時間窗與維度，缺失維度不得被補成 0。"""

    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        cls.output = Path(cls._directory.name)
        cls.payload = run_comparison("BTC", "ETH", "BTC 與 ETH 哪個風險較低？", cls.output,
                                     live=False, use_llm=False)
        cls.document = json.loads((cls.output / "claims.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()

    def test_shared_claims_document_covers_both_legs(self):
        self.assertEqual(self.document["mode"], "comparison")
        self.assertEqual(self.document["coins"], ["BTC", "ETH"])
        self.assertEqual(sorted(self.document["claims_by_coin"]), ["BTC", "ETH"])
        for coin, leg in self.document["claims_by_coin"].items():
            self.assertTrue(leg["claims"], coin)

    def test_both_legs_are_judged_over_the_same_window(self):
        window = self.document["shared_time_window"]
        for coin in ("BTC", "ETH"):
            plan = json.loads((self.output / coin / "research_plan.json").read_text(encoding="utf-8"))
            self.assertEqual(plan["time_window"], window)

    def test_each_leg_keeps_its_own_claims_file(self):
        for coin in ("BTC", "ETH"):
            leg = json.loads((self.output / coin / "claims.json").read_text(encoding="utf-8"))
            self.assertEqual(leg, self.document["claims_by_coin"][coin])

    def test_each_comparison_dimension_keeps_its_evidence_ids(self):
        for coin in ("BTC", "ETH"):
            profile = self.payload["profiles"][coin]
            self.assertTrue(profile["liquidity"]["evidence_id"], coin)
            self.assertTrue(profile["risk_exposure"]["cited_evidence_ids"], coin)
            self.assertTrue(profile["attention"]["cited_evidence_ids"], coin)

    def test_missing_dimension_stays_missing_instead_of_zero(self):
        # 缺失維度必須留成 None：一個資金費率抓失敗的幣不得因此看起來「低風險」。
        profile_a = json.loads(json.dumps(self.payload["profiles"]["BTC"]))
        profile_b = json.loads(json.dumps(self.payload["profiles"]["ETH"]))
        profile_a["risk_exposure"]["composite_score"] = None
        risk = compare_profiles(profile_a, profile_b)["risk_exposure"]

        self.assertIsNone(risk["a"])
        self.assertNotEqual(risk["a"], 0)
        self.assertIn("資料不可用", risk["verdict"])


class ClaimValidatorTests(unittest.TestCase):
    """validate_claims 擋掉不可稽核的圖，而不是等到讀者發現。"""

    def setUp(self):
        self.evidence = [
            make_evidence("EV-1", "market", 0.90, source_type="market_api"),
            make_evidence("EV-2", "news", 0.80),
            make_evidence("EV-3", "social", 0.20, source_type="fallback_fixture",
                          verification_status="fallback"),
            make_evidence("EV-4", "news", 0.0, verification_status="rejected"),
        ]

    def claim(self, **overrides):
        claim = {
            "claim_id": "CL-001", "statement": "ETH 偏多", "claim_type": "market_judgment",
            "verdict": "partially_supported",
            "facts": [{"statement": "報酬為正", "evidence_ids": ["EV-1"]}],
            "inference": "動能為正", "conclusion": "偏多",
            "supporting_evidence_ids": ["EV-1"], "contradicting_evidence_ids": ["EV-2"],
            "confidence": {"score": 0.5, "level": "medium", "type": CONFIDENCE_TYPE_HEURISTIC,
                           "components": {key: 0.5 for key in CONFIDENCE_COMPONENT_KEYS},
                           "limiters": []},
            "limitations": [], "invalidation_conditions": [], "watchpoints": [],
        }
        claim.update(overrides)
        return claim

    def test_a_well_formed_claim_passes(self):
        self.assertEqual(validate_claims([self.claim()], self.evidence), [])

    def test_empty_graph_is_rejected(self):
        self.assertEqual(validate_claims([], self.evidence),
                         ["claim graph must contain at least one claim"])

    def test_unknown_evidence_id_is_reported(self):
        errors = validate_claims([self.claim(supporting_evidence_ids=["EV-404"])], self.evidence)
        self.assertTrue(any("unknown evidence ids" in error for error in errors))

    def test_rejected_evidence_cannot_be_cited(self):
        errors = validate_claims([self.claim(contradicting_evidence_ids=["EV-4"])], self.evidence)
        self.assertTrue(any("cites rejected evidence" in error for error in errors))

    def test_claim_without_support_must_be_insufficient_evidence(self):
        errors = validate_claims([self.claim(supporting_evidence_ids=[])], self.evidence)
        self.assertTrue(any("must be insufficient_evidence" in error for error in errors))

        ok = self.claim(supporting_evidence_ids=[], verdict=VERDICT_INSUFFICIENT_EVIDENCE)
        self.assertEqual(validate_claims([ok], self.evidence), [])

    def test_fallback_only_support_cannot_carry_a_direction(self):
        errors = validate_claims([self.claim(supporting_evidence_ids=["EV-3"])], self.evidence)
        self.assertTrue(any("sole support" in error for error in errors))

    def test_same_evidence_cannot_support_and_contradict(self):
        errors = validate_claims([self.claim(contradicting_evidence_ids=["EV-1"])], self.evidence)
        self.assertTrue(any("both supports and contradicts" in error for error in errors))

    def test_missing_layers_and_facts_are_reported(self):
        errors = validate_claims([self.claim(inference="", conclusion="", facts=[
            {"statement": "沒有引用", "evidence_ids": []}])], self.evidence)
        self.assertTrue(any("missing inference layer" in error for error in errors))
        self.assertTrue(any("missing conclusion layer" in error for error in errors))
        self.assertTrue(any("must cite at least one evidence id" in error for error in errors))

    def test_confidence_shape_is_enforced(self):
        errors = validate_claims([self.claim(confidence={
            "score": 1.4, "level": "certain", "type": "probability",
            "components": {"weighted_evidence_quality": 1.0}, "limiters": {}})], self.evidence)
        self.assertTrue(any("confidence score must be between 0 and 1" in error for error in errors))
        self.assertTrue(any("unknown confidence level" in error for error in errors))
        self.assertTrue(any("confidence type must be" in error for error in errors))
        self.assertTrue(any("components must be exactly" in error for error in errors))
        self.assertTrue(any("limiters must be a list" in error for error in errors))

    def test_duplicate_claim_ids_are_reported(self):
        errors = validate_claims([self.claim(), self.claim()], self.evidence)
        self.assertTrue(any("duplicate claim_id" in error for error in errors))

    def test_unknown_verdict_is_reported(self):
        errors = validate_claims([self.claim(verdict="probably_true")], self.evidence)
        self.assertTrue(any("unknown verdict" in error for error in errors))

    def test_shape_checks_run_without_evidence(self):
        # 只有圖沒有 Evidence 時仍要能檢查形狀，但不得誤報 unknown evidence。
        errors = validate_claims([self.claim()])
        self.assertEqual(errors, [])


class MarkdownRenderingTests(unittest.TestCase):
    def test_claims_markdown_lists_both_sides_and_the_scoring_note(self):
        evidence = [make_evidence("EV-M", "market", 0.92, source_type="market_api"),
                    make_evidence("EV-N", "news", 0.80)]
        graph = _build_claims(fake_result(evidence), evidence, {},
                              signals_for(bull=["EV-M"], bear=["EV-N"]), None,
                              client=None, deadline=time.monotonic() + 60)
        markdown = _claims_markdown(graph)

        self.assertIn("- 支持證據：EV-M", markdown)
        self.assertIn("- 反方證據：EV-N", markdown)
        self.assertIn("deterministic Python 計算", markdown)
        self.assertIn("- 信心分量：", markdown)


if __name__ == "__main__":
    unittest.main()
