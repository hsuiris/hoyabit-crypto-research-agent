"""T0.6：驗證 T2–T5 共用的資料形狀契約已凍結，且既有行為沒有被動到。

這個 Task 只加介面與常數，不實作 credibility 計分、planner、claim graph 或 citation gate，
所以這裡驗證的是「形狀與字面值」而不是分數是否正確：

- Evidence 的既有 positional constructor 仍可用，新欄位全部有 default 且可序列化。
- 兩組權重各自加總為 1.0，鍵與 component key 清單一致。
- hard caps 覆蓋 T3 列出的全部鍵，值域在 0–1，``missing_fetched_at`` 保留 rejected 語意。
- 常數集合不可變，VERDICTS／TASK_MODES 與文件一致。
- 六項提交物檔名齊全。
- 既有離線執行仍產出三個標準檔案。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from dataclasses import asdict, fields
from pathlib import Path

from src import schemas
from src.day1_mvp import Evidence
from src.orchestrator import run
from src.schemas import (
    ARTIFACT_FILENAMES,
    CONFIDENCE_COMPONENT_KEYS,
    CONFIDENCE_LEVELS,
    CONFIDENCE_TYPE_HEURISTIC,
    CONFIDENCE_WEIGHTS,
    CREDIBILITY_COMPONENT_KEYS,
    CREDIBILITY_WEIGHTS,
    EVIDENCE_CREDIBILITY_FIELDS,
    HARD_CAPS,
    HIGH_QUALITY_CONFLICT_CONFIDENCE_CAP,
    MIN_DOMAIN_COVERAGE,
    REJECTING_HARD_CAP_KEYS,
    SCHEMA_VERSION,
    SCORING_VERSION,
    SINGLE_DOMAIN_CONFIDENCE_CAP,
    SOURCE_TYPES,
    TASK_MODES,
    VERDICTS,
    VERIFICATION_STATUSES,
    Claim,
    ClaimConfidence,
    Fact,
    Hypothesis,
    ResearchPlan,
)


# T3-credibility.md「Hard Caps」列出的鍵；high_quality_conflict_claim_cap 來自
# .kiro/steering/evidence-confidence-standards.md，HARD_CAPS 必須同時覆蓋兩份清單。
T3_HARD_CAP_KEYS = (
    "missing_source_locator",
    "missing_fetched_at",
    "anonymous_or_low_trace_social",
    "single_secondary_news_source",
    "fallback_fixture",
    "unverifiable_entity_attribution",
    "unverifiable_intent_attribution",
)


class EvidenceCompatibilityTests(unittest.TestCase):
    def test_existing_positional_constructor_still_works(self):
        """既有 Collector 全部用 positional 參數建立 Evidence，順序不得改變。"""
        evidence = Evidence(
            "EV-1", "CoinGecko", "https://example.com/market", "2026-08-01T00:00:00+00:00",
            "market", "ETH", "14d", {"return_pct": 8.2}, 0.9,
        )

        self.assertEqual(evidence.evidence_id, "EV-1")
        self.assertEqual(evidence.source, "CoinGecko")
        self.assertEqual(evidence.data_type, "market")
        self.assertEqual(evidence.reliability_score, 0.9)
        # __post_init__ 的既有補值行為不變。
        self.assertEqual(evidence.content_reference["time_range"], "14d")
        self.assertIn("ETH", evidence.related_claim)

    def test_positional_constructor_including_traceability_fields_still_works(self):
        """orchestrator 會連 content_reference 與 related_claim 一起用 positional 傳入。"""
        evidence = Evidence(
            "EV-2", "Competition OHLCV CSV", "data/ETH.csv", "2026-08-01T00:00:00+00:00",
            "market", "ETH", "1826d", {}, 0.95, {"file": "data/ETH.csv"}, "ETH close prices",
        )

        self.assertEqual(evidence.content_reference, {"file": "data/ETH.csv"})
        self.assertEqual(evidence.related_claim, "ETH close prices")

    def test_new_credibility_fields_are_appended_with_defaults(self):
        names = [item.name for item in fields(Evidence)]
        legacy = [
            "evidence_id", "source", "source_url", "fetched_at", "data_type", "coin",
            "time_range", "content", "reliability_score", "content_reference", "related_claim",
        ]

        self.assertEqual(names[:len(legacy)], legacy, "既有欄位不得刪除或重新排序")
        # T3 的 11 個計分欄位仍緊接在既有欄位之後，字面與順序不變；
        # T5 只在尾端追加 run 歸屬欄位（EVIDENCE_RUN_FIELDS），這是相容的擴充方式。
        credibility_block = tuple(names[len(legacy):len(legacy) + len(EVIDENCE_CREDIBILITY_FIELDS)])
        self.assertEqual(credibility_block, EVIDENCE_CREDIBILITY_FIELDS)
        self.assertEqual(len(EVIDENCE_CREDIBILITY_FIELDS), 11)
        # 尾端依 Task 順序分段追加：T5 的 run 歸屬，然後 E2 的語意評估。分段比對而不是
        # 「最後一段必須是 run_id」，否則每次相容擴充都會讓這個測試失敗，卻沒有任何契約被破壞。
        tail = tuple(names[len(legacy) + len(EVIDENCE_CREDIBILITY_FIELDS):])
        self.assertEqual(tail, schemas.EVIDENCE_RUN_FIELDS + schemas.EVIDENCE_ASSESSMENT_FIELDS)
        self.assertEqual(schemas.EVIDENCE_RUN_FIELDS, ("run_id",))
        self.assertEqual(schemas.EVIDENCE_ASSESSMENT_FIELDS,
                         ("source_items", "semantic_assessment"))

    def test_new_field_defaults_match_the_frozen_schema_constants(self):
        """day1_mvp 未 import schemas（避免多一個相對匯入相依），因此在這裡擋住兩邊漂移。"""
        evidence = Evidence(
            "EV-3", "MockNews", "https://example.com/news", "2026-08-01T00:00:00+00:00",
            "news", "ETH", "14d", {}, 0.6,
        )

        self.assertEqual(evidence.source_type, schemas.SOURCE_TYPE_UNKNOWN)
        self.assertEqual(evidence.verification_status, schemas.VERIFICATION_STATUS_UNVERIFIED)
        self.assertIn(evidence.source_type, SOURCE_TYPES)
        self.assertIn(evidence.verification_status, VERIFICATION_STATUSES)
        self.assertIsNone(evidence.published_at)
        self.assertIsNone(evidence.event_time)
        self.assertEqual(evidence.source_lineage_id, "")
        self.assertEqual(evidence.claim_relevance, 0.0)
        self.assertEqual(evidence.independence_factor, 1.0)
        self.assertEqual(evidence.score_breakdown, {})
        self.assertEqual(evidence.score_limiters, [])
        self.assertEqual(evidence.related_claim_ids, [])
        self.assertEqual(evidence.scoring_version, "")
        # T5：未標記 run 的證據預設為空字串，由 Orchestrator 在計分前蓋上本次 run_id。
        self.assertEqual(evidence.run_id, "")
        # E2：未評估過的證據兩個容器都是空的，因此「沒有評估」與「評估為 0」可以區分。
        self.assertEqual(evidence.source_items, [])
        self.assertEqual(evidence.semantic_assessment, {})

    def test_mutable_defaults_are_not_shared_between_instances(self):
        first = Evidence("EV-4", "S", "u", "2026-08-01T00:00:00+00:00", "news", "ETH", "14d", {}, 0.5)
        second = Evidence("EV-5", "S", "u", "2026-08-01T00:00:00+00:00", "news", "ETH", "14d", {}, 0.5)

        first.score_limiters.append("fallback_fixture")
        first.related_claim_ids.append("CL-001")
        first.score_breakdown["freshness"] = 0.4

        self.assertEqual(second.score_limiters, [])
        self.assertEqual(second.related_claim_ids, [])
        self.assertEqual(second.score_breakdown, {})

    def test_evidence_with_new_fields_stays_json_serialisable(self):
        """evidence.json 用 json.dumps(asdict(...)) 落地，新欄位不得引入不可序列化的值。"""
        evidence = Evidence(
            "EV-6", "Binance Futures", "https://example.com/deriv", "2026-08-01T00:00:00+00:00",
            "derivatives", "ETH", "current", {"funding_rate_pct": 0.008}, 0.75,
        )
        evidence.source_type = "derivatives_api"
        evidence.published_at = "2026-07-31T23:00:00+00:00"
        evidence.score_breakdown = {key: 0.5 for key in CREDIBILITY_COMPONENT_KEYS}
        evidence.score_limiters = ["single_secondary_news_source"]
        evidence.related_claim_ids = ["CL-001"]
        evidence.scoring_version = SCORING_VERSION

        record = json.loads(json.dumps(asdict(evidence)))

        for name in EVIDENCE_CREDIBILITY_FIELDS:
            self.assertIn(name, record)
        self.assertEqual(record["score_breakdown"], {key: 0.5 for key in CREDIBILITY_COMPONENT_KEYS})
        self.assertEqual(record["scoring_version"], SCORING_VERSION)


class ResearchPlanSchemaTests(unittest.TestCase):
    def test_plan_fields_match_the_documented_json(self):
        expected = (
            "coins", "task_modes", "primary_question", "time_window", "hypotheses",
            "required_domains", "comparison_dimensions", "assumptions", "stop_conditions",
        )
        self.assertEqual(tuple(item.name for item in fields(ResearchPlan)), expected)

    def test_hypothesis_keeps_support_and_contradiction_symmetrical(self):
        expected = (
            "hypothesis_id", "statement", "support_questions",
            "contradiction_questions", "falsification_conditions",
        )
        self.assertEqual(tuple(item.name for item in fields(Hypothesis)), expected)

    def test_plan_defaults_are_serialisable_and_disclose_the_window_source(self):
        plan = ResearchPlan(
            coins=["ETH"],
            task_modes=[schemas.DEFAULT_TASK_MODE],
            primary_question="分析 ETH 當前市場狀況。",
            hypotheses=[asdict(Hypothesis("H1", "ETH 維持盤整"))],
        )

        payload = json.loads(json.dumps(asdict(plan), ensure_ascii=False))

        self.assertEqual(payload["time_window"], {"days": 14, "source": "default"})
        self.assertEqual(payload["stop_conditions"], {"max_evidence": 36, "max_followup_rounds": 1})
        self.assertEqual(payload["hypotheses"][0]["hypothesis_id"], "H1")
        self.assertIn(schemas.DEFAULT_TASK_MODE, TASK_MODES)
        self.assertIn(schemas.TIME_WINDOW_SOURCE_DEFAULT, schemas.TIME_WINDOW_SOURCES)

    def test_plan_default_containers_are_not_shared(self):
        first = ResearchPlan()
        second = ResearchPlan()
        first.task_modes.append("compare_assets")
        first.time_window["days"] = 30

        self.assertEqual(second.task_modes, [])
        self.assertEqual(second.time_window["days"], 14)

    def test_task_modes_match_the_documented_seven(self):
        self.assertEqual(TASK_MODES, (
            "describe_market_state",
            "test_hypothesis",
            "compare_assets",
            "explain_driver",
            "assess_consistency",
            "identify_risks",
            "identify_attention_conditions",
        ))
        self.assertEqual(len(TASK_MODES), 7)


class ClaimSchemaTests(unittest.TestCase):
    def test_claim_fields_match_the_documented_json(self):
        expected = (
            "claim_id", "statement", "claim_type", "verdict", "facts", "inference",
            "conclusion", "supporting_evidence_ids", "contradicting_evidence_ids",
            "confidence", "limitations", "invalidation_conditions", "watchpoints",
        )
        self.assertEqual(tuple(item.name for item in fields(Claim)), expected)
        self.assertEqual(tuple(item.name for item in fields(Fact)), ("statement", "evidence_ids"))
        self.assertEqual(
            tuple(item.name for item in fields(ClaimConfidence)),
            ("score", "level", "type", "components", "limiters"),
        )

    def test_claim_defaults_are_conservative(self):
        claim = Claim()

        self.assertEqual(claim.verdict, "insufficient_evidence")
        self.assertIn(claim.verdict, VERDICTS)
        self.assertIn(claim.claim_type, ("market_judgment",))
        self.assertEqual(claim.confidence.score, 0.0)
        self.assertEqual(claim.confidence.type, CONFIDENCE_TYPE_HEURISTIC)
        self.assertIn(claim.confidence.level, CONFIDENCE_LEVELS)

    def test_claim_is_json_serialisable_with_nested_facts_and_confidence(self):
        claim = Claim(
            claim_id="CL-001",
            statement="ETH 短期訊號互相牽制。",
            verdict="partially_supported",
            facts=[asdict(Fact("14 日報酬為正。", ["EV-MARKET-001"]))],
            inference="動能不具決定性。",
            conclusion="維持觀察。",
            supporting_evidence_ids=["EV-MARKET-001"],
            contradicting_evidence_ids=["EV-SOCIAL-001"],
            confidence=ClaimConfidence(
                score=0.6, level="medium",
                components={key: 0.5 for key in CONFIDENCE_COMPONENT_KEYS},
                limiters=["single_supporting_domain"],
            ),
            limitations=["社群樣本數不足。"],
            invalidation_conditions=["跌破區間下緣。"],
            watchpoints=["成交量變化。"],
        )

        payload = json.loads(json.dumps(asdict(claim), ensure_ascii=False))

        self.assertEqual(payload["facts"][0]["evidence_ids"], ["EV-MARKET-001"])
        self.assertEqual(payload["confidence"]["type"], "heuristic")
        self.assertEqual(sorted(payload["confidence"]["components"]), sorted(CONFIDENCE_COMPONENT_KEYS))

    def test_verdicts_match_the_documented_five(self):
        self.assertEqual(VERDICTS, (
            "supported", "partially_supported", "mixed", "contradicted", "insufficient_evidence",
        ))
        self.assertEqual(len(VERDICTS), 5)


class WeightAndCapTests(unittest.TestCase):
    def test_credibility_weights_sum_to_one_and_cover_every_component(self):
        self.assertEqual(tuple(CREDIBILITY_WEIGHTS), CREDIBILITY_COMPONENT_KEYS)
        self.assertAlmostEqual(sum(CREDIBILITY_WEIGHTS.values()), 1.0, places=9)
        self.assertEqual(CREDIBILITY_WEIGHTS["source_quality"], 0.30)
        self.assertEqual(CREDIBILITY_WEIGHTS["traceability"], 0.25)
        self.assertEqual(CREDIBILITY_WEIGHTS["freshness"], 0.20)
        self.assertEqual(CREDIBILITY_WEIGHTS["method_transparency"], 0.15)
        self.assertEqual(CREDIBILITY_WEIGHTS["independence"], 0.10)

    def test_confidence_weights_sum_to_one_and_cover_every_component(self):
        self.assertEqual(tuple(CONFIDENCE_WEIGHTS), CONFIDENCE_COMPONENT_KEYS)
        self.assertAlmostEqual(sum(CONFIDENCE_WEIGHTS.values()), 1.0, places=9)
        self.assertEqual(CONFIDENCE_WEIGHTS["weighted_evidence_quality"], 0.30)
        self.assertEqual(CONFIDENCE_WEIGHTS["domain_coverage"], 0.25)
        self.assertEqual(CONFIDENCE_WEIGHTS["source_diversity"], 0.20)
        self.assertEqual(CONFIDENCE_WEIGHTS["signal_consistency"], 0.15)
        self.assertEqual(CONFIDENCE_WEIGHTS["counter_evidence_coverage"], 0.10)

    def test_hard_caps_cover_every_documented_key_within_zero_to_one(self):
        for key in T3_HARD_CAP_KEYS:
            self.assertIn(key, HARD_CAPS)
        self.assertIn("high_quality_conflict_claim_cap", HARD_CAPS)

        for key, cap in HARD_CAPS.items():
            self.assertIsInstance(cap, float, key)
            self.assertGreaterEqual(cap, 0.0, key)
            self.assertLessEqual(cap, 1.0, key)

        self.assertEqual(HARD_CAPS["missing_source_locator"], 0.30)
        self.assertEqual(HARD_CAPS["anonymous_or_low_trace_social"], 0.35)
        self.assertEqual(HARD_CAPS["single_secondary_news_source"], 0.60)
        self.assertEqual(HARD_CAPS["fallback_fixture"], 0.20)
        self.assertEqual(HARD_CAPS["unverifiable_entity_attribution"], 0.60)
        self.assertEqual(HARD_CAPS["unverifiable_intent_attribution"], 0.55)
        self.assertEqual(HARD_CAPS["high_quality_conflict_claim_cap"], 0.70)

    def test_missing_fetched_at_keeps_rejected_semantics(self):
        """T3 對 missing_fetched_at 寫的是 rejected 而不是一個上限值。"""
        self.assertIn("missing_fetched_at", REJECTING_HARD_CAP_KEYS)
        self.assertEqual(HARD_CAPS["missing_fetched_at"], schemas.HARD_CAP_REJECTED)
        self.assertEqual(schemas.HARD_CAP_REJECTED, 0.0)
        self.assertEqual(schemas.VERIFICATION_STATUS_REJECTED, "rejected")
        self.assertIn(schemas.VERIFICATION_STATUS_REJECTED, VERIFICATION_STATUSES)

    def test_confidence_rule_thresholds_are_frozen(self):
        self.assertEqual(SINGLE_DOMAIN_CONFIDENCE_CAP, 0.60)
        self.assertEqual(HIGH_QUALITY_CONFLICT_CONFIDENCE_CAP, HARD_CAPS["high_quality_conflict_claim_cap"])
        self.assertEqual(MIN_DOMAIN_COVERAGE, 0.40)


class ImmutabilityTests(unittest.TestCase):
    def test_constant_sequences_are_tuples_or_frozensets(self):
        for name in ("TASK_MODES", "TIME_WINDOW_SOURCES", "VERDICTS", "CONFIDENCE_LEVELS",
                     "CREDIBILITY_COMPONENT_KEYS", "CONFIDENCE_COMPONENT_KEYS", "SOURCE_TYPES",
                     "VERIFICATION_STATUSES", "EVIDENCE_CREDIBILITY_FIELDS", "SOURCE_LINEAGE_KEYS",
                     "REQUIRED_ARTIFACT_KEYS", "REJECTING_HARD_CAP_KEYS"):
            value = getattr(schemas, name)
            self.assertIsInstance(value, (tuple, frozenset), name)

    def test_constant_mappings_reject_mutation(self):
        for name in ("CREDIBILITY_WEIGHTS", "CONFIDENCE_WEIGHTS", "HARD_CAPS", "ARTIFACT_FILENAMES"):
            mapping = getattr(schemas, name)
            with self.assertRaises(TypeError, msg=name):
                mapping["injected"] = 1.0

    def test_source_types_and_verification_statuses_match_the_registry(self):
        self.assertEqual(SOURCE_TYPES, (
            "market_api", "derivatives_api", "blockchain_raw", "official_announcement",
            "major_media", "secondary_media", "social_public", "macro_api", "local_csv",
            "fallback_fixture", "unknown",
        ))
        # T3 appended "unavailable"／"fallback" (T3-credibility.md rule 11). Additive only: the
        # first four values and their order are still the T0.6 contract.
        self.assertEqual(VERIFICATION_STATUSES,
                         ("unverified", "partially_confirmed", "verified", "rejected",
                          "unavailable", "fallback"))
        self.assertEqual(VERIFICATION_STATUSES[:4],
                         ("unverified", "partially_confirmed", "verified", "rejected"))
        self.assertEqual(schemas.NON_SUBSTANTIVE_VERIFICATION_STATUSES,
                         frozenset({"rejected", "unavailable", "fallback"}))
        self.assertEqual(SCHEMA_VERSION, "competition-schema-v1")
        self.assertEqual(SCORING_VERSION, "credibility-v1")


class ArtifactContractTests(unittest.TestCase):
    def test_all_six_submission_filenames_are_frozen(self):
        self.assertEqual(dict(ARTIFACT_FILENAMES), {
            "report": "report.md",
            "evidence": "evidence.json",
            "execution_log": "execution_log.json",
            "research_plan": "research_plan.json",
            "claims": "claims.json",
            "manifest": "manifest.json",
        })
        self.assertEqual(len(ARTIFACT_FILENAMES), 6)
        self.assertEqual(schemas.REQUIRED_ARTIFACT_KEYS, tuple(ARTIFACT_FILENAMES))

    def test_schema_module_stays_free_of_project_dependencies(self):
        """契約模組不得 import 專案內其他模組，才能被任何一層安全引用。"""
        source = (Path(__file__).parents[1] / "src" / "schemas.py").read_text(encoding="utf-8")
        for forbidden in ("from .", "from src", "import src"):
            self.assertNotIn(forbidden, source)


class OfflineRegressionTests(unittest.TestCase):
    def test_offline_run_still_produces_the_three_existing_artifacts(self):
        """既有離線流程與三個輸出檔必須不受影響；新欄位由 T3 起開始填值。"""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run("ETH", "T0.6 schema contract 離線回歸", output, live=False, use_llm=False)

            for name in ("report.md", "evidence.json", "execution_log.json"):
                self.assertTrue((output / name).exists(), name)

            records = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
            self.assertTrue(records)
            for record in records:
                for name in EVIDENCE_CREDIBILITY_FIELDS:
                    self.assertIn(name, record, f"{record['evidence_id']} missing {name}")
                # T0.6 凍結形狀、T3 開始填值：分數必須可展開，且離線 fixture 不得偽裝成實證。
                self.assertEqual(record["score_breakdown"]["final_score"], record["reliability_score"])
                self.assertEqual(record["scoring_version"], SCORING_VERSION)
                self.assertIn(record["source_type"], SOURCE_TYPES)
                self.assertIn(record["verification_status"], VERIFICATION_STATUSES)
                # T4 起 claim 綁定會被寫回：值只能是本次 run 的 Claim ID，未被任何 Claim
                # 引用的證據仍維持空清單（不得為了好看而硬塞）。
                self.assertIsInstance(record["related_claim_ids"], list)
                for claim_id in record["related_claim_ids"]:
                    self.assertRegex(claim_id, r"^CL-\d{3}$")

            log = json.loads((output / "execution_log.json").read_text(encoding="utf-8"))
            llm_step = next(step for step in log["steps"] if step["name"] == "llm_reasoning")
            self.assertEqual(llm_step["status"], "offline_fallback")
            self.assertEqual(result["reasoning"]["cited_evidence_ids"], result["evidence_ids"])


if __name__ == "__main__":
    unittest.main()
