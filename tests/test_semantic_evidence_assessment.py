"""E2：每筆 Evidence 相對**使用者這一題**的可稽核語意評估。

`tests/test_t3_credibility_wiring.py` 驗的是「這個來源多可信」；這個檔案驗的是完全不同的另一
件事：「這筆證據對這個問題有多相關」。兩者混用是本 Task 要修的核心問題 —— 一則可信度 0.9 的
新聞對題目可能完全不相關，而報告會用來源品質冒充問題相關性。

驗四層：

* **契約**：五個標籤的分數映射、`effective_weight` 公式、子項 ID 的穩定性；
* **邊界**：模型只給標籤，數值一律由 Python 算；任何一處不合法就**整批**作廢；
* **誠實**：讀不懂新聞語意時必須 `unclear`，不可自動給方向；缺作者／發布時間留 `None`，
  不得以 `fetched_at` 代填；
* **不加成本**：評估搭既有分析回應的便車，模型呼叫次數必須與沒有評估時完全相同。

全部離線執行：模型一律走注入的 client，不觸網路。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch

from src.claim_graph import (DEFAULT_CLAIM_RELEVANCE, EvidencePool, LIMITER_CONTEXT_ONLY,
                            evaluate_claim, normalize_evidence)
from src.day1_mvp import Evidence
from src.day2_sources import (_parse_feed_entries, _time_semantics, normalise_source_items,
                              stamp_credibility_metadata)
from src.llm import (ANALYSIS_SCHEMA, EVIDENCE_ASSESSMENT_FIELD,
                     EVIDENCE_ASSESSMENT_RESULT_FIELD, _strict_json_schema, build_prompt,
                     normalise_analysis_assessments)
from src.orchestrator import (ASSESSMENT_PATH_FALLBACK, ASSESSMENT_PATH_LLM,
                              assess_evidence_relevance, run)
from src.schemas import (ARTIFACT_FILENAMES, CONFIDENCE_COMPONENT_KEYS,
                         CONFIDENCE_TYPE_HEURISTIC, CREDIBILITY_COMPONENT_KEYS,
                         GATE_SEVERITY_ERROR, GATE_SEVERITY_WARNING,
                         GATE_STATUS_FAIL, IMPACT_DIRECTIONS, IMPACT_HORIZONS,
                         MAX_ASSESSMENT_SOURCE_ITEM_IDS, QUESTION_RELATIONSHIPS,
                         RELEVANCE_LABEL_SCORES, RELEVANCE_LABELS, SOURCE_ITEM_KEYS,
                         effective_weight, relevance_score_for, source_item_id,
                         source_item_parent_id)
from src.validation import (run_citation_gate, validate_evidence, validate_evidence_assessments)


NOW = datetime(2026, 8, 1, 12, 0, tzinfo=timezone.utc)
FETCHED_AT = NOW.isoformat()
RUN_ID = "20260801T000000Z-e2test"
PLAN = {"time_window": {"days": 14, "source": "default"}}
# `src/llm.py` 送給稽核的 schema 名稱；spy 用它分辨分析呼叫與稽核呼叫。
CRITIC_SCHEMA_NAME = "research_critique"


def iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def make_evidence(evidence_id="EV-1", data_type="market", *, coin="ETH", score=0.90,
                  source_type="market_api", content=None, published_at=None, event_time=None,
                  independence=1.0, run_id=RUN_ID, related_claim_ids=("CL-001",)) -> Evidence:
    """一筆已計分、已標記 run 的證據；每個測試只調整它關心的維度。"""
    evidence = Evidence(
        evidence_id, "TestSource", f"https://example.com/{evidence_id}", FETCHED_AT,
        data_type, coin, "14d", dict(content or {"note": "fixture"}), score,
        {"endpoint": f"https://example.com/{evidence_id}"}, f"{coin} {data_type} evidence",
        source_type=source_type,
        verification_status="unverified",
        source_lineage_id=evidence_id,
        independence_factor=independence,
        score_breakdown={
            "raw_score": score, "final_score": score, "hard_cap": 1.0,
            "components": {key: score for key in CREDIBILITY_COMPONENT_KEYS},
            "score_limiters": [], "scoring_version": "credibility-v1",
        },
        related_claim_ids=list(related_claim_ids),
        scoring_version="credibility-v1",
        run_id=run_id,
        published_at=published_at,
        event_time=event_time,
    )
    evidence.source_items = normalise_source_items(evidence)
    return evidence


def news_evidence(evidence_id="EV-NEWS-001", *, item_count=2, author=None, published=None,
                  source_type="major_media", score=0.90) -> Evidence:
    items = [{"title": f"ETH 報導 {index}", "url": f"https://news.example.com/{index}",
              "published": published, "author": author, "summary": "內文摘要"}
             for index in range(1, item_count + 1)]
    return make_evidence(evidence_id, "news", score=score, source_type=source_type,
                         content={"items": items, "summarised_count": item_count})


def assess(evidence, *, coin="ETH", signals=(), proposals=None, plan=None, now=NOW) -> dict:
    return assess_evidence_relevance(evidence, coin=coin, signals=list(signals),
                                     plan_dict=plan if plan is not None else PLAN,
                                     proposals=proposals, now=now)


def proposal(evidence_id, label="direct", *, relationship="support", impact="bullish",
             horizon="short_term", rationale="直接回答題目。", source_item_ids=(), **extra) -> dict:
    payload = {
        "evidence_id": evidence_id,
        "relevance_label": label,
        "relationship_to_question": relationship,
        "impact_direction": impact,
        "impact_horizon": horizon,
        "rationale": rationale,
        "source_item_ids": list(source_item_ids),
    }
    payload.update(extra)
    return payload


def analysis_payload(*, evidence_ids=(), assessments=None) -> dict:
    """一份合法的分析回應；`assessments` 為 None 時完全不帶評估欄位。"""
    payload = {
        "market_judgment": "偏多", "confidence": 0.5,
        "facts": [f"{item}: 已取得資料。" for item in evidence_ids],
        "inferences": ["訊號偏多。"], "conclusion": "本次證據偏多。",
        "counter_evidence": ["社群樣本不足。"],
        "observation_points": ["追蹤成交量是否在下週維持，否則本次判讀失效。"],
        "cited_evidence_ids": list(evidence_ids),
    }
    if assessments is not None:
        payload[EVIDENCE_ASSESSMENT_FIELD] = assessments
    return payload


class SpyClient:
    """記錄每一次 `generate_json` 的注入 client；同一個實例可同時當分析與稽核用。"""

    def __init__(self, analysis: dict) -> None:
        self.analysis = analysis
        self.calls = []

    def generate_json(self, *, prompt, schema, schema_name, timeout_seconds):
        self.calls.append(schema_name)
        if schema_name == CRITIC_SCHEMA_NAME:
            return {"verdict": "pass", "summary": "已檢查引用與分層。",
                    "confidence_adjustment": 0, "findings": []}
        return dict(self.analysis)


# --------------------------------------------------------------------------------------
# 契約：標籤 → 分數 → 權重
# --------------------------------------------------------------------------------------

class RelevanceMappingTests(unittest.TestCase):
    def test_five_labels_map_to_the_frozen_scores(self):
        self.assertEqual(dict(RELEVANCE_LABEL_SCORES), {
            "direct": 1.0, "indirect": 0.65, "context": 0.35, "irrelevant": 0.0, "unclear": 0.20,
        })
        self.assertEqual(set(RELEVANCE_LABELS), set(RELEVANCE_LABEL_SCORES))
        for label, expected in RELEVANCE_LABEL_SCORES.items():
            self.assertEqual(relevance_score_for(label), expected, label)

    def test_effective_weight_is_the_product_of_three_factors(self):
        self.assertEqual(effective_weight(0.80, 1.0, 1.0), 0.80)
        self.assertEqual(effective_weight(0.80, 0.65, 1.0), 0.52)
        self.assertEqual(effective_weight(0.80, 0.35, 0.5), 0.14)
        self.assertEqual(effective_weight(0.90, 0.0, 1.0), 0.0)

    def test_effective_weight_is_clamped_and_never_raises(self):
        self.assertEqual(effective_weight(5.0, 5.0, 5.0), 1.0)
        self.assertEqual(effective_weight(-1.0, 1.0, 1.0), 0.0)
        self.assertEqual(effective_weight(None, "x", object()), 0.0)
        self.assertEqual(effective_weight(float("nan"), 1.0, 1.0), 0.0)

    def test_every_label_produces_a_reproducible_assessment_and_weight(self):
        for label, expected in RELEVANCE_LABEL_SCORES.items():
            evidence = make_evidence("EV-M", "market", score=0.80)
            assess([evidence], proposals=[proposal("EV-M", label, relationship="context",
                                                   impact="neutral", horizon="short_term")])
            assessment = evidence.semantic_assessment
            self.assertEqual(assessment["relevance_label"], label)
            self.assertEqual(assessment["relevance_score"], expected, label)
            self.assertEqual(assessment["effective_weight"], round(0.80 * expected, 4), label)
            self.assertEqual(validate_evidence([evidence], [], require_scored=True), [], label)


class WeightSeparatesCredibilityFromRelevanceTests(unittest.TestCase):
    """來源可信度與問題相關性是兩個維度；混用是 E2 要修的核心問題。"""

    def test_high_credibility_but_irrelevant_news_carries_no_weight(self):
        evidence = news_evidence("EV-NEWS-001", score=0.95)
        assess([evidence], proposals=[proposal(
            "EV-NEWS-001", "irrelevant", relationship="irrelevant", impact="not_applicable",
            horizon="unclear", rationale="報導談的是另一個標的的監管進度。")])

        self.assertEqual(evidence.reliability_score, 0.95, "來源可信度不該被相關性改寫")
        self.assertEqual(evidence.semantic_assessment["effective_weight"], 0.0)
        self.assertTrue(evidence.semantic_assessment["excluded_reason"])

    def test_low_credibility_but_direct_evidence_is_still_held_down_by_reliability(self):
        evidence = make_evidence("EV-S", "social", score=0.35, source_type="social_public")
        assess([evidence], proposals=[proposal("EV-S", "direct")])

        # 相關性拉不動可信度：direct（1.0）× 0.35 仍然是 0.35。
        self.assertEqual(evidence.semantic_assessment["relevance_score"], 1.0)
        self.assertEqual(evidence.semantic_assessment["effective_weight"], 0.35)

    def test_independence_dilution_still_applies(self):
        evidence = make_evidence("EV-N", "news", score=0.80, independence=0.5,
                                 content={"items": [{"title": "t", "url": "https://a.example/1"}]})
        assess([evidence], proposals=[proposal("EV-N", "indirect")])
        self.assertEqual(evidence.semantic_assessment["effective_weight"], round(0.8 * 0.65 * 0.5, 4))


# --------------------------------------------------------------------------------------
# 明確的 0 必須一路保持 0
# --------------------------------------------------------------------------------------

class ExplicitIrrelevanceTests(unittest.TestCase):
    def test_missing_assessment_still_gets_the_conservative_default(self):
        """沒有評估過的證據行為完全不變：`claim_relevance` 是 0.0，但那代表缺值。"""
        record = normalize_evidence([make_evidence("EV-1", "market")])[0]
        self.assertEqual(record["claim_relevance"], DEFAULT_CLAIM_RELEVANCE)
        self.assertEqual(record["relevance_label"], "")

    def test_explicit_irrelevant_stays_zero_and_never_returns_to_the_default(self):
        evidence = news_evidence("EV-N")
        assess([evidence], proposals=[proposal(
            "EV-N", "irrelevant", relationship="irrelevant", impact="not_applicable",
            horizon="unclear", rationale="與題目無關。")])

        record = normalize_evidence([evidence])[0]
        self.assertEqual(evidence.claim_relevance, 0.0)
        self.assertEqual(record["claim_relevance"], 0.0)
        self.assertNotEqual(record["claim_relevance"], DEFAULT_CLAIM_RELEVANCE)

    def test_irrelevant_evidence_cannot_be_cited_by_a_claim(self):
        relevant = make_evidence("EV-M", "market")
        irrelevant = news_evidence("EV-N")
        assess([relevant, irrelevant], proposals=[
            proposal("EV-M", "direct"),
            proposal("EV-N", "irrelevant", relationship="irrelevant", impact="not_applicable",
                     horizon="unclear", rationale="與題目無關。"),
        ])
        pool = EvidencePool([relevant, irrelevant])

        self.assertEqual(pool.active_ids, ["EV-M"])
        self.assertEqual(pool.irrelevant_ids, ["EV-N"])
        with self.assertRaisesRegex(ValueError, "irrelevant to the question"):
            pool.require(["EV-N"])

    def test_irrelevant_evidence_is_kept_in_the_record_with_a_reason(self):
        evidence = news_evidence("EV-N")
        summary = assess([evidence], proposals=[proposal(
            "EV-N", "irrelevant", relationship="irrelevant", impact="not_applicable",
            horizon="unclear", rationale="談的是另一條鏈。")])

        self.assertEqual(summary["excluded_evidence_ids"], ["EV-N"])
        self.assertIn("EV-N", summary["excluded_reasons"])
        # 排除不等於刪除：紀錄仍然完整並通過既有的證據契約。
        self.assertEqual(validate_evidence([evidence], [], require_scored=True), [])

    def test_context_only_support_cannot_carry_a_claim_on_its_own(self):
        macro = make_evidence("EV-MACRO", "macro", score=0.85)
        onchain = make_evidence("EV-CHAIN", "onchain", score=0.80, source_type="blockchain_raw")
        assess([macro, onchain], proposals=[
            proposal("EV-MACRO", "context", relationship="context", impact="neutral",
                     horizon="medium_term"),
            proposal("EV-CHAIN", "direct"),
        ])
        pool = EvidencePool([macro, onchain])

        context_only = evaluate_claim(pool, supporting_ids=["EV-MACRO"])
        self.assertIn(LIMITER_CONTEXT_ONLY, context_only["confidence"]["limiters"])
        self.assertEqual(context_only["verdict"], "insufficient_evidence")

        with_direct = evaluate_claim(pool, supporting_ids=["EV-MACRO", "EV-CHAIN"])
        self.assertNotIn(LIMITER_CONTEXT_ONLY, with_direct["confidence"]["limiters"])


# --------------------------------------------------------------------------------------
# 模型只給標籤；任何一處不合法就整批作廢
# --------------------------------------------------------------------------------------

class ModelPayloadValidationTests(unittest.TestCase):
    def setUp(self):
        self.market = make_evidence("EV-M", "market")
        self.news = news_evidence("EV-N")
        self.evidence = [self.market, self.news]

    def _fallback_for(self, proposals) -> dict:
        summary = assess(self.evidence, proposals=proposals)
        self.assertEqual(summary["path"], ASSESSMENT_PATH_FALLBACK)
        self.assertTrue(summary["fallback_reason"])
        for item in self.evidence:
            self.assertEqual(item.semantic_assessment["assessment_source"],
                             "deterministic_fallback")
        return summary

    def test_a_complete_valid_payload_is_accepted(self):
        summary = assess(self.evidence, proposals=[
            proposal("EV-M", "direct"),
            proposal("EV-N", "indirect", source_item_ids=["EV-N-ITEM-01"]),
        ])
        self.assertEqual(summary["path"], ASSESSMENT_PATH_LLM)
        self.assertEqual(summary["fallback_reason"], "")
        self.assertEqual(self.news.semantic_assessment["source_item_ids"], ["EV-N-ITEM-01"])

    def test_an_accepted_payload_without_item_ids_gets_a_deterministic_locator(self):
        """模型漏填子項不該讓聚合引用失去 locator，進而讓整份報告發不出去。"""
        summary = assess(self.evidence, proposals=[
            proposal("EV-M", "direct"), proposal("EV-N", "indirect", source_item_ids=[])])

        self.assertEqual(summary["path"], ASSESSMENT_PATH_LLM)
        self.assertEqual(self.news.semantic_assessment["source_item_ids"],
                         ["EV-N-ITEM-01", "EV-N-ITEM-02"])
        # 標籤仍然是模型的；補上的只有機械性的子項選擇。
        self.assertEqual(self.news.semantic_assessment["assessment_source"], "llm")

    def test_unknown_evidence_id_discards_the_whole_batch(self):
        summary = self._fallback_for([proposal("EV-M", "direct"), proposal("EV-404", "direct"),
                                      proposal("EV-N", "indirect")])
        self.assertIn("EV-404", summary["fallback_reason"])

    def test_a_missing_evidence_id_discards_the_whole_batch(self):
        summary = self._fallback_for([proposal("EV-M", "direct")])
        self.assertIn("does not cover every evidence id", summary["fallback_reason"])

    def test_duplicate_evidence_id_discards_the_whole_batch(self):
        summary = self._fallback_for([proposal("EV-M", "direct"), proposal("EV-M", "context"),
                                      proposal("EV-N", "indirect")])
        self.assertIn("repeats evidence id", summary["fallback_reason"])

    def test_source_item_id_from_another_parent_discards_the_whole_batch(self):
        summary = self._fallback_for([
            proposal("EV-M", "direct"),
            proposal("EV-N", "indirect", source_item_ids=["EV-M-ITEM-01"]),
        ])
        self.assertIn("does not belong to it", summary["fallback_reason"])

    def test_illegal_enum_discards_the_whole_batch(self):
        for key, bad in (("relevance_label", "very_relevant"),
                         ("relationship_to_question", "agrees"),
                         ("impact_direction", "up"),
                         ("impact_horizon", "someday")):
            with self.subTest(key=key):
                entry = proposal("EV-N", "indirect")
                entry[key] = bad
                summary = self._fallback_for([proposal("EV-M", "direct"), entry])
                self.assertIn(key, summary["fallback_reason"])

    def test_a_model_supplied_number_discards_the_whole_batch(self):
        for key in ("relevance_score", "effective_weight", "reliability_score", "confidence"):
            with self.subTest(key=key):
                summary = self._fallback_for([
                    proposal("EV-M", "direct", **{key: 0.99}), proposal("EV-N", "indirect")])
                self.assertIn(key, summary["fallback_reason"])

    def test_too_many_source_item_ids_is_rejected(self):
        news = news_evidence("EV-N", item_count=5)
        item_ids = [item["source_item_id"] for item in news.source_items]
        errors = validate_evidence_assessments(
            [proposal("EV-N", "indirect", source_item_ids=item_ids)], [news])
        self.assertTrue(any("at most" in error for error in errors))
        self.assertGreater(len(item_ids), MAX_ASSESSMENT_SOURCE_ITEM_IDS)

    def test_an_empty_payload_is_reported_rather_than_silently_accepted(self):
        self.assertTrue(validate_evidence_assessments([], self.evidence))
        self.assertTrue(validate_evidence_assessments(None, self.evidence))


class AssessmentContractTests(unittest.TestCase):
    """寫進 evidence.json 的評估必須自己解釋得通，而不是靠寫入者自律。"""

    def _assessed(self) -> Evidence:
        evidence = make_evidence("EV-M", "market", score=0.80)
        assess([evidence], proposals=[proposal("EV-M", "indirect")])
        return evidence

    def test_a_relevance_score_that_does_not_match_its_label_is_rejected(self):
        evidence = self._assessed()
        evidence.semantic_assessment["relevance_score"] = 0.95
        errors = validate_evidence([evidence], [], require_scored=True)
        self.assertTrue(any("does not match" in error for error in errors), errors)

    def test_a_hand_edited_effective_weight_is_rejected(self):
        evidence = self._assessed()
        evidence.semantic_assessment["effective_weight"] = 0.99
        errors = validate_evidence([evidence], [], require_scored=True)
        self.assertTrue(any("effective_weight" in error for error in errors), errors)

    def test_an_unknown_enum_value_is_rejected(self):
        evidence = self._assessed()
        evidence.semantic_assessment["impact_horizon"] = "eventually"
        self.assertTrue(validate_evidence([evidence], [], require_scored=True))

    def test_a_source_item_id_outside_this_evidence_is_rejected(self):
        evidence = self._assessed()
        evidence.semantic_assessment["source_item_ids"] = ["EV-OTHER-ITEM-01"]
        errors = validate_evidence([evidence], [], require_scored=True)
        self.assertTrue(any("not items of this evidence" in error for error in errors), errors)

    def test_an_over_long_rationale_is_rejected(self):
        evidence = self._assessed()
        evidence.semantic_assessment["rationale"] = "很" * 200
        self.assertTrue(validate_evidence([evidence], [], require_scored=True))

    def test_an_excluded_reason_without_an_irrelevant_label_is_rejected(self):
        evidence = self._assessed()
        evidence.semantic_assessment["excluded_reason"] = "偷偷排除"
        self.assertTrue(validate_evidence([evidence], [], require_scored=True))

    def test_unassessed_evidence_passes_the_contract(self):
        self.assertEqual(validate_evidence([make_evidence()], [], require_scored=True), [])

    def test_a_duplicate_source_item_id_is_rejected(self):
        evidence = news_evidence("EV-N")
        evidence.source_items[1]["source_item_id"] = evidence.source_items[0]["source_item_id"]
        errors = validate_evidence([evidence], [], require_scored=True)
        self.assertTrue(any("duplicate source_item_id" in error for error in errors), errors)

    def test_a_source_item_id_that_does_not_name_its_parent_is_rejected(self):
        evidence = news_evidence("EV-N")
        evidence.source_items[0]["source_item_id"] = "EV-OTHER-ITEM-01"
        errors = validate_evidence([evidence], [], require_scored=True)
        self.assertTrue(any("does not belong to this evidence" in error for error in errors), errors)


# --------------------------------------------------------------------------------------
# Deterministic fallback：看不懂就說看不懂
# --------------------------------------------------------------------------------------

class DeterministicFallbackTests(unittest.TestCase):
    def test_news_with_only_titles_is_unclear_not_directional(self):
        evidence = news_evidence("EV-N")
        assess([evidence])
        assessment = evidence.semantic_assessment

        self.assertEqual(assessment["relevance_label"], "unclear")
        self.assertEqual(assessment["relevance_score"], 0.20)
        self.assertEqual(assessment["impact_direction"], "unclear")
        self.assertEqual(assessment["relationship_to_question"], "unclear")
        self.assertNotIn(assessment["impact_direction"], ("bullish", "bearish"))

    def test_announcement_is_unclear_rather_than_a_fixed_neutral(self):
        evidence = make_evidence("EV-A", "announcement", source_type="official_announcement",
                                 content={"items": [{"title": "協議升級發布",
                                                     "url": "https://a.example/1"}],
                                          "first_party": True})
        assess([evidence])
        self.assertEqual(evidence.semantic_assessment["relevance_label"], "unclear")

    def test_structured_market_data_takes_its_direction_from_the_signal_inventory(self):
        evidence = make_evidence("EV-M", "market")
        assess([evidence], signals=[{"side": "bull", "evidence_id": "EV-M", "weight": 1.0,
                                     "text": "期間報酬為正"}])
        assessment = evidence.semantic_assessment

        self.assertEqual(assessment["relevance_label"], "direct")
        self.assertEqual(assessment["impact_direction"], "bullish")
        # 離線規則讀不懂題目，因此不宣稱支持或反對，只說它提供依據。
        self.assertEqual(assessment["relationship_to_question"], "context")

    def test_two_sided_signals_on_one_record_are_reported_as_mixed(self):
        evidence = make_evidence("EV-V", "vegas_channel")
        assess([evidence], signals=[
            {"side": "bear", "evidence_id": "EV-V", "weight": 1.2, "text": "4H 空頭排列"},
            {"side": "bull", "evidence_id": "EV-V", "weight": 1.0, "text": "1H RSI 超賣"},
        ])
        self.assertEqual(evidence.semantic_assessment["impact_direction"], "mixed")

    def test_social_sentiment_gives_an_indirect_reading_not_a_direct_one(self):
        evidence = make_evidence("EV-S", "social", source_type="social_public", score=0.45,
                                 content={"platform": "reddit", "sentiment": "negative",
                                          "post_count": 4, "positive_terms": 1,
                                          "negative_terms": 3,
                                          "posts": [{"title": "ETH dump", "url": "https://s.example/1"}]})
        assess([evidence])
        assessment = evidence.semantic_assessment

        self.assertEqual(assessment["relevance_label"], "indirect")
        self.assertEqual(assessment["impact_direction"], "bearish")

    def test_social_without_a_usable_sample_is_unclear(self):
        evidence = make_evidence("EV-S", "social", source_type="social_public",
                                 content={"platform": "reddit", "sentiment": "mixed",
                                          "post_count": 0, "posts": []})
        assess([evidence])
        self.assertEqual(evidence.semantic_assessment["relevance_label"], "unclear")

    def test_a_coin_mismatch_is_irrelevant_with_a_recorded_reason(self):
        evidence = make_evidence("EV-BTC", "market", coin="BTC")
        summary = assess([evidence], coin="ETH")
        assessment = evidence.semantic_assessment

        self.assertEqual(assessment["relevance_label"], "irrelevant")
        self.assertEqual(assessment["effective_weight"], 0.0)
        self.assertIn("BTC", assessment["excluded_reason"])
        self.assertEqual(summary["excluded_evidence_ids"], ["EV-BTC"])

    def test_evidence_older_than_the_plan_window_is_irrelevant_not_deleted(self):
        evidence = news_evidence("EV-OLD")
        evidence.published_at = iso(120)
        summary = assess([evidence])

        self.assertEqual(evidence.semantic_assessment["relevance_label"], "irrelevant")
        self.assertIn("超出 plan 時間窗", evidence.semantic_assessment["excluded_reason"])
        self.assertEqual(summary["assessed_count"], 1, "排除不等於從清單移除")

    def test_evidence_inside_the_plan_window_is_not_excluded(self):
        evidence = news_evidence("EV-NEW")
        evidence.published_at = iso(3)
        assess([evidence])
        self.assertNotEqual(evidence.semantic_assessment["relevance_label"], "irrelevant")

    def test_a_fallback_fixture_is_unclear_rather_than_stale(self):
        """fixture 裡的日期是佔位值，拿它比對研究區間等於讀出一個不存在的意思。"""
        evidence = make_evidence("EV-F", "market", source_type="fallback_fixture", score=0.20,
                                 event_time=iso(200))
        assess([evidence])
        self.assertEqual(evidence.semantic_assessment["relevance_label"], "unclear")
        self.assertEqual(evidence.semantic_assessment["excluded_reason"], "")

    def test_the_same_input_produces_the_same_assessment(self):
        first, second = news_evidence("EV-N"), news_evidence("EV-N")
        signals = [{"side": "bull", "evidence_id": "EV-N", "weight": 1.0, "text": "x"}]
        self.assertEqual(assess([first], signals=signals), assess([second], signals=signals))
        self.assertEqual(first.semantic_assessment, second.semantic_assessment)
        self.assertEqual(first.source_items, second.source_items)


# --------------------------------------------------------------------------------------
# 子項 locator
# --------------------------------------------------------------------------------------

class SourceItemTests(unittest.TestCase):
    def test_item_ids_are_stable_and_name_their_parent(self):
        evidence = news_evidence("EV-NEWS-001", item_count=3)
        ids = [item["source_item_id"] for item in evidence.source_items]

        self.assertEqual(ids, ["EV-NEWS-001-ITEM-01", "EV-NEWS-001-ITEM-02",
                               "EV-NEWS-001-ITEM-03"])
        self.assertEqual(ids, [source_item_id("EV-NEWS-001", index) for index in (1, 2, 3)])
        for item_id in ids:
            self.assertEqual(source_item_parent_id(item_id), "EV-NEWS-001")

    def test_every_item_carries_the_frozen_key_set(self):
        evidence = news_evidence("EV-N")
        for item in evidence.source_items:
            self.assertEqual(tuple(item), SOURCE_ITEM_KEYS)

    def test_missing_author_and_published_stay_none_rather_than_borrowing_fetched_at(self):
        evidence = news_evidence("EV-N", author=None, published=None)
        item = evidence.source_items[0]

        self.assertIsNone(item["author"])
        self.assertIsNone(item["published_at"])
        self.assertNotEqual(item["published_at"], evidence.fetched_at)
        self.assertNotIn(evidence.fetched_at, json.dumps(evidence.source_items))

    def test_a_provided_author_and_time_are_preserved(self):
        evidence = news_evidence("EV-N", author="記者 A", published="Fri, 31 Jul 2026 00:00:00 GMT")
        item = evidence.source_items[0]

        self.assertEqual(item["author"], "記者 A")
        self.assertTrue(item["published_at"].startswith("2026-07-31"))

    def test_posts_are_normalised_the_same_way_as_items(self):
        evidence = make_evidence("EV-S", "social_bluesky", source_type="social_public", content={
            "platform": "bluesky", "sentiment": "mixed", "post_count": 1,
            "posts": [{"text": "ETH 討論", "url": "https://bsky.app/p/1",
                       "created_at": "2026-07-31T10:00:00Z", "indexed_at": "2026-07-31T10:01:00Z",
                       "author_handle": "someone.bsky.social"}]})
        item = evidence.source_items[0]

        self.assertEqual(item["source_item_id"], "EV-S-ITEM-01")
        self.assertEqual(item["text"], "ETH 討論")
        self.assertEqual(item["author_handle"], "someone.bsky.social")
        self.assertEqual(item["platform"], "bluesky")
        self.assertTrue(item["created_at"].startswith("2026-07-31T10:00"))
        self.assertTrue(item["indexed_at"].startswith("2026-07-31T10:01"))
        self.assertIsNone(item["published_at"], "沒有 published 就不得從別的時間欄位代填")

    def test_records_without_items_or_posts_have_no_source_items(self):
        self.assertEqual(make_evidence("EV-M", "market").source_items, [])

    def test_stamping_twice_gives_the_same_ids(self):
        evidence = news_evidence("EV-N")
        first = list(evidence.source_items)
        stamp_credibility_metadata(evidence)
        self.assertEqual(evidence.source_items, first)


class FeedAuthorParsingTests(unittest.TestCase):
    def test_rss_dc_creator_is_read_as_the_author(self):
        raw = ("""<?xml version="1.0"?>
        <rss xmlns:dc="http://purl.org/dc/elements/1.1/"><channel><item>
          <title>ETH 上漲</title><link>https://news.example/1</link>
          <pubDate>Fri, 31 Jul 2026 00:00:00 GMT</pubDate>
          <description>內文</description><dc:creator>記者 B</dc:creator>
        </item></channel></rss>""").encode()
        entry = _parse_feed_entries(raw, limit=5)[0]
        self.assertEqual(entry["author"], "記者 B")

    def test_rss_without_an_author_returns_none(self):
        raw = (b"""<?xml version="1.0"?><rss><channel><item><title>t</title>"""
               b"""<link>https://news.example/1</link></item></channel></rss>""")
        self.assertIsNone(_parse_feed_entries(raw, limit=5)[0]["author"])

    def test_atom_author_name_is_read(self):
        raw = ("""<?xml version="1.0"?>
        <feed xmlns="http://www.w3.org/2005/Atom"><entry>
          <title>ETH thread</title><link href="https://reddit.example/1"/>
          <updated>2026-07-31T00:00:00Z</updated>
          <author><name>/u/someone</name></author>
        </entry></feed>""").encode()
        entry = _parse_feed_entries(raw, limit=5)[0]
        self.assertEqual(entry["author"], "/u/someone")


class SocialTimeSemanticsTests(unittest.TestCase):
    """三個社群平台各有自己的時間欄位；抓取時間不是發布時間。"""

    def _event_time(self, data_type: str, post: dict) -> str | None:
        evidence = make_evidence("EV-S", data_type, source_type="social_public",
                                 content={"platform": "x", "posts": [post]})
        return _time_semantics(evidence)[1]

    def test_reddit_published_is_used(self):
        self.assertTrue(self._event_time("social", {"published": "2026-07-30T00:00:00Z"})
                        .startswith("2026-07-30"))

    def test_bluesky_created_and_indexed_are_used(self):
        self.assertTrue(self._event_time("social_bluesky", {"created_at": "2026-07-29T00:00:00Z"})
                        .startswith("2026-07-29"))
        self.assertTrue(self._event_time("social_bluesky", {"indexed_at": "2026-07-28T00:00:00Z"})
                        .startswith("2026-07-28"))

    def test_hacker_news_created_at_is_used(self):
        self.assertTrue(self._event_time("social_hackernews", {"created_at": "2026-07-27T00:00:00Z"})
                        .startswith("2026-07-27"))

    def test_a_social_record_without_any_time_stays_none(self):
        self.assertIsNone(self._event_time("social_hackernews", {"text": "no timestamps"}))

    def test_fetched_at_is_never_promoted_to_the_event_time(self):
        evidence = make_evidence("EV-S", "social", source_type="social_public",
                                 content={"platform": "reddit", "posts": [{"title": "t"}]})
        stamp_credibility_metadata(evidence)
        self.assertIsNone(evidence.event_time)
        self.assertIsNone(evidence.published_at)


# --------------------------------------------------------------------------------------
# Citation Gate：聚合證據被引用時要指出是哪一則
# --------------------------------------------------------------------------------------

def gate_claim(**overrides) -> dict:
    claim = {
        "claim_id": "CL-001", "statement": "ETH 短期訊號偏多", "claim_type": "market_judgment",
        "verdict": "partially_supported",
        "facts": [{"statement": "近期新聞提及網路活動", "evidence_ids": ["EV-N"]}],
        "inference": "新聞面提供背景。", "conclusion": "本次證據偏多。",
        "supporting_evidence_ids": ["EV-N"], "contradicting_evidence_ids": ["EV-M"],
        "confidence": {"score": 0.50, "level": "medium", "type": CONFIDENCE_TYPE_HEURISTIC,
                       "components": {key: 0.5 for key in CONFIDENCE_COMPONENT_KEYS},
                       "limiters": []},
        "limitations": ["新聞面僅為背景。"], "invalidation_conditions": ["反向側超過支持側。"],
        "watchpoints": ["追蹤成交量。"],
    }
    claim.update(overrides)
    return claim


class CitedItemLocatorGateTests(unittest.TestCase):
    def _gate(self, news: Evidence) -> dict:
        market = make_evidence("EV-M", "market", related_claim_ids=["CL-001"])
        assess([news, market], signals=[{"side": "bull", "evidence_id": "EV-M", "weight": 1.0,
                                         "text": "x"}])
        return run_citation_gate(RUN_ID, [news, market], [gate_claim()])

    def test_a_named_item_with_an_http_locator_passes(self):
        result = self._gate(news_evidence("EV-N", author="記者 A", published=iso(2)))
        self.assertEqual(result["checks"]["cited_item_locator"], "pass")

    def test_an_aggregate_without_a_named_item_fails_the_gate(self):
        news = news_evidence("EV-N", author="記者 A", published=iso(2))
        assess([news])
        news.semantic_assessment["source_item_ids"] = []
        market = make_evidence("EV-M", "market")
        result = run_citation_gate(RUN_ID, [news, market], [gate_claim()])

        self.assertEqual(result["status"], GATE_STATUS_FAIL)
        self.assertEqual(result["checks"]["cited_item_locator"], "fail")
        self.assertTrue(any("哪一則" in message for message in result["errors"]))

    def test_a_named_item_without_its_own_url_only_warns(self):
        """子項已被指名、母來源仍可回溯，缺的是那一則自己的網址 —— 資料品質問題，不阻擋發佈。"""
        news = news_evidence("EV-N", author="記者 A", published=iso(2))
        for item in news.source_items:
            item["url"] = None
        result = self._gate(news)

        self.assertEqual(result["checks"]["cited_item_locator"], "warn")
        self.assertEqual(result["error_count"], 0)
        self.assertTrue(any("沒有自己的 http(s) URL" in message for message in result["warnings"]))

    def test_missing_author_or_published_is_only_a_warning(self):
        result = self._gate(news_evidence("EV-N", author=None, published=None))

        self.assertEqual(result["checks"]["cited_item_locator"], "warn")
        self.assertEqual(result["error_count"], 0)
        self.assertTrue(any("已存 null" in message for message in result["warnings"]))

    def test_a_degraded_aggregate_only_warns(self):
        """採集失敗的降級紀錄缺子項是採集結果，不是可追溯性瑕疵。"""
        news = news_evidence("EV-N", source_type="fallback_fixture", score=0.20)
        news.verification_status = "unavailable"
        news.score_limiters = ["fallback_fixture"]
        news.score_breakdown = {"final_score": 0.20, "score_limiters": ["fallback_fixture"]}
        assess([news])
        news.semantic_assessment["source_item_ids"] = []
        market = make_evidence("EV-M", "market")
        result = run_citation_gate(RUN_ID, [news, market], [gate_claim()])

        severities = [item["severity"] for item in result["findings"]
                      if item["check"] == "cited_item_locator"]
        self.assertEqual(severities, [GATE_SEVERITY_WARNING])
        self.assertNotIn(GATE_SEVERITY_ERROR, severities)

    def test_non_aggregated_evidence_is_not_asked_for_item_locators(self):
        market = make_evidence("EV-M", "market", related_claim_ids=["CL-001"])
        news = news_evidence("EV-N")
        assess([market, news])
        result = run_citation_gate(RUN_ID, [market, news],
                                   [gate_claim(supporting_evidence_ids=["EV-M"],
                                               contradicting_evidence_ids=["EV-N"],
                                               facts=[{"statement": "報酬為正",
                                                       "evidence_ids": ["EV-M"]}])])
        locator_findings = [item for item in result["findings"]
                            if item["check"] == "cited_item_locator"
                            and "EV-M" in item["evidence_ids"]]
        self.assertEqual(locator_findings, [])


# --------------------------------------------------------------------------------------
# 不新增模型呼叫
# --------------------------------------------------------------------------------------

class NoExtraModelCallTests(unittest.TestCase):
    """評估搭既有分析回應的便車，因此呼叫次數必須與沒有評估時完全相同。"""

    def _run(self, output: Path, assessments):
        evidence_ids = ["EV-00%d" % index for index in range(1, 10)]
        spy = SpyClient(analysis_payload(evidence_ids=evidence_ids, assessments=assessments))
        with patch("src.orchestrator.llm_is_configured", return_value=False):
            result = run("ETH", "ETH 近期市場狀況與下行風險？", output, live=False, use_llm=True,
                         analysis_client=spy, critic_client=spy)
        return result, spy

    def test_assessments_do_not_change_the_number_of_model_calls(self):
        ids = ["EV-00%d" % index for index in range(1, 10)]
        assessments = [proposal(item, "indirect", relationship="context", impact="neutral",
                                horizon="short_term") for item in ids]
        with tempfile.TemporaryDirectory() as directory:
            without, spy_without = self._run(Path(directory) / "a", None)
            with_assessment, spy_with = self._run(Path(directory) / "b", assessments)

        self.assertEqual(spy_without.calls, spy_with.calls)
        self.assertEqual(spy_with.calls, ["market_analysis", CRITIC_SCHEMA_NAME])
        self.assertEqual(without["evidence_assessment"]["path"], ASSESSMENT_PATH_FALLBACK)
        self.assertEqual(with_assessment["evidence_assessment"]["path"], ASSESSMENT_PATH_LLM)

    def test_the_execution_log_states_that_no_extra_call_was_made(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            self._run(output, None)
            log = json.loads((output / "execution_log.json").read_text(encoding="utf-8"))
        step = next(item for item in log["steps"] if item["name"] == "assess_evidence")
        self.assertEqual(step["extra_llm_calls"], 0)
        self.assertEqual(step["scored_by"], "deterministic_rules")

    def test_the_analysis_schema_keeps_the_assessment_field_optional(self):
        """列為必填時，模型漏掉一組標籤就會賠掉整份分析。"""
        self.assertIn(EVIDENCE_ASSESSMENT_FIELD, ANALYSIS_SCHEMA["properties"])
        self.assertNotIn(EVIDENCE_ASSESSMENT_FIELD, ANALYSIS_SCHEMA["required"])
        self.assertIn(EVIDENCE_ASSESSMENT_FIELD,
                      _strict_json_schema(ANALYSIS_SCHEMA)["required"])

    def test_the_assessment_field_is_quarantined_as_a_proposal(self):
        payload = analysis_payload(evidence_ids=["EV-1"], assessments=[proposal("EV-1")])
        normalised = normalise_analysis_assessments(payload)

        self.assertNotIn(EVIDENCE_ASSESSMENT_FIELD, normalised)
        self.assertEqual(len(normalised[EVIDENCE_ASSESSMENT_RESULT_FIELD]), 1)
        self.assertIn("market_judgment", normalised)

    def test_an_analysis_without_assessments_is_untouched(self):
        payload = analysis_payload(evidence_ids=["EV-1"])
        self.assertIs(normalise_analysis_assessments(payload), payload)

    def test_the_prompt_forbids_model_supplied_numbers(self):
        prompt = build_prompt("ETH", "近期風險？", [])
        self.assertIn("evidence_assessments", prompt)
        self.assertIn("不要輸出 relevance_score", prompt)
        for enum in (RELEVANCE_LABELS, QUESTION_RELATIONSHIPS, IMPACT_DIRECTIONS, IMPACT_HORIZONS):
            self.assertIn(enum[0], prompt)


# --------------------------------------------------------------------------------------
# 端到端：兩條路都要交出六項提交物
# --------------------------------------------------------------------------------------

class PipelineArtifactTests(unittest.TestCase):
    def _artifacts(self, output: Path) -> dict:
        for name in ARTIFACT_FILENAMES.values():
            self.assertTrue((output / name).exists(), name)
        return json.loads((output / "execution_log.json").read_text(encoding="utf-8"))

    def test_offline_run_produces_six_artifacts_and_a_recorded_fallback(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run("ETH", "ETH 近期市場狀況？", output, live=False, use_llm=False)
            log = self._artifacts(output)

        step = next(item for item in log["steps"] if item["name"] == "assess_evidence")
        self.assertEqual(step["status"], "fallback")
        self.assertEqual(step["path"], ASSESSMENT_PATH_FALLBACK)
        self.assertIn("no evidence assessment", step["fallback_reason"])
        self.assertEqual(step["labelled_by"], "deterministic_rules")
        self.assertEqual(result["citation_gate"]["error_count"], 0)

    def test_an_invalid_model_assessment_still_produces_six_artifacts(self):
        ids = ["EV-00%d" % index for index in range(1, 10)]
        broken = [proposal(item, "indirect") for item in ids]
        broken[0]["relevance_label"] = "extremely_relevant"   # 非法 enum
        broken[1]["relevance_score"] = 0.99                   # 模型自帶分數
        spy = SpyClient(analysis_payload(evidence_ids=ids, assessments=broken))
        with tempfile.TemporaryDirectory() as directory, \
             patch("src.orchestrator.llm_is_configured", return_value=False):
            output = Path(directory)
            result = run("ETH", "ETH 近期市場狀況？", output, live=False, use_llm=True,
                         analysis_client=spy, critic_client=spy)
            log = self._artifacts(output)
            records = json.loads((output / "evidence.json").read_text(encoding="utf-8"))

        step = next(item for item in log["steps"] if item["name"] == "assess_evidence")
        self.assertEqual(step["path"], ASSESSMENT_PATH_FALLBACK)
        self.assertIn("model assessment rejected", step["fallback_reason"])
        self.assertEqual(result["citation_gate"]["error_count"], 0)
        # 整批作廢，不是部分採用：不得有任何一筆沿用模型標籤。
        self.assertEqual({record["semantic_assessment"]["assessment_source"]
                          for record in records}, {"deterministic_fallback"})

    def test_evidence_json_keeps_the_assessment_and_the_item_metadata(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run("ETH", "ETH 近期市場狀況？", output, live=False, use_llm=False)
            records = json.loads((output / "evidence.json").read_text(encoding="utf-8"))

        for record in records:
            assessment = record["semantic_assessment"]
            self.assertIn(assessment["relevance_label"], RELEVANCE_LABELS)
            self.assertEqual(assessment["relevance_score"],
                             RELEVANCE_LABEL_SCORES[assessment["relevance_label"]])
            self.assertEqual(assessment["assessment_version"], "evidence-assessment-v1")
            self.assertEqual(
                assessment["effective_weight"],
                effective_weight(record["reliability_score"], assessment["relevance_score"],
                                 record["independence_factor"]))
        news = next(record for record in records if record["data_type"] == "news")
        self.assertTrue(news["source_items"])
        self.assertTrue(news["source_items"][0]["source_item_id"].startswith(news["evidence_id"]))

    def test_the_execution_log_does_not_carry_article_full_text(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run("ETH", "ETH 近期市場狀況？", output, live=False, use_llm=False)
            log = json.loads((output / "execution_log.json").read_text(encoding="utf-8"))
            records = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
        step = next(item for item in log["steps"] if item["name"] == "assess_evidence")

        self.assertNotIn("fulltext", json.dumps(step, ensure_ascii=False))
        self.assertNotIn("source_items", json.dumps(step, ensure_ascii=False))
        self.assertEqual(set(step["effective_weights"]),
                         {record["evidence_id"] for record in records})

    def test_two_offline_runs_agree_on_every_assessment(self):
        with tempfile.TemporaryDirectory() as directory:
            first, second = Path(directory) / "a", Path(directory) / "b"
            run("ETH", "ETH 近期市場狀況？", first, live=False, use_llm=False)
            run("ETH", "ETH 近期市場狀況？", second, live=False, use_llm=False)
            left = json.loads((first / "evidence.json").read_text(encoding="utf-8"))
            right = json.loads((second / "evidence.json").read_text(encoding="utf-8"))

        self.assertEqual([record["semantic_assessment"] for record in left],
                         [record["semantic_assessment"] for record in right])
        self.assertEqual([record["source_items"] for record in left],
                         [record["source_items"] for record in right])


if __name__ == "__main__":
    unittest.main()
