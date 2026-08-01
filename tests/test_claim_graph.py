"""T4 函式庫層：Claim Graph 與 deterministic confidence 引擎。

這裡驗證的是「規則」而不是「文案」：同一批證據必須永遠得到同一個 confidence，
cap 必須真的壓住分數，Critic 不得加分，同源轉載不得靠數量取勝，
而 LLM 任何一種失敗（例外、非 JSON、無 claims、未知 ID、Fact 混寫推論）都必須降級成
deterministic fallback 而不是中斷流程。所有模型呼叫都走注入的 MockLLMClient，不觸網路。
"""

from __future__ import annotations

import json
import unittest
from pathlib import Path

from src.claim_graph import (
    CLAIM_PROPOSAL_SCHEMA,
    CLAIM_SCHEMA_NAME,
    CLAIM_TYPE_HYPOTHESIS,
    GRAPH_SOURCE_FALLBACK,
    GRAPH_SOURCE_LLM,
    INSUFFICIENT_EVIDENCE_CONFIDENCE_CAP,
    KNOWN_LIMITERS,
    LIMITER_CRITIC_POSITIVE_REJECTED,
    LIMITER_CRITIC_REDUCTION,
    LIMITER_FALLBACK_ONLY,
    LIMITER_HIGH_QUALITY_CONFLICT,
    LIMITER_LOW_DOMAIN_COVERAGE,
    LIMITER_PLAN_SCOPE_FLOOR,
    LIMITER_SINGLE_DOMAIN,
    LIMITER_SINGLE_SECONDARY_NEWS,
    MIN_REQUIRED_DOMAIN_COUNT,
    EvidencePool,
    apply_related_claim_ids,
    build_claim,
    build_claim_graph,
    claims_document,
    evaluate_claim,
)
from src.day1_mvp import Evidence
from src.ports import LLMClient
from src.schemas import (
    CONFIDENCE_COMPONENT_KEYS,
    CONFIDENCE_TYPE_HEURISTIC,
    HIGH_QUALITY_CONFLICT_CONFIDENCE_CAP,
    MIN_DOMAIN_COVERAGE,
    SINGLE_DOMAIN_CONFIDENCE_CAP,
    VERDICT_INSUFFICIENT_EVIDENCE,
    VERDICTS,
)


FETCHED_AT = "2026-01-14T00:00:00+00:00"


def make_evidence(evidence_id, data_type, quality=0.90, *, relevance=0.80, independence=1.0,
                  lineage=None, source_type="major_media", verification_status="unverified",
                  source="TestSource", url="https://example.com/a", coin="ETH", content=None):
    """建一筆帶 T3 credibility 欄位的 Evidence；每個測試只調整它關心的維度。"""
    return Evidence(
        evidence_id, source, url, FETCHED_AT, data_type, coin, "14d",
        content or {"note": "fixture"}, quality,
        source_type=source_type,
        verification_status=verification_status,
        source_lineage_id=lineage or evidence_id,
        claim_relevance=relevance,
        independence_factor=independence,
        score_breakdown={"final_score": quality},
    )


def fallback_evidence(evidence_id, data_type):
    """降級 fixture：即使上游填了漂亮的分數，hard cap 也必須把品質壓回 0.20。"""
    return make_evidence(evidence_id, data_type, 0.90, source_type="fallback_fixture",
                         source="OfflineFixture", url="https://example.com/fallback")


def signals_from(bull_ids=(), bear_ids=(), weight=1.0):
    signals = [{"side": "bull", "text": "多方訊號 %s" % item, "evidence_id": item, "weight": weight}
               for item in bull_ids]
    signals += [{"side": "bear", "text": "空方訊號 %s" % item, "evidence_id": item, "weight": weight}
                for item in bear_ids]
    return signals


class MockLLMClient:
    """測試用 LLMClient：回傳預先準備好的提案或直接拋錯，永不觸網路。"""

    def __init__(self, response=None, error=None):
        self.response = response
        self.error = error
        self.calls = []

    def generate_json(self, *, prompt, schema, schema_name, timeout_seconds):
        self.calls.append({"prompt": prompt, "schema": schema,
                           "schema_name": schema_name, "timeout_seconds": timeout_seconds})
        if self.error is not None:
            raise self.error
        return self.response


def proposal(statement="ETH 短期動能偏多", *, facts, supporting, contradicting=(),
             inference="支持側強度高於反向側，構成偏多解讀。", conclusion="本次證據偏多，但保留反向側。",
             claim_type=None, hypothesis_id=None, **extra):
    payload = {
        "statement": statement,
        "facts": facts,
        "inference": inference,
        "conclusion": conclusion,
        "supporting_evidence_ids": list(supporting),
        "contradicting_evidence_ids": list(contradicting),
    }
    if claim_type:
        payload["claim_type"] = claim_type
    if hypothesis_id:
        payload["hypothesis_id"] = hypothesis_id
    payload.update(extra)
    return payload


class EvidencePoolTests(unittest.TestCase):
    def test_rejected_evidence_never_enters_the_pool(self):
        pool = EvidencePool([
            make_evidence("EV-1", "market"),
            make_evidence("EV-2", "news", verification_status="rejected"),
        ])

        self.assertEqual(pool.active_ids, ["EV-1"])
        self.assertEqual(pool.rejected_ids, ["EV-2"])
        with self.assertRaisesRegex(ValueError, "rejected evidence cannot be cited"):
            pool.require(["EV-2"])

    def test_unknown_evidence_id_is_rejected_not_silently_dropped(self):
        pool = EvidencePool([make_evidence("EV-1", "market")])
        with self.assertRaisesRegex(ValueError, "unknown evidence ids: EV-404"):
            pool.require(["EV-1", "EV-404"])

    def test_missing_credibility_fields_fall_back_to_conservative_defaults(self):
        pool = EvidencePool([{"evidence_id": "EV-1", "data_type": "market"}])
        record = pool.get("EV-1")

        self.assertEqual(record["quality"], 0.20)
        self.assertEqual(record["claim_relevance"], 0.50)
        self.assertEqual(record["independence_factor"], 1.0)
        self.assertEqual(record["verification_status"], "unverified")

    def test_fallback_fixture_quality_is_capped_even_when_upstream_scores_it_high(self):
        pool = EvidencePool([fallback_evidence("EV-F1", "market")])
        self.assertEqual(pool.quality("EV-F1"), 0.20)
        self.assertTrue(pool.is_fallback("EV-F1"))
        self.assertFalse(pool.is_high_quality("EV-F1"))

    def test_evidence_cannot_support_and_contradict_the_same_claim(self):
        pool = EvidencePool([make_evidence("EV-1", "market"), make_evidence("EV-2", "news")])
        with self.assertRaisesRegex(ValueError, "support and contradict"):
            evaluate_claim(pool, supporting_ids=["EV-1"], contradicting_ids=["EV-1", "EV-2"])


class ConfidenceRuleTests(unittest.TestCase):
    def test_single_supporting_domain_is_capped_at_060(self):
        pool = EvidencePool([
            make_evidence("EV-M1", "market", lineage="market-api"),
            make_evidence("EV-M2", "vegas_channel", lineage="exchange-api"),
            make_evidence("EV-N1", "news", 0.30, relevance=0.8, independence=0.4),
            make_evidence("EV-S1", "social", 0.30, relevance=0.8, independence=0.4),
            make_evidence("EV-O1", "onchain", 0.30, relevance=0.8, independence=0.4),
        ])

        assessment = evaluate_claim(
            pool,
            supporting_ids=["EV-M1", "EV-M2"],
            contradicting_ids=["EV-N1", "EV-S1", "EV-O1"],
        )

        self.assertEqual(assessment["supporting_domains"], ["market"])
        self.assertIn(LIMITER_SINGLE_DOMAIN, assessment["confidence"]["limiters"])
        self.assertLessEqual(assessment["confidence"]["score"], SINGLE_DOMAIN_CONFIDENCE_CAP)
        self.assertEqual(assessment["confidence"]["score"], SINGLE_DOMAIN_CONFIDENCE_CAP)

    def test_high_quality_conflict_is_capped_at_070(self):
        pool = EvidencePool([
            make_evidence("EV-M1", "market", lineage="market-api"),
            make_evidence("EV-N1", "news", lineage="major-media"),
            make_evidence("EV-S1", "social", 0.85, lineage="social-api"),
            make_evidence("EV-O1", "onchain", 0.80, lineage="chain-node"),
        ])

        assessment = evaluate_claim(
            pool,
            supporting_ids=["EV-M1", "EV-N1"],
            contradicting_ids=["EV-S1", "EV-O1"],
        )

        self.assertIn(LIMITER_HIGH_QUALITY_CONFLICT, assessment["confidence"]["limiters"])
        self.assertLessEqual(assessment["confidence"]["score"], HIGH_QUALITY_CONFLICT_CONFIDENCE_CAP)
        self.assertEqual(assessment["confidence"]["score"], HIGH_QUALITY_CONFLICT_CONFIDENCE_CAP)
        self.assertEqual(assessment["verdict"], "mixed")

    def test_low_domain_coverage_produces_insufficient_evidence(self):
        pool = EvidencePool([make_evidence("EV-M1", "market")])

        assessment = evaluate_claim(pool, supporting_ids=["EV-M1"])

        self.assertLess(assessment["confidence"]["components"]["domain_coverage"], MIN_DOMAIN_COVERAGE)
        self.assertEqual(assessment["verdict"], VERDICT_INSUFFICIENT_EVIDENCE)
        self.assertIn(LIMITER_LOW_DOMAIN_COVERAGE, assessment["confidence"]["limiters"])
        self.assertLessEqual(assessment["confidence"]["score"], INSUFFICIENT_EVIDENCE_CONFIDENCE_CAP)

    def test_fallback_only_primary_support_produces_insufficient_evidence(self):
        pool = EvidencePool([
            fallback_evidence("EV-F1", "market"),
            fallback_evidence("EV-F2", "news"),
            make_evidence("EV-S1", "social", 0.60),
        ])

        assessment = evaluate_claim(
            pool, supporting_ids=["EV-F1", "EV-F2"], contradicting_ids=["EV-S1"])

        self.assertGreaterEqual(assessment["confidence"]["components"]["domain_coverage"], MIN_DOMAIN_COVERAGE)
        self.assertEqual(assessment["verdict"], VERDICT_INSUFFICIENT_EVIDENCE)
        self.assertIn(LIMITER_FALLBACK_ONLY, assessment["confidence"]["limiters"])

    def test_no_supporting_evidence_produces_insufficient_evidence(self):
        pool = EvidencePool([make_evidence("EV-M1", "market")])
        assessment = evaluate_claim(pool, contradicting_ids=["EV-M1"])

        self.assertEqual(assessment["verdict"], VERDICT_INSUFFICIENT_EVIDENCE)
        self.assertIn("no_supporting_evidence", assessment["confidence"]["limiters"])

    def test_single_secondary_news_chain_is_capped(self):
        pool = EvidencePool([
            make_evidence("EV-N1", "news", 0.55, lineage="wire-story-1", source_type="secondary_media"),
            make_evidence("EV-N2", "news", 0.50, lineage="wire-story-1", source_type="secondary_media"),
            make_evidence("EV-M1", "market"),
        ])

        assessment = evaluate_claim(
            pool, supporting_ids=["EV-N1", "EV-N2"], contradicting_ids=["EV-M1"])

        self.assertEqual(assessment["independent_support_chains"], 1)
        self.assertIn(LIMITER_SINGLE_SECONDARY_NEWS, assessment["confidence"]["limiters"])
        self.assertLessEqual(assessment["confidence"]["score"], 0.60)

    def test_consistent_multi_domain_support_reaches_supported(self):
        pool = EvidencePool([
            make_evidence("EV-M1", "market", lineage="market-api"),
            make_evidence("EV-N1", "news", lineage="major-media"),
            make_evidence("EV-S1", "social", lineage="social-api"),
            make_evidence("EV-O1", "onchain", lineage="chain-node"),
        ])

        assessment = evaluate_claim(
            pool, supporting_ids=["EV-M1", "EV-N1", "EV-S1", "EV-O1"])

        self.assertEqual(assessment["confidence"]["components"]["domain_coverage"], 1.0)
        self.assertEqual(assessment["verdict"], "supported")
        self.assertEqual(assessment["confidence"]["limiters"], [])
        self.assertEqual(assessment["confidence"]["level"], "high")
        self.assertEqual(assessment["confidence"]["type"], CONFIDENCE_TYPE_HEURISTIC)

    def test_missing_onchain_domain_lowers_coverage_and_confidence(self):
        full = EvidencePool([
            make_evidence("EV-M1", "market", lineage="market-api"),
            make_evidence("EV-N1", "news", lineage="major-media"),
            make_evidence("EV-S1", "social", lineage="social-api"),
            make_evidence("EV-O1", "onchain", lineage="chain-node"),
        ])
        partial = EvidencePool([
            make_evidence("EV-M1", "market", lineage="market-api"),
            make_evidence("EV-N1", "news", lineage="major-media"),
            make_evidence("EV-S1", "social", lineage="social-api"),
        ])

        complete = evaluate_claim(full, supporting_ids=["EV-M1", "EV-N1", "EV-S1", "EV-O1"])
        missing = evaluate_claim(partial, supporting_ids=["EV-M1", "EV-N1", "EV-S1"])

        self.assertNotIn("onchain", missing["covered_domains"])
        self.assertEqual(missing["confidence"]["components"]["domain_coverage"], 0.75)
        self.assertLess(missing["confidence"]["score"], complete["confidence"]["score"])

    def test_contradiction_lowers_confidence_relative_to_clean_support(self):
        pool = EvidencePool([
            make_evidence("EV-M1", "market", lineage="market-api"),
            make_evidence("EV-N1", "news", lineage="major-media"),
            make_evidence("EV-S1", "social", lineage="social-api"),
            make_evidence("EV-O1", "onchain", lineage="chain-node"),
        ])

        clean = evaluate_claim(pool, supporting_ids=["EV-M1", "EV-N1", "EV-S1", "EV-O1"])
        conflicted = evaluate_claim(pool, supporting_ids=["EV-M1", "EV-N1"],
                                    contradicting_ids=["EV-S1", "EV-O1"])

        self.assertLess(conflicted["confidence"]["score"], clean["confidence"]["score"])

    def test_components_use_the_frozen_keys_and_weights(self):
        pool = EvidencePool([
            make_evidence("EV-M1", "market", lineage="market-api"),
            make_evidence("EV-N1", "news", lineage="major-media"),
        ])
        assessment = evaluate_claim(pool, supporting_ids=["EV-M1", "EV-N1"])

        self.assertEqual(sorted(assessment["confidence"]["components"]), sorted(CONFIDENCE_COMPONENT_KEYS))
        self.assertIn(assessment["verdict"], VERDICTS)


class CriticBoundaryTests(unittest.TestCase):
    def _pool(self):
        return EvidencePool([
            make_evidence("EV-M1", "market", lineage="market-api"),
            make_evidence("EV-N1", "news", lineage="major-media"),
        ])

    def test_positive_critic_adjustment_is_rejected(self):
        pool = self._pool()
        baseline = evaluate_claim(pool, supporting_ids=["EV-M1", "EV-N1"])
        raised = evaluate_claim(pool, supporting_ids=["EV-M1", "EV-N1"], critic_adjustment=0.25)

        self.assertEqual(raised["confidence"]["score"], baseline["confidence"]["score"])
        self.assertIn(LIMITER_CRITIC_POSITIVE_REJECTED, raised["confidence"]["limiters"])
        self.assertNotIn(LIMITER_CRITIC_REDUCTION, raised["confidence"]["limiters"])

    def test_negative_critic_adjustment_reduces_confidence(self):
        pool = self._pool()
        baseline = evaluate_claim(pool, supporting_ids=["EV-M1", "EV-N1"])
        reduced = evaluate_claim(pool, supporting_ids=["EV-M1", "EV-N1"], critic_adjustment=-0.10)

        self.assertEqual(reduced["confidence"]["score"],
                         round(baseline["confidence"]["score"] - 0.10, 4))
        self.assertIn(LIMITER_CRITIC_REDUCTION, reduced["confidence"]["limiters"])

    def test_critique_flows_through_the_graph_entry_point(self):
        evidence = [make_evidence("EV-M1", "market", lineage="market-api"),
                    make_evidence("EV-N1", "news", lineage="major-media")]
        signals = signals_from(bull_ids=["EV-M1"], bear_ids=["EV-N1"])

        raised = build_claim_graph("ETH", "近期動能？", evidence, signals=signals,
                                   critique={"confidence_adjustment": 0.30})
        lowered = build_claim_graph("ETH", "近期動能？", evidence, signals=signals,
                                    critique={"confidence_adjustment": -0.20})

        self.assertIn(LIMITER_CRITIC_POSITIVE_REJECTED, raised["claims"][0]["confidence"]["limiters"])
        self.assertLess(lowered["claims"][0]["confidence"]["score"],
                        raised["claims"][0]["confidence"]["score"])


class HypothesisModeTests(unittest.TestCase):
    def test_twenty_syndicated_low_quality_items_lose_to_one_high_quality_counter(self):
        evidence = [
            make_evidence("EV-N%02d" % index, "news", 0.30, relevance=0.4, independence=0.2,
                          lineage="wire-story-1", source_type="secondary_media",
                          url="https://aggregator%02d.example.com/story" % index)
            for index in range(1, 21)
        ]
        evidence.append(make_evidence("EV-O1", "onchain", 0.90, relevance=0.9, independence=1.0,
                                      lineage="chain-node"))
        client = MockLLMClient({"claims": [proposal(
            statement="鏈上資金正在流入，價格將延續漲勢",
            claim_type=CLAIM_TYPE_HYPOTHESIS,
            hypothesis_id="H-1",
            facts=[{"statement": "20 篇媒體報導同一則消息", "evidence_ids": ["EV-N01"]}],
            supporting=["EV-N%02d" % index for index in range(1, 21)],
            contradicting=["EV-O1"],
        )]})

        graph = build_claim_graph("ETH", "資金是否持續流入？", evidence, client=client)
        assessment = graph["hypothesis_assessments"][0]

        self.assertEqual(graph["source"], GRAPH_SOURCE_LLM)
        self.assertEqual(assessment["hypothesis_id"], "H-1")
        self.assertEqual(assessment["independent_support_chains"], 1)
        self.assertLess(assessment["support_strength"], assessment["contradiction_strength"])
        self.assertEqual(assessment["verdict"], "contradicted")
        self.assertIn(LIMITER_SINGLE_SECONDARY_NEWS, graph["claims"][0]["confidence"]["limiters"])

    def test_support_and_contradiction_strength_are_reported_separately(self):
        evidence = [
            make_evidence("EV-M1", "market", lineage="market-api"),
            make_evidence("EV-N1", "news", lineage="major-media"),
            make_evidence("EV-S1", "social", 0.40, relevance=0.5, independence=0.5, lineage="social-api"),
            make_evidence("EV-O1", "onchain", lineage="chain-node"),
        ]
        client = MockLLMClient({"claims": [proposal(
            statement="ETH 需求端維持擴張",
            claim_type=CLAIM_TYPE_HYPOTHESIS,
            hypothesis_id="H-2",
            facts=[{"statement": "14 日報酬為正", "evidence_ids": ["EV-M1"]}],
            supporting=["EV-M1", "EV-N1", "EV-O1"],
            contradicting=["EV-S1"],
        )]})

        graph = build_claim_graph("ETH", "需求是否擴張？", evidence, client=client)
        assessment = graph["hypothesis_assessments"][0]

        self.assertGreater(assessment["support_strength"], assessment["contradiction_strength"])
        self.assertNotEqual(assessment["support_strength"], assessment["contradiction_strength"])
        self.assertIn(assessment["verdict"], ("supported", "partially_supported"))
        self.assertEqual(graph["claims"][0]["claim_type"], CLAIM_TYPE_HYPOTHESIS)


class ComparisonModeTests(unittest.TestCase):
    def test_missing_comparison_value_stays_missing_and_cannot_be_invented(self):
        pool = EvidencePool([
            make_evidence("EV-BTC-M", "market", coin="BTC", lineage="market-api-btc"),
            make_evidence("EV-ETH-M", "market", coin="ETH", lineage="market-api-eth"),
            make_evidence("EV-BTC-O", "onchain", coin="BTC", lineage="chain-node-btc"),
        ])

        with self.assertRaisesRegex(ValueError, "unknown evidence ids: EV-ETH-O"):
            build_claim(
                pool, "CL-001",
                statement="BTC 與 ETH 的鏈上活躍度比較",
                facts=[{"statement": "兩幣鏈上活躍度", "evidence_ids": ["EV-BTC-O", "EV-ETH-O"]}],
                inference="不可用未取得的資料補齊比較維度。",
                conclusion="缺失維度必須維持缺失。",
                supporting_ids=["EV-BTC-O"],
            )

        claim, assessment = build_claim(
            pool, "CL-001",
            statement="BTC 與 ETH 的市場表現比較（鏈上維度僅 BTC 可得）",
            claim_type="comparison",
            facts=[{"statement": "BTC 與 ETH 同期間市場資料", "evidence_ids": ["EV-BTC-M", "EV-ETH-M"]}],
            inference="兩幣使用同一 as-of 與同一時間窗計算，缺失維度不補值。",
            conclusion="鏈上維度僅 BTC 有資料，該維度不做比較。",
            supporting_ids=["EV-BTC-M", "EV-ETH-M"],
            limitations=["ETH 鏈上資料缺失，該比較維度維持缺失。"],
        )

        self.assertEqual(claim["supporting_evidence_ids"], ["EV-BTC-M", "EV-ETH-M"])
        self.assertEqual(assessment["supporting_domains"], ["market"])
        self.assertIn("ETH 鏈上資料缺失，該比較維度維持缺失。", claim["limitations"])


class LLMBoundaryTests(unittest.TestCase):
    def _evidence(self):
        return [
            make_evidence("EV-M1", "market", lineage="market-api"),
            make_evidence("EV-N1", "news", lineage="major-media"),
            make_evidence("EV-S1", "social", 0.40, relevance=0.5, independence=0.5, lineage="social-api"),
            make_evidence("EV-O1", "onchain", lineage="chain-node"),
        ]

    def test_mock_client_satisfies_the_llm_client_protocol(self):
        self.assertIsInstance(MockLLMClient({"claims": []}), LLMClient)

    def test_claim_graph_uses_the_frozen_schema_and_trims_long_series(self):
        evidence = self._evidence()
        evidence[0] = make_evidence("EV-M1", "market", lineage="market-api",
                                    content={"prices": list(range(200)), "rsi": 61.4})
        client = MockLLMClient({"claims": [proposal(
            facts=[{"statement": "14 日報酬為正", "evidence_ids": ["EV-M1"]}],
            supporting=["EV-M1", "EV-N1"], contradicting=["EV-S1"],
        )]})

        build_claim_graph("ETH", "近期動能？", evidence, client=client)
        call = client.calls[0]

        self.assertEqual(call["schema"], CLAIM_PROPOSAL_SCHEMA)
        self.assertEqual(call["schema_name"], CLAIM_SCHEMA_NAME)
        self.assertIn("EV-M1", call["prompt"])
        self.assertIn("omitted_series_length", call["prompt"])
        self.assertNotIn('"prices": [0, 1, 2', call["prompt"])

    def test_model_supplied_confidence_and_verdict_are_ignored(self):
        client = MockLLMClient({"claims": [dict(proposal(
            facts=[{"statement": "14 日報酬為正", "evidence_ids": ["EV-M1"]}],
            supporting=["EV-M1", "EV-N1", "EV-O1"], contradicting=["EV-S1"],
        ), confidence={"score": 0.99, "level": "high"}, verdict="supported")]})

        graph = build_claim_graph("ETH", "近期動能？", self._evidence(), client=client)
        claim = graph["claims"][0]

        self.assertEqual(graph["source"], GRAPH_SOURCE_LLM)
        self.assertNotEqual(claim["confidence"]["score"], 0.99)
        self.assertEqual(claim["confidence"]["type"], CONFIDENCE_TYPE_HEURISTIC)
        self.assertIn(claim["verdict"], VERDICTS)

    def test_unknown_evidence_id_falls_back_to_deterministic_claims(self):
        client = MockLLMClient({"claims": [proposal(
            facts=[{"statement": "14 日報酬為正", "evidence_ids": ["EV-M1"]}],
            supporting=["EV-M1", "EV-GHOST"],
        )]})
        signals = signals_from(bull_ids=["EV-M1", "EV-N1"], bear_ids=["EV-S1"])

        graph = build_claim_graph("ETH", "近期動能？", self._evidence(), signals=signals, client=client)

        self.assertEqual(graph["source"], GRAPH_SOURCE_FALLBACK)
        self.assertIn("unknown evidence ids: EV-GHOST", graph["fallback_reason"])
        self.assertTrue(graph["claims"])
        self.assertEqual(graph["claims"][0]["claim_id"], "CL-001")

    def test_fact_mixing_inference_layer_is_rejected(self):
        client = MockLLMClient({"claims": [proposal(
            facts=[{"statement": "價格上漲，因此將會突破前高", "evidence_ids": ["EV-M1"]}],
            supporting=["EV-M1", "EV-N1"],
        )]})
        signals = signals_from(bull_ids=["EV-M1", "EV-N1"], bear_ids=["EV-S1"])

        graph = build_claim_graph("ETH", "近期動能？", self._evidence(), signals=signals, client=client)
        claim = graph["claims"][0]

        self.assertEqual(graph["source"], GRAPH_SOURCE_FALLBACK)
        self.assertIn("mixes inference", graph["fallback_reason"])
        for fact in claim["facts"]:
            self.assertNotIn("因此", fact["statement"])
            self.assertNotEqual(fact["statement"], claim["inference"])
            self.assertNotEqual(fact["statement"], claim["conclusion"])
            self.assertTrue(fact["evidence_ids"])
        self.assertTrue(claim["inference"])
        self.assertTrue(claim["conclusion"])
        self.assertNotEqual(claim["inference"], claim["conclusion"])

    def test_facts_without_evidence_ids_are_rejected(self):
        client = MockLLMClient({"claims": [proposal(
            facts=[{"statement": "市場走強", "evidence_ids": []}],
            supporting=["EV-M1", "EV-N1"],
        )]})
        signals = signals_from(bull_ids=["EV-M1"], bear_ids=["EV-S1"])

        graph = build_claim_graph("ETH", "近期動能？", self._evidence(), signals=signals, client=client)

        self.assertEqual(graph["source"], GRAPH_SOURCE_FALLBACK)
        self.assertIn("evidence id", graph["fallback_reason"])

    def test_missing_inference_or_conclusion_layer_is_rejected(self):
        client = MockLLMClient({"claims": [proposal(
            facts=[{"statement": "14 日報酬為正", "evidence_ids": ["EV-M1"]}],
            supporting=["EV-M1", "EV-N1"], conclusion="",
        )]})
        signals = signals_from(bull_ids=["EV-M1"], bear_ids=["EV-S1"])

        graph = build_claim_graph("ETH", "近期動能？", self._evidence(), signals=signals, client=client)

        self.assertEqual(graph["source"], GRAPH_SOURCE_FALLBACK)
        self.assertIn("separate inference and conclusion", graph["fallback_reason"])

    def test_model_failure_empty_claims_and_bad_json_all_degrade(self):
        signals = signals_from(bull_ids=["EV-M1"], bear_ids=["EV-S1"])
        cases = [
            MockLLMClient(error=RuntimeError("bedrock timeout")),
            MockLLMClient({"claims": []}),
            MockLLMClient("not a json object"),
        ]
        for client in cases:
            graph = build_claim_graph("ETH", "近期動能？", self._evidence(), signals=signals, client=client)
            self.assertEqual(graph["source"], GRAPH_SOURCE_FALLBACK)
            self.assertTrue(graph["fallback_reason"])
            self.assertTrue(graph["claims"])

    def test_rejected_evidence_cited_by_the_model_degrades_the_batch(self):
        evidence = self._evidence()
        evidence.append(make_evidence("EV-R1", "news", verification_status="rejected"))
        client = MockLLMClient({"claims": [proposal(
            facts=[{"statement": "14 日報酬為正", "evidence_ids": ["EV-M1"]}],
            supporting=["EV-M1", "EV-R1"],
        )]})
        signals = signals_from(bull_ids=["EV-M1"], bear_ids=["EV-S1"])

        graph = build_claim_graph("ETH", "近期動能？", evidence, signals=signals, client=client)

        self.assertEqual(graph["source"], GRAPH_SOURCE_FALLBACK)
        self.assertIn("rejected evidence cannot be cited", graph["fallback_reason"])
        self.assertEqual(graph["rejected_evidence_ids"], ["EV-R1"])

    def test_module_never_imports_a_provider_sdk(self):
        source = (Path(__file__).parents[1] / "src" / "claim_graph.py").read_text(encoding="utf-8")
        for forbidden in ("import boto3", "from boto3", "import openai", "generativelanguage",
                          "api.openai.com", "bedrock-runtime", "urlopen"):
            self.assertNotIn(forbidden, source)


class DeterministicFallbackTests(unittest.TestCase):
    def test_no_evidence_produces_a_single_insufficient_claim(self):
        graph = build_claim_graph("ETH", "近期動能？", [])

        self.assertEqual(len(graph["claims"]), 1)
        self.assertEqual(graph["claims"][0]["verdict"], VERDICT_INSUFFICIENT_EVIDENCE)
        self.assertEqual(graph["source"], GRAPH_SOURCE_FALLBACK)
        self.assertIn("no llm client", graph["fallback_reason"])

    def test_evidence_without_directional_signals_stays_insufficient(self):
        evidence = [make_evidence("EV-M1", "market"), make_evidence("EV-N1", "news")]

        graph = build_claim_graph("ETH", "近期動能？", evidence, signals=[])

        self.assertEqual(graph["claims"][0]["verdict"], VERDICT_INSUFFICIENT_EVIDENCE)
        self.assertTrue(graph["claims"][0]["limitations"])

    def test_signal_inventory_builds_a_traceable_market_claim(self):
        evidence = [
            make_evidence("EV-M1", "market", lineage="market-api"),
            make_evidence("EV-N1", "news", lineage="major-media"),
            make_evidence("EV-S1", "social", 0.40, relevance=0.5, independence=0.5, lineage="social-api"),
            make_evidence("EV-O1", "onchain", lineage="chain-node"),
        ]
        signals = signals_from(bull_ids=["EV-M1", "EV-N1", "EV-O1"], bear_ids=["EV-S1"], weight=1.2)

        graph = build_claim_graph("ETH", "近期動能？", evidence, signals=signals,
                                  stance={"basis": "多方 3.6 / 空方 1.2"})
        claim = graph["claims"][0]

        self.assertEqual(claim["supporting_evidence_ids"], ["EV-M1", "EV-N1", "EV-O1"])
        self.assertEqual(claim["contradicting_evidence_ids"], ["EV-S1"])
        self.assertEqual([fact["evidence_ids"] for fact in claim["facts"]],
                         [["EV-M1"], ["EV-N1"], ["EV-O1"]])
        self.assertTrue(claim["invalidation_conditions"])
        self.assertTrue(claim["watchpoints"])
        self.assertIn(claim["verdict"], VERDICTS)

    def test_plan_hypotheses_produce_separate_hypothesis_claims(self):
        evidence = [
            make_evidence("EV-M1", "market", lineage="market-api"),
            make_evidence("EV-N1", "news", lineage="major-media"),
            make_evidence("EV-S1", "social", 0.40, relevance=0.5, independence=0.5, lineage="social-api"),
        ]
        plan = {
            "coins": ["ETH"],
            "required_domains": ["market", "news", "social"],
            "hypotheses": [{"hypothesis_id": "H-1", "statement": "ETH 需求端維持擴張",
                            "falsification_conditions": ["鏈上活躍度連續兩週下滑"]}],
        }
        signals = signals_from(bull_ids=["EV-M1", "EV-N1"], bear_ids=["EV-S1"])

        graph = build_claim_graph("ETH", "需求是否擴張？", evidence, signals=signals, plan=plan)

        self.assertEqual([claim["claim_id"] for claim in graph["claims"]], ["CL-001", "CL-002"])
        self.assertEqual(graph["claims"][1]["claim_type"], CLAIM_TYPE_HYPOTHESIS)
        self.assertEqual(graph["hypothesis_assessments"][0]["hypothesis_id"], "H-1")
        self.assertIn("鏈上活躍度連續兩週下滑", graph["claims"][1]["invalidation_conditions"])
        # required_domains 來自 plan：三個領域全覆蓋，不應被預設的四領域判成資料不足。
        self.assertEqual(graph["claims"][0]["confidence"]["components"]["domain_coverage"], 1.0)


class DeterminismAndSerialisationTests(unittest.TestCase):
    def _inputs(self):
        evidence = [
            make_evidence("EV-M1", "market", lineage="market-api"),
            make_evidence("EV-N1", "news", lineage="major-media"),
            make_evidence("EV-S1", "social", 0.40, relevance=0.5, independence=0.5, lineage="social-api"),
            make_evidence("EV-O1", "onchain", lineage="chain-node"),
        ]
        signals = signals_from(bull_ids=["EV-M1", "EV-N1", "EV-O1"], bear_ids=["EV-S1"])
        return evidence, signals

    def test_same_input_produces_identical_confidence(self):
        evidence, signals = self._inputs()

        first = build_claim_graph("ETH", "近期動能？", evidence, signals=signals)
        second = build_claim_graph("ETH", "近期動能？", evidence, signals=signals)

        self.assertEqual(json.dumps(first["claims"], ensure_ascii=False, sort_keys=True),
                         json.dumps(second["claims"], ensure_ascii=False, sort_keys=True))
        self.assertEqual(first["claims"][0]["confidence"]["score"],
                         second["claims"][0]["confidence"]["score"])

    def test_evidence_order_does_not_change_the_score(self):
        evidence, signals = self._inputs()
        pool_a = EvidencePool(evidence)
        pool_b = EvidencePool(list(reversed(evidence)))

        first = evaluate_claim(pool_a, supporting_ids=["EV-M1", "EV-N1"], contradicting_ids=["EV-S1"])
        second = evaluate_claim(pool_b, supporting_ids=["EV-M1", "EV-N1"], contradicting_ids=["EV-S1"])

        self.assertEqual(first["confidence"], second["confidence"])

    def test_claims_are_json_serialisable_with_known_limiters(self):
        evidence, signals = self._inputs()
        graph = build_claim_graph("ETH", "近期動能？", evidence, signals=signals,
                                  critique={"confidence_adjustment": -0.05})
        document = claims_document(graph)
        encoded = json.dumps(document, ensure_ascii=False)

        self.assertEqual(json.loads(encoded)["claims"][0]["claim_id"], "CL-001")
        self.assertEqual(document["confidence_type"], CONFIDENCE_TYPE_HEURISTIC)
        for claim in document["claims"]:
            self.assertTrue(claim["confidence"]["limiters"])
            for limiter in claim["confidence"]["limiters"]:
                self.assertIn(limiter, KNOWN_LIMITERS)
            self.assertIn(LIMITER_CRITIC_REDUCTION, claim["confidence"]["limiters"])
            self.assertNotIn(LIMITER_HIGH_QUALITY_CONFLICT, claim["confidence"]["limiters"])

    def test_claim_keeps_the_three_layers_in_separate_fields(self):
        evidence, signals = self._inputs()
        graph = build_claim_graph("ETH", "近期動能？", evidence, signals=signals)
        claim = graph["claims"][0]

        self.assertEqual(sorted(claim), sorted([
            "claim_id", "statement", "claim_type", "verdict", "facts", "inference", "conclusion",
            "supporting_evidence_ids", "contradicting_evidence_ids", "confidence",
            "limitations", "invalidation_conditions", "watchpoints",
        ]))
        self.assertTrue(claim["facts"])
        self.assertTrue(claim["inference"])
        self.assertTrue(claim["conclusion"])
        for fact in claim["facts"]:
            self.assertEqual(sorted(fact), ["evidence_ids", "statement"])
            self.assertNotIn(fact["statement"], (claim["inference"], claim["conclusion"]))

    def test_related_claim_ids_are_written_back_only_when_asked(self):
        evidence, signals = self._inputs()
        graph = build_claim_graph("ETH", "近期動能？", evidence, signals=signals)

        self.assertTrue(all(item.related_claim_ids == [] for item in evidence))
        updated = apply_related_claim_ids(evidence, graph)

        self.assertEqual(updated, 4)
        self.assertEqual(next(item for item in evidence if item.evidence_id == "EV-M1").related_claim_ids,
                         ["CL-001"])
        self.assertEqual(graph["related_claim_ids"]["EV-S1"], ["CL-001"])


class PlanScopeFloorTests(unittest.TestCase):
    """plan 不得靠縮小 required_domains 間接抬高 confidence。

    `required_domains` 來自 research plan，而 plan 可能由模型產生。它是 domain coverage 的
    分母，所以「少要求幾個領域」原本可以直接推高 confidence —— 那等於讓 LLM 決定分數。
    這組測試把分母下限釘住。
    """

    def _two_domain_case(self, required_domains):
        """同一批證據（market 支持、news 反對），只改 plan 要求的領域。"""
        pool = EvidencePool([
            make_evidence("EV-M1", "market", lineage="market-api"),
            make_evidence("EV-N1", "news", lineage="major-media"),
        ])
        return evaluate_claim(
            pool,
            supporting_ids=["EV-M1"],
            contradicting_ids=["EV-N1"],
            required_domains=required_domains,
        )

    def test_narrowing_the_plan_cannot_raise_confidence(self):
        wide = self._two_domain_case(["market", "news", "social", "onchain"])
        narrow = self._two_domain_case(["market"])

        self.assertLessEqual(
            narrow["confidence"]["score"], wide["confidence"]["score"],
            "縮小 required_domains 不得提高 confidence：這會讓 plan（可能來自 LLM）間接決定分數",
        )

    def test_single_domain_plan_is_padded_to_the_floor(self):
        result = self._two_domain_case(["market"])

        self.assertEqual(len(result["required_domains"]), MIN_REQUIRED_DOMAIN_COUNT)
        self.assertEqual(result["plan_requested_domains"], ["market"])
        self.assertIn(LIMITER_PLAN_SCOPE_FLOOR, result["confidence"]["limiters"])
        # 兩個領域對照補足後的三領域分母：不再是 1.0。
        self.assertLess(result["confidence"]["components"]["domain_coverage"], 1.0)

    def test_one_covered_domain_can_never_clear_the_coverage_floor(self):
        """MIN_REQUIRED_DOMAIN_COUNT 的選值理由：1 / 3 必定低於 MIN_DOMAIN_COVERAGE。"""
        pool = EvidencePool([make_evidence("EV-M1", "market", lineage="market-api")])
        result = evaluate_claim(pool, supporting_ids=["EV-M1"], required_domains=["market"])

        self.assertLess(result["confidence"]["components"]["domain_coverage"], MIN_DOMAIN_COVERAGE)
        self.assertIn(LIMITER_LOW_DOMAIN_COVERAGE, result["confidence"]["limiters"])
        self.assertEqual(result["verdict"], VERDICT_INSUFFICIENT_EVIDENCE)

    def test_plan_at_or_above_the_floor_is_left_untouched(self):
        """既有意圖不變：題目不相關的領域本來就不該扣分。"""
        result = self._two_domain_case(["market", "news", "social"])

        self.assertEqual(result["required_domains"], ["market", "news", "social"])
        self.assertEqual(result["plan_requested_domains"], ["market", "news", "social"])
        self.assertNotIn(LIMITER_PLAN_SCOPE_FLOOR, result["confidence"]["limiters"])
        self.assertEqual(result["confidence"]["components"]["domain_coverage"], 0.6667)

    def test_padding_is_deterministic(self):
        first = self._two_domain_case(["derivatives"])
        second = self._two_domain_case(["derivatives"])

        self.assertEqual(first["required_domains"], second["required_domains"])
        self.assertEqual(first["confidence"]["score"], second["confidence"]["score"])

    def test_floor_limiter_is_a_known_limiter(self):
        self.assertIn(LIMITER_PLAN_SCOPE_FLOOR, KNOWN_LIMITERS)


if __name__ == "__main__":
    unittest.main()


class MixedDirectionEvidenceTests(unittest.TestCase):
    """一筆證據同時產生多空訊號時，不得同時進入支持側與反對側。

    背景（實測崩潰）：訊號比證據細，同一筆證據可以給出方向相反的多個訊號。live BNB 的
    `EV-VEGAS-BNB-001` 就同時產生「4H 通道空頭排列」（bear, 1.2）與「1H RSI 17.76 超賣，
    具技術性反彈條件」（bull, 1.0）—— 中期看空、短期超賣，正常市況。

    舊的 `fallback_claims()` 把兩側的 evidence_id 直接各自送進 supporting／contradicting，
    於是同一個 ID 出現在兩邊，`evaluate_claim()` 的互斥檢查拋 ValueError，**整個 live run
    產不出任何報告**。這不是降級，是硬失敗。
    """

    def _signals(self, *specs):
        return [{"evidence_id": evidence_id, "side": side, "weight": weight, "text": text}
                for evidence_id, side, weight, text in specs]

    def _evidence(self):
        return [
            make_evidence("EV-VEGAS", "vegas_channel", lineage="binance-klines"),
            make_evidence("EV-M1", "market", lineage="market-api"),
            make_evidence("EV-N1", "news", lineage="major-media"),
        ]

    def test_a_two_sided_evidence_no_longer_crashes_the_run(self):
        """這組訊號複現 live BNB 的形狀；修法前這裡會拋 ValueError。"""
        signals = self._signals(
            ("EV-VEGAS", "bear", 1.2, "4H Vegas 通道為空頭排列"),
            ("EV-VEGAS", "bull", 1.0, "1H RSI 超賣，具技術性反彈條件"),
            ("EV-N1", "bear", 0.9, "新聞面偏空"),
        )
        graph = build_claim_graph("BNB", "近期市場狀況？", self._evidence(), signals=signals)

        self.assertTrue(graph["claims"])
        claim = graph["claims"][0]
        overlap = set(claim["supporting_evidence_ids"]) & set(claim["contradicting_evidence_ids"])
        self.assertFalse(overlap, "同一筆證據不得同時支持與反對同一個 Claim")

    def test_the_evidence_is_assigned_to_its_heavier_side(self):
        signals = self._signals(
            ("EV-VEGAS", "bear", 1.2, "4H 空頭排列"),
            ("EV-VEGAS", "bull", 1.0, "1H 超賣反彈"),
            ("EV-N1", "bear", 0.9, "新聞面偏空"),
        )
        claim = build_claim_graph("BNB", "q", self._evidence(), signals=signals)["claims"][0]

        # 空方權重（1.2 + 0.9）較大，因此支持側是空方；EV-VEGAS 的淨權重也偏空。
        self.assertIn("EV-VEGAS", claim["supporting_evidence_ids"])
        self.assertNotIn("EV-VEGAS", claim["contradicting_evidence_ids"])

    def test_an_evenly_split_evidence_is_claimed_by_neither_side(self):
        """兩側權重相同時無法判斷它偏哪一側，宣稱它支持任一側都不誠實。"""
        signals = self._signals(
            ("EV-VEGAS", "bear", 1.0, "4H 空頭排列"),
            ("EV-VEGAS", "bull", 1.0, "1H 超賣反彈"),
            ("EV-M1", "bear", 1.5, "價格動能轉弱"),
        )
        claim = build_claim_graph("BNB", "q", self._evidence(), signals=signals)["claims"][0]

        self.assertNotIn("EV-VEGAS", claim["supporting_evidence_ids"])
        self.assertNotIn("EV-VEGAS", claim["contradicting_evidence_ids"])

    def test_the_internal_disagreement_is_disclosed_not_hidden(self):
        """歸邊不能靜靜發生：讀者要知道那筆證據本身就不一致。"""
        signals = self._signals(
            ("EV-VEGAS", "bear", 1.2, "4H 空頭排列"),
            ("EV-VEGAS", "bull", 1.0, "1H 超賣反彈"),
            ("EV-N1", "bear", 0.9, "新聞面偏空"),
        )
        claim = build_claim_graph("BNB", "q", self._evidence(), signals=signals)["claims"][0]

        limitations = " ".join(claim["limitations"])
        self.assertIn("EV-VEGAS", limitations)
        self.assertIn("同時產生多空兩側訊號", limitations)
        # 反向訊號的內容仍必須出現在觀察重點，不得因為歸邊就消失。
        self.assertIn("1H 超賣反彈", " ".join(claim["watchpoints"]))

    def test_single_sided_evidence_behaviour_is_unchanged(self):
        signals = self._signals(
            ("EV-M1", "bull", 1.0, "價格動能為正"),
            ("EV-N1", "bear", 0.6, "新聞面偏空"),
        )
        claim = build_claim_graph("ETH", "q", self._evidence(), signals=signals)["claims"][0]

        self.assertEqual(claim["supporting_evidence_ids"], ["EV-M1"])
        self.assertEqual(claim["contradicting_evidence_ids"], ["EV-N1"])
        self.assertNotIn("同時產生多空兩側訊號", " ".join(claim["limitations"]))

    def test_assignment_is_deterministic(self):
        signals = self._signals(
            ("EV-VEGAS", "bear", 1.2, "4H 空頭排列"),
            ("EV-VEGAS", "bull", 1.0, "1H 超賣反彈"),
            ("EV-N1", "bear", 0.9, "新聞面偏空"),
        )
        first = build_claim_graph("BNB", "q", self._evidence(), signals=signals)["claims"][0]
        second = build_claim_graph("BNB", "q", self._evidence(), signals=signals)["claims"][0]

        self.assertEqual(first["supporting_evidence_ids"], second["supporting_evidence_ids"])
        self.assertEqual(first["contradicting_evidence_ids"], second["contradicting_evidence_ids"])
