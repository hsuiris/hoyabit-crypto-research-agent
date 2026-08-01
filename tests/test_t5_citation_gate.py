"""T5：Structural Citation Gate、語意稽核與六項競賽提交物。

`tests/test_t4_claim_graph_wiring.py` 驗的是 Claim 怎麼被算出來；這個檔案驗的是**發佈前的最後
一道關卡**與**提交物本身**：

* gate 是否擋得住十種不可追溯的引用（未知 ID、跨 run、rejected、fallback 唯一支持、
  正反引用同一筆、無支持卻給方向、信心突破上限、related_claim_ids 對不上）；
* 八個語意類別在**沒有模型**的情況下是否仍有覆蓋（官方公告當成果、鏈上轉帳當意圖、
  同時性當因果……）；
* Critic 逾時／試圖加分時，是否仍然產出保守但完整的六個檔案；
* `manifest.json` 的 SHA-256 是否真的能驗證輸出檔案，六個檔案是否共用同一個 run_id；
* 報告 renderer 是否 deterministic：相同輸入逐字相同，段落順序固定。

全部離線執行：模型一律走注入的 client，不觸網路。
"""

from __future__ import annotations

import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from src.claim_graph import GRAPH_SOURCE_FALLBACK
from src.day1_mvp import Evidence
from src.llm import (CRITIC_CATEGORY_GUIDE, CRITIC_SCHEMA, OfflineLLMClient, build_critic_prompt,
                     critique_with_llm)
from src.orchestrator import _source_consistency, run, run_comparison
from src.report_renderer import render_competition_report
from src.schemas import (ARTIFACT_FILENAMES, CITATION_GATE_CHECKS, CITATION_GATE_VERSION,
                         CONFIDENCE_COMPONENT_KEYS, CONFIDENCE_TYPE_HEURISTIC,
                         GATE_STATUS_FAIL, GATE_STATUS_PASS, GATE_STATUS_PASS_WITH_WARNINGS,
                         MANIFEST_VERSION, SEMANTIC_CRITIC_CATEGORIES,
                         VERDICT_INSUFFICIENT_EVIDENCE)
from src.validation import (detect_semantic_risks, normalise_semantic_category, run_citation_gate)

RUN_ID = "20260801T000000Z-t5test"
FETCHED_AT = "2026-07-30T00:00:00+00:00"


def make_evidence(evidence_id="EV-1", data_type="market", *, source_type="market_api",
                  score=0.90, status="unverified", run_id=RUN_ID, related_claim_ids=("CL-001",),
                  limiters=(), content=None) -> Evidence:
    """帶 T3 計分欄位與 T5 run 歸屬的證據；只調整測試關心的維度。"""
    return Evidence(
        evidence_id, "TestSource", f"https://example.com/{evidence_id}", FETCHED_AT,
        data_type, "ETH", "14d", dict(content or {"note": "fixture"}), score,
        source_type=source_type,
        verification_status=status,
        source_lineage_id=evidence_id,
        claim_relevance=0.80,
        independence_factor=1.0,
        score_limiters=list(limiters),
        score_breakdown={"final_score": score, "score_limiters": list(limiters)},
        related_claim_ids=list(related_claim_ids),
        scoring_version="credibility-v1",
        run_id=run_id,
    )


def make_claim(**overrides) -> dict:
    """一個結構完整、可通過 gate 的 Claim；測試再逐項破壞它。"""
    claim = {
        "claim_id": "CL-001", "statement": "ETH 短期訊號偏多", "claim_type": "market_judgment",
        "verdict": "partially_supported",
        "facts": [{"statement": "期間報酬為正", "evidence_ids": ["EV-1"]}],
        "inference": "價格動能為正，構成偏多解讀。",
        "conclusion": "本次證據偏多，但保留反向側證據。",
        "supporting_evidence_ids": ["EV-1"], "contradicting_evidence_ids": ["EV-2"],
        "confidence": {"score": 0.50, "level": "medium", "type": CONFIDENCE_TYPE_HEURISTIC,
                       "components": {key: 0.5 for key in CONFIDENCE_COMPONENT_KEYS},
                       "limiters": []},
        "limitations": ["社群樣本數不足。"],
        "invalidation_conditions": ["反向側權重超過支持側。"],
        "watchpoints": ["追蹤成交量。"],
    }
    claim.update(overrides)
    return claim


def two_sided_evidence(**overrides) -> list:
    return [make_evidence("EV-1", "market", **overrides),
            make_evidence("EV-2", "news", source_type="major_media", score=0.70)]


def gate(evidence, claims, **kwargs) -> dict:
    return run_citation_gate(RUN_ID, evidence, claims, **kwargs)


class GateBaselineTests(unittest.TestCase):
    def test_a_fully_traceable_claim_passes(self):
        result = gate(two_sided_evidence(), [make_claim()])

        self.assertEqual(result["status"], GATE_STATUS_PASS)
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["gate_version"], CITATION_GATE_VERSION)
        self.assertEqual(set(result["checks"]), set(CITATION_GATE_CHECKS))
        self.assertTrue(all(outcome == "pass" for outcome in result["checks"].values()))

    def test_gate_result_is_json_serialisable_for_the_execution_log(self):
        result = gate(two_sided_evidence(), [make_claim()])
        self.assertEqual(json.loads(json.dumps(result, ensure_ascii=False)), result)

    def test_same_input_gives_the_same_gate_result(self):
        first = gate(two_sided_evidence(), [make_claim()])
        second = gate(two_sided_evidence(), [make_claim()])
        self.assertEqual(first, second)


class GateStructuralRuleTests(unittest.TestCase):
    """十條結構規則，一條一個測試。"""

    def test_unknown_evidence_id_fails_the_gate(self):
        claim = make_claim(supporting_evidence_ids=["EV-404"],
                           facts=[{"statement": "無來源", "evidence_ids": ["EV-404"]}])
        result = gate(two_sided_evidence(), [claim])

        self.assertEqual(result["status"], GATE_STATUS_FAIL)
        self.assertEqual(result["checks"]["evidence_id_exists"], "fail")
        self.assertTrue(any("EV-404" in message and "沒有這筆 Evidence" in message
                            for message in result["errors"]))

    def test_cross_run_evidence_cannot_be_cited(self):
        evidence = two_sided_evidence()
        evidence[0].run_id = "20260731T000000Z-otherrun"
        result = gate(evidence, [make_claim()])

        self.assertEqual(result["status"], GATE_STATUS_FAIL)
        self.assertEqual(result["checks"]["no_cross_run_citation"], "fail")
        self.assertEqual(result["checks"]["evidence_belongs_to_run"], "fail")
        self.assertTrue(any("另一次執行" in message for message in result["errors"]))

    def test_unstamped_evidence_is_a_warning_not_a_failure(self):
        # 舊 fixture 沒有 run_id：必須被指出來，但不該讓整份報告發不出去。
        evidence = two_sided_evidence()
        evidence[0].run_id = ""
        result = gate(evidence, [make_claim()])

        self.assertEqual(result["status"], GATE_STATUS_PASS_WITH_WARNINGS)
        self.assertEqual(result["checks"]["evidence_belongs_to_run"], "warn")
        self.assertTrue(any("沒有 run_id" in message for message in result["warnings"]))

    def test_evidence_missing_a_required_field_fails_the_gate(self):
        evidence = two_sided_evidence()
        evidence[0].content_reference = {}
        result = gate(evidence, [make_claim()])

        self.assertEqual(result["status"], GATE_STATUS_FAIL)
        self.assertEqual(result["checks"]["evidence_required_fields"], "fail")
        self.assertTrue(any("缺少 content_reference" in message for message in result["errors"]))

    def test_rejected_evidence_cannot_be_cited(self):
        evidence = two_sided_evidence()
        evidence[1].verification_status = "rejected"
        result = gate(evidence, [make_claim()])

        self.assertEqual(result["status"], GATE_STATUS_FAIL)
        self.assertEqual(result["checks"]["evidence_not_rejected"], "fail")

    def test_fallback_evidence_cannot_be_the_sole_support(self):
        evidence = [make_evidence("EV-1", "market", source_type="fallback_fixture",
                                  score=0.20, status="fallback", limiters=("fallback_fixture",)),
                    make_evidence("EV-2", "news", source_type="major_media", score=0.70)]
        result = gate(evidence, [make_claim()])

        self.assertEqual(result["status"], GATE_STATUS_FAIL)
        self.assertEqual(result["checks"]["fallback_not_sole_support"], "fail")
        self.assertTrue(any("fallback／降級來源" in message for message in result["errors"]))

    def test_fallback_sole_support_is_acceptable_when_declared_insufficient(self):
        # 誠實的降級：支持證據只有 fixture 時輸出 insufficient_evidence 是允許的。
        evidence = [make_evidence("EV-1", "market", source_type="fallback_fixture",
                                  score=0.20, status="fallback", limiters=("fallback_fixture",)),
                    make_evidence("EV-2", "news", source_type="major_media", score=0.70)]
        claim = make_claim(verdict=VERDICT_INSUFFICIENT_EVIDENCE,
                           confidence={"score": 0.30, "level": "low",
                                       "type": CONFIDENCE_TYPE_HEURISTIC,
                                       "components": {key: 0.3 for key in CONFIDENCE_COMPONENT_KEYS},
                                       "limiters": ["fallback_only_primary_support"]})
        result = gate(evidence, [claim])

        self.assertEqual(result["status"], GATE_STATUS_PASS)

    def test_same_evidence_cannot_support_and_contradict(self):
        claim = make_claim(contradicting_evidence_ids=["EV-1", "EV-2"])
        result = gate(two_sided_evidence(), [claim])

        self.assertEqual(result["status"], GATE_STATUS_FAIL)
        self.assertEqual(result["checks"]["support_contradiction_exclusive"], "fail")
        self.assertTrue(any("未標示為 mixed／context" in message for message in result["errors"]))

    def test_declared_mixed_evidence_is_downgraded_to_a_warning(self):
        evidence = two_sided_evidence()
        for item in evidence:
            item.related_claim_ids = ["CL-001"]
        claim = make_claim(verdict="mixed", contradicting_evidence_ids=["EV-1", "EV-2"])
        result = gate(evidence, [claim])

        self.assertEqual(result["status"], GATE_STATUS_PASS_WITH_WARNINGS)
        self.assertEqual(result["checks"]["support_contradiction_exclusive"], "warn")

    def test_claim_without_support_must_be_insufficient_evidence(self):
        claim = make_claim(supporting_evidence_ids=[], facts=[])
        result = gate(two_sided_evidence(), [claim])

        self.assertEqual(result["status"], GATE_STATUS_FAIL)
        self.assertEqual(result["checks"]["claim_has_supporting_evidence"], "fail")

    def test_confidence_above_its_limiter_cap_fails_the_gate(self):
        claim = make_claim(confidence={
            "score": 0.90, "level": "high", "type": CONFIDENCE_TYPE_HEURISTIC,
            "components": {key: 0.9 for key in CONFIDENCE_COMPONENT_KEYS},
            "limiters": ["single_supporting_domain"]})
        result = gate(two_sided_evidence(), [claim])

        self.assertEqual(result["status"], GATE_STATUS_FAIL)
        self.assertEqual(result["checks"]["confidence_within_cap"], "fail")
        self.assertTrue(any("single_supporting_domain 的上限 0.6" in message
                            for message in result["errors"]))

    def test_insufficient_evidence_claim_cannot_carry_a_high_confidence(self):
        claim = make_claim(verdict=VERDICT_INSUFFICIENT_EVIDENCE, confidence={
            "score": 0.80, "level": "high", "type": CONFIDENCE_TYPE_HEURISTIC,
            "components": {key: 0.8 for key in CONFIDENCE_COMPONENT_KEYS}, "limiters": []})
        result = gate(two_sided_evidence(), [claim])

        self.assertEqual(result["status"], GATE_STATUS_FAIL)
        self.assertTrue(any("insufficient_evidence 的上限" in message
                            for message in result["errors"]))

    def test_shared_evidence_credibility_cap_is_reported_as_a_warning(self):
        # claim confidence 與 evidence reliability 是兩個尺度，硬性等同會擋掉合法執行，
        # 所以「全部支持證據共有的 cap」（此例 0.55）只警告，不 FAIL。
        evidence = two_sided_evidence(limiters=("unverifiable_intent_attribution",))
        claim = make_claim(confidence={
            "score": 0.65, "level": "medium", "type": CONFIDENCE_TYPE_HEURISTIC,
            "components": {key: 0.65 for key in CONFIDENCE_COMPONENT_KEYS}, "limiters": []})
        result = gate(evidence, [claim])

        self.assertEqual(result["status"], GATE_STATUS_PASS_WITH_WARNINGS)
        self.assertEqual(result["error_count"], 0)
        self.assertTrue(any("unverifiable_intent_attribution" in message
                            for message in result["warnings"]))

    def test_related_claim_ids_must_contain_the_citing_claim(self):
        evidence = two_sided_evidence()
        evidence[0].related_claim_ids = []
        result = gate(evidence, [make_claim()])

        self.assertEqual(result["status"], GATE_STATUS_FAIL)
        self.assertEqual(result["checks"]["related_claim_ids_consistent"], "fail")
        self.assertTrue(any("related_claim_ids 只有 空清單" in message
                            for message in result["errors"]))

    def test_related_claim_ids_pointing_at_an_unused_claim_fails(self):
        evidence = two_sided_evidence()
        evidence[0].related_claim_ids = ["CL-001", "CL-999"]
        result = gate(evidence, [make_claim()])

        self.assertEqual(result["status"], GATE_STATUS_FAIL)
        self.assertTrue(any("不存在的 Claim" in message for message in result["errors"]))

    def test_related_claim_check_can_be_deferred_before_linking(self):
        evidence = two_sided_evidence(related_claim_ids=())
        evidence[1].related_claim_ids = []
        result = gate(evidence, [make_claim()], check_related_claim_ids=False)

        self.assertFalse(result["related_claim_ids_checked"])
        self.assertEqual(result["checks"]["related_claim_ids_consistent"], "pass")


class SemanticRiskTests(unittest.TestCase):
    """八個語意類別在離線（無模型）情況下的 deterministic 覆蓋。"""

    def categories(self, claims, evidence) -> set:
        return {finding["category"] for finding in detect_semantic_risks(claims, evidence)}

    def test_official_announcement_cannot_be_presented_as_a_business_outcome(self):
        evidence = [make_evidence("EV-1", "announcement", source_type="official_announcement")]
        claim = make_claim(
            supporting_evidence_ids=["EV-1"], contradicting_evidence_ids=[],
            facts=[{"statement": "官方發布合作公告", "evidence_ids": ["EV-1"]}],
            inference="合作已帶來實際營收成長。", conclusion="基本面已改善。")

        self.assertIn("official_statement_as_outcome", self.categories([claim], evidence))

    def test_official_announcement_without_outcome_language_is_not_flagged(self):
        evidence = [make_evidence("EV-1", "announcement", source_type="official_announcement")]
        claim = make_claim(
            supporting_evidence_ids=["EV-1"], contradicting_evidence_ids=[],
            facts=[{"statement": "官方發布合作公告", "evidence_ids": ["EV-1"]}],
            inference="公告本身只能證明訊息已發布。", conclusion="維持觀察。")

        self.assertNotIn("official_statement_as_outcome", self.categories([claim], evidence))

    def test_onchain_transfer_cannot_be_presented_as_intent(self):
        evidence = [make_evidence("EV-1", "whale", source_type="blockchain_raw")]
        claim = make_claim(
            statement="大戶準備拋售", supporting_evidence_ids=["EV-1"],
            contradicting_evidence_ids=[],
            facts=[{"statement": "偵測到 12000 ETH 轉入交易所", "evidence_ids": ["EV-1"]}],
            inference="轉入交易所通常先於賣出。", conclusion="短線偏空。")

        self.assertIn("onchain_transfer_as_intent", self.categories([claim], evidence))

    def test_causal_language_is_flagged_as_correlation_as_causation(self):
        claim = make_claim(inference="資金費率上升導致價格上漲。")
        self.assertIn("correlation_as_causation", self.categories([claim], two_sided_evidence()))

    def test_certainty_language_is_flagged_as_over_claim(self):
        claim = make_claim(conclusion="價格必然續漲。")
        self.assertIn("over_claim", self.categories([claim], two_sided_evidence()))

    def test_counter_evidence_without_stated_limitation_is_flagged(self):
        claim = make_claim(limitations=[])
        self.assertIn("ignored_counter", self.categories([claim], two_sided_evidence()))

    def test_weak_only_support_is_flagged_as_stale_or_weak(self):
        evidence = [make_evidence("EV-1", "market", score=0.30),
                    make_evidence("EV-2", "news", source_type="major_media", score=0.70)]
        self.assertIn("stale_or_weak", self.categories([make_claim()], evidence))

    def test_high_confidence_on_weak_support_is_flagged(self):
        evidence = [make_evidence("EV-1", "market", score=0.30),
                    make_evidence("EV-2", "news", source_type="major_media", score=0.70)]
        claim = make_claim(confidence={
            "score": 0.75, "level": "high", "type": CONFIDENCE_TYPE_HEURISTIC,
            "components": {key: 0.75 for key in CONFIDENCE_COMPONENT_KEYS}, "limiters": []})

        self.assertIn("confidence_too_high", self.categories([claim], evidence))

    def test_unsupported_direction_is_flagged(self):
        claim = make_claim(supporting_evidence_ids=[], facts=[])
        self.assertIn("unsupported", self.categories([claim], two_sided_evidence()))

    def test_every_documented_category_is_reachable(self):
        """八個類別都必須有 deterministic 偵測路徑，否則離線就等於沒有語意檢查。"""
        reachable = set()
        evidence = [make_evidence("EV-1", "market", score=0.30),
                    make_evidence("EV-2", "news", source_type="major_media", score=0.70)]
        official = [make_evidence("EV-1", "announcement", source_type="official_announcement")]
        onchain = [make_evidence("EV-1", "whale", source_type="blockchain_raw")]

        reachable |= self.categories([make_claim(conclusion="價格必然續漲。", limitations=[])], evidence)
        reachable |= self.categories([make_claim(supporting_evidence_ids=[], facts=[])], evidence)
        reachable |= self.categories([make_claim(inference="因為升息導致價格下跌。")], evidence)
        reachable |= self.categories([make_claim(
            supporting_evidence_ids=["EV-1"], contradicting_evidence_ids=[],
            facts=[{"statement": "公告已發布", "evidence_ids": ["EV-1"]}],
            inference="已帶來營收成長。", conclusion="基本面改善。")], official)
        reachable |= self.categories([make_claim(
            statement="大戶意圖出貨", supporting_evidence_ids=["EV-1"],
            contradicting_evidence_ids=[],
            facts=[{"statement": "大額轉帳", "evidence_ids": ["EV-1"]}])], onchain)
        reachable |= self.categories([make_claim(confidence={
            "score": 0.75, "level": "high", "type": CONFIDENCE_TYPE_HEURISTIC,
            "components": {key: 0.75 for key in CONFIDENCE_COMPONENT_KEYS},
            "limiters": []})], evidence)

        self.assertEqual(set(SEMANTIC_CRITIC_CATEGORIES) - reachable, set())

    def test_semantic_findings_never_fail_the_gate_on_their_own(self):
        claim = make_claim(inference="資金費率上升導致價格上漲。")
        result = gate(two_sided_evidence(), [claim])

        self.assertEqual(result["status"], GATE_STATUS_PASS_WITH_WARNINGS)
        self.assertEqual(result["error_count"], 0)
        self.assertIn("correlation_as_causation", result["semantic_categories"])


class CriticBoundaryTests(unittest.TestCase):
    """Critic 只能標註與下調信心：不能新增證據、不能加分、不能改寫 Evidence。"""

    def test_prompt_and_guide_cover_all_eight_categories(self):
        prompt = build_critic_prompt("ETH", "問題", {"reasoning": {}, "stance": {}}, [])
        for category in SEMANTIC_CRITIC_CATEGORIES:
            self.assertIn(category, CRITIC_CATEGORY_GUIDE, category)
            self.assertIn(category, prompt, category)

    def test_prompt_states_the_critic_permission_boundary(self):
        prompt = build_critic_prompt("ETH", "問題", {"reasoning": {}, "stance": {}}, [])
        self.assertIn("不可新增任何 Evidence", prompt)
        self.assertIn("不可提高信心", prompt)
        self.assertIn("不可改寫或重新解釋原始 Evidence", prompt)

    def test_positive_confidence_adjustment_is_rejected_by_the_adapter(self):
        class BoostingClient:
            def generate_json(self, **kwargs):
                return {"verdict": "pass", "summary": "s", "confidence_adjustment": 0.3,
                        "findings": []}

        with self.assertRaisesRegex(ValueError, "between -1 and 0"):
            critique_with_llm("ETH", "q", {"reasoning": {}, "stance": {}}, [],
                              client=BoostingClient())

    def test_schema_still_requires_a_category_on_every_finding(self):
        item = CRITIC_SCHEMA["properties"]["findings"]["items"]
        self.assertIn("category", item["required"])

    def test_legacy_and_unknown_categories_are_normalised(self):
        self.assertEqual(normalise_semantic_category("confidence"), "confidence_too_high")
        self.assertEqual(normalise_semantic_category("Over Claim"), "over_claim")
        self.assertEqual(normalise_semantic_category("something_new"), "other")
        for category in SEMANTIC_CRITIC_CATEGORIES:
            self.assertEqual(normalise_semantic_category(category), category)

    def test_critic_findings_appear_in_the_gate_without_touching_scores(self):
        critique = {"verdict": "concerns", "summary": "s", "confidence_adjustment": -0.1,
                    "findings": [{"severity": "high", "category": "confidence",
                                  "claim": "偏多結論", "issue": "信心過高",
                                  "evidence_id": "EV-1"}]}
        result = gate(two_sided_evidence(), [make_claim()], critique=critique)

        self.assertEqual(result["status"], GATE_STATUS_PASS_WITH_WARNINGS)
        self.assertIn("confidence_too_high", result["semantic_categories"])
        critic_findings = [item for item in result["semantic_findings"]
                           if item["detected_by"] == "llm_critic"]
        self.assertEqual(len(critic_findings), 1)
        # gate 不從 critique 讀任何分數，只保留類別與指向。
        self.assertNotIn("confidence_adjustment", critic_findings[0])


class TimeoutClient:
    """模擬 Critic 逾時。"""

    def generate_json(self, **kwargs):
        raise TimeoutError("critic call timed out")


class ProposalClient:
    """用 prompt 裡真實存在的第一筆證據組一個合法 Claim 提案，讓圖的來源變成 llm。"""

    def generate_json(self, *, prompt, schema, schema_name, timeout_seconds):
        payload = json.loads(prompt[prompt.index("{"):])
        first = payload["evidence"][0]["evidence_id"]
        return {"claims": [{
            "statement": "ETH 短期動能偏多",
            "facts": [{"statement": "期間報酬為正", "evidence_ids": [first]}],
            "inference": "價格動能為正，構成偏多解讀。",
            "conclusion": "本次證據偏多。",
            "supporting_evidence_ids": [first],
            "contradicting_evidence_ids": [],
        }]}


def offline_llm_run(directory: Path, **kwargs) -> dict:
    """use_llm=True 但所有模型端都注入離線／失敗 client：不觸網路，強制走保守路徑。"""
    return run("ETH", "T5 提交物測試", directory, live=False, use_llm=True,
               planner_client=OfflineLLMClient(), analysis_client=OfflineLLMClient(), **kwargs)


class ArtifactBundleTests(unittest.TestCase):
    """六項提交物、manifest hash、run_id 一致性。"""

    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        cls.output = Path(cls._directory.name)
        cls.result = run("ETH", "T5 離線提交物", cls.output, live=False, use_llm=False)
        cls.manifest = json.loads((cls.output / "manifest.json").read_text(encoding="utf-8"))
        cls.log = json.loads((cls.output / "execution_log.json").read_text(encoding="utf-8"))
        cls.evidence = json.loads((cls.output / "evidence.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()

    def test_all_six_artifacts_exist(self):
        for name in ARTIFACT_FILENAMES.values():
            self.assertTrue((self.output / name).exists(), name)

    def test_manifest_hashes_verify_every_written_file(self):
        listed = {entry["path"] for entry in self.manifest["files"]}
        self.assertEqual(listed, set(ARTIFACT_FILENAMES.values()) - {"manifest.json"})
        for entry in self.manifest["files"]:
            content = (self.output / entry["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), entry["sha256"], entry["path"])
            self.assertEqual(len(content), entry["bytes"], entry["path"])

    def test_manifest_detects_a_tampered_artifact(self):
        """manifest 的用途就是這個：檔案被改過必須看得出來。"""
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run("ETH", "T5 manifest 驗證", output, live=False, use_llm=False)
            manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
            entry = next(item for item in manifest["files"] if item["path"] == "report.md")

            (output / "report.md").write_text("已被改過的報告", encoding="utf-8")
            actual = hashlib.sha256((output / "report.md").read_bytes()).hexdigest()

            self.assertNotEqual(actual, entry["sha256"])

    def test_manifest_records_everything_needed_to_identify_the_run(self):
        self.assertEqual(self.manifest["manifest_version"], MANIFEST_VERSION)
        self.assertEqual(self.manifest["question"], "T5 離線提交物")
        self.assertEqual(self.manifest["coins"], ["ETH"])
        self.assertEqual(self.manifest["mode"], "offline")
        self.assertEqual(self.manifest["execution_flags"], {"live": False, "use_llm": False})
        self.assertTrue(self.manifest["code_commit"])
        self.assertTrue(self.manifest["config_version"])
        self.assertEqual(dict(self.manifest["artifact_filenames"]), dict(ARTIFACT_FILENAMES))
        for key in ("schema", "credibility_scoring", "claim_confidence_scoring",
                    "citation_gate", "source_registry"):
            self.assertTrue(self.manifest["versions"][key], key)
        for stage in ("planner", "analyst", "critic", "claims"):
            self.assertIn("provider", self.manifest["stage_providers"][stage], stage)
        self.assertTrue(self.manifest["started_at"] < self.manifest["completed_at"])

    def test_manifest_reports_the_validation_outcome(self):
        validation = self.manifest["validation"]
        self.assertEqual(validation["evidence_error_count"], 0)
        self.assertEqual(validation["citation_gate_status"], GATE_STATUS_PASS)
        self.assertEqual(validation["citation_gate_error_count"], 0)
        self.assertEqual(set(validation["citation_gate_checks"]), set(CITATION_GATE_CHECKS))

    def test_every_artifact_belongs_to_the_same_run(self):
        run_id = self.manifest["run_id"]
        self.assertEqual(self.result["run_id"], run_id)
        self.assertEqual(self.log["run_id"], run_id)
        for record in self.evidence:
            self.assertEqual(record["run_id"], run_id, record["evidence_id"])

    def test_claims_document_stays_byte_identical_across_runs(self):
        """claims.json 刻意不帶 run_id：兩次相同輸入必須逐字相同，這是計分 deterministic 的證明。"""
        with tempfile.TemporaryDirectory() as first, tempfile.TemporaryDirectory() as second:
            run("ETH", "T5 determinism", Path(first), live=False, use_llm=False)
            run("ETH", "T5 determinism", Path(second), live=False, use_llm=False)
            left = (Path(first) / "claims.json").read_bytes()
            right = (Path(second) / "claims.json").read_bytes()

        self.assertEqual(left, right)


class ComparisonBundleTests(unittest.TestCase):
    """比較題：兩腳各自有完整六檔，另有一份可驗證的 pair manifest。"""

    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        cls.output = Path(cls._directory.name)
        cls.payload = run_comparison("BTC", "ETH", "BTC 與 ETH 哪個風險較低？", cls.output,
                                     live=False, use_llm=False)
        cls.manifest = json.loads((cls.output / "manifest.json").read_text(encoding="utf-8"))

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()

    def test_each_leg_has_all_six_artifacts(self):
        for coin in ("BTC", "ETH"):
            for name in ARTIFACT_FILENAMES.values():
                self.assertTrue((self.output / coin / name).exists(), f"{coin}/{name}")

    def test_pair_manifest_hashes_verify_and_reference_both_legs(self):
        for entry in self.manifest["files"]:
            content = (self.output / entry["path"]).read_bytes()
            self.assertEqual(hashlib.sha256(content).hexdigest(), entry["sha256"], entry["path"])
        for coin in ("BTC", "ETH"):
            leg = self.manifest["legs"][coin]
            self.assertEqual(leg["run_id"], self.payload["results"][coin]["run_id"])
            self.assertTrue((self.output / leg["manifest"]).exists())
            self.assertEqual(leg["citation_gate_status"], GATE_STATUS_PASS)

    def test_each_leg_keeps_its_own_run_id(self):
        run_ids = {coin: self.payload["results"][coin]["run_id"] for coin in ("BTC", "ETH")}
        self.assertNotEqual(run_ids["BTC"], run_ids["ETH"])
        self.assertNotIn(self.manifest["run_id"], run_ids.values())
        for coin, run_id in run_ids.items():
            records = json.loads(
                (self.output / coin / "evidence.json").read_text(encoding="utf-8"))
            for record in records:
                self.assertEqual(record["run_id"], run_id, f"{coin}/{record['evidence_id']}")


class ExecutionLogTests(unittest.TestCase):
    """Execution Log 必須足以重建主要流程。"""

    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        cls.output = Path(cls._directory.name)
        run("ETH", "T5 執行記錄", cls.output, live=False, use_llm=False)
        cls.log = json.loads((cls.output / "execution_log.json").read_text(encoding="utf-8"))
        cls.steps = {step["name"]: step for step in cls.log["steps"]}

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()

    def test_every_step_records_its_own_wall_clock_window(self):
        for name, step in self.steps.items():
            self.assertIsNotNone(step["started_at"], name)
            self.assertIsNotNone(step["completed_at"], name)
            self.assertIsInstance(step["duration_ms"], float, name)
            self.assertGreaterEqual(step["duration_ms"], 0.0, name)
            self.assertTrue(step["tool"], name)

    def test_collection_step_records_locators_and_created_evidence_ids(self):
        step = self.steps["collect_evidence"]
        self.assertTrue(step["evidence_ids_created"])
        self.assertEqual(len(step["collectors"]), len(step["evidence_ids_created"]))
        for record in step["collectors"]:
            self.assertTrue(record["collector"])
            self.assertTrue(record["source_locator"])
            self.assertTrue(record["query_summary"])
            self.assertTrue(record["status"])

    def test_provider_and_model_are_recorded_per_stage(self):
        for stage in ("planner", "analyst", "critic", "claims"):
            self.assertIn("provider", self.log["stage_providers"][stage], stage)
        self.assertEqual(self.log["stage_providers"]["analyst"]["status"], "offline_fallback")
        self.assertEqual(self.log["stage_providers"]["claims"]["scored_by"], "deterministic_rules")

    def test_citation_gate_result_is_in_the_log(self):
        step = self.steps["citation_gate"]
        self.assertEqual(step["status"], GATE_STATUS_PASS)
        self.assertEqual(step["gate_version"], CITATION_GATE_VERSION)
        self.assertEqual(set(step["checks"]), set(CITATION_GATE_CHECKS))
        self.assertEqual(self.log["citation_gate"]["status"], GATE_STATUS_PASS)

    def test_gate_runs_after_claims_and_before_the_report_is_declared_done(self):
        names = [step["name"] for step in self.log["steps"]]
        self.assertLess(names.index("build_claims"), names.index("citation_gate"))
        self.assertLess(names.index("citation_gate"), names.index("generate_report"))


class CriticFailureStillProducesOutputTests(unittest.TestCase):
    """Critic 逾時不得讓正式執行失去產出。"""

    def test_critic_timeout_keeps_all_six_artifacts_and_a_conservative_report(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = offline_llm_run(output, critic_client=TimeoutClient())
            log = json.loads((output / "execution_log.json").read_text(encoding="utf-8"))
            report = (output / "report.md").read_text(encoding="utf-8")

            for name in ARTIFACT_FILENAMES.values():
                self.assertTrue((output / name).exists(), name)
            self.assertIsNone(result["critique"])
            critic = next(step for step in log["steps"] if step["name"] == "critic_review")
            self.assertEqual(critic["status"], "fallback:TimeoutError")
            self.assertEqual(result["citation_gate"]["status"], GATE_STATUS_PASS)
            # 報告必須自己說出語意稽核沒跑，而不是安靜地少一段。
            self.assertIn("語意稽核未執行", report)
            self.assertIn("fallback:TimeoutError", report)
            self.assertIn("## Citation Gate", report)

    def test_gate_failure_on_a_model_graph_degrades_instead_of_raising(self):
        """模型提案過不了 gate 時，整批作廢改用 deterministic 圖，而不是照樣發佈或直接中止。"""
        failing = dict(gate(two_sided_evidence(), [make_claim(supporting_evidence_ids=["EV-404"])]))
        passing = dict(gate(two_sided_evidence(), [make_claim()]))
        with tempfile.TemporaryDirectory() as directory, \
             patch("src.orchestrator.run_citation_gate", side_effect=[failing, passing]):
            result = run("ETH", "T5 gate 降級", Path(directory), live=False, use_llm=False,
                         claim_client=ProposalClient())

        self.assertEqual(result["citation_gate"]["status"], GATE_STATUS_PASS)
        self.assertEqual(result["claim_graph"]["source"], GRAPH_SOURCE_FALLBACK)
        self.assertIn("citation gate failed", result["claim_graph"]["fallback_reason"])
        self.assertTrue(result["claims"])

    def test_persistent_gate_failure_stops_the_run(self):
        failing = gate(two_sided_evidence(), [make_claim(supporting_evidence_ids=["EV-404"])])
        with tempfile.TemporaryDirectory() as directory, \
             patch("src.orchestrator.run_citation_gate", return_value=dict(failing)):
            with self.assertRaisesRegex(ValueError, "Citation gate failed"):
                run("ETH", "T5 gate 中止", Path(directory), live=False, use_llm=False)


class DeterministicReportTests(unittest.TestCase):
    """報告骨架由程式決定，相同輸入逐字相同。"""

    def payload(self) -> dict:
        return {
            "run": {"run_id": RUN_ID, "started_at": "2026-08-01T00:00:00+00:00",
                    "completed_at": "2026-08-01T00:00:10+00:00", "as_of": "2026-08-01T00:00:00+00:00",
                    "mode": "offline", "live": False, "use_llm": False,
                    "question": "ETH 近期狀況？", "coins": ["ETH"],
                    "provider": "deterministic", "model": None},
            "plan": {"time_window": {"days": 14, "source": "default"}, "task_modes": ["describe_market_state"]},
            "stance": {"label": "偏多", "label_en": "Bullish", "basis": "多方領先",
                       "bull_weight": 2.0, "bear_weight": 0.5, "signal_count": 3,
                       "drivers": [{"text": "報酬為正", "evidence_id": "EV-1", "weight": 1.0}]},
            "reasoning": {"market_judgment": "偏多", "confidence": 0.5, "facts": ["f1"],
                          "inferences": ["i1"], "conclusion": "c1", "counter_evidence": ["ce1"],
                          "observation_points": ["op1"]},
            "indicators": {"return_pct": 8.2},
            "claim_graph": {"claims": [make_claim()], "scoring_version": "claim-confidence-v1"},
            "consistency": {"label": "部分一致", "basis": "b", "agreement_pct": 66.7,
                            "domain_count": 3, "directional_domain_count": 3, "domain_sides": {}},
            "critique": None, "critic_status": "skipped:disabled",
            "citation_gate": {"status": GATE_STATUS_PASS, "gate_version": CITATION_GATE_VERSION,
                              "claim_count": 1, "evidence_count": 2, "cited_evidence_count": 2,
                              "checks": {}, "errors": [], "warnings": [], "semantic_findings": []},
            "evidence": [{"evidence_id": "EV-1", "source": "TestSource",
                          "source_url": "https://example.com/EV-1", "fetched_at": FETCHED_AT,
                          "reliability_score": 0.9, "verification_status": "unverified",
                          "source_type": "market_api", "content_reference": {"endpoint": "x"},
                          "related_claim_ids": ["CL-001"]}],
            "evidence_window": {"earliest_fetched_at": FETCHED_AT, "latest_fetched_at": FETCHED_AT},
            "risk_factors": ["r1"],
        }

    def test_identical_input_renders_byte_identical_markdown(self):
        self.assertEqual(render_competition_report(self.payload()),
                         render_competition_report(self.payload()))

    def test_missing_sections_degrade_instead_of_raising(self):
        markdown = render_competition_report({})
        self.assertIn("# 未指定標的 Market Research", markdown)
        self.assertIn("N/A", markdown)

    def test_section_order_is_fixed(self):
        markdown = render_competition_report(self.payload())
        expected = ["## Question", "## 分析標的與題目", "## 資料截止與分析區間", "## Stance",
                    "## Market Judgment", "## 關鍵依據", "## Facts", "## Inferences",
                    "## Conclusion", "## Claims", "## 跨來源一致程度", "## Critic Review",
                    "## Citation Gate", "## Confidence", "## Indicators", "## Counter Evidence",
                    "## Evidence Sources", "## Next Observations", "## Risks and Limitations",
                    "## 可能推翻結論的條件"]
        positions = [markdown.index(heading) for heading in expected]
        self.assertEqual(positions, sorted(positions))

    def test_report_covers_every_required_submission_item(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run("ETH", "T5 報告內容", output, live=False, use_llm=False)
            report = (output / "report.md").read_text(encoding="utf-8")

        for required in ("## 分析標的與題目", "## 資料截止與分析區間", "## Stance", "## 關鍵依據",
                         "## Claims", "- 事實：", "- 推論：", "- 結論：", "- 支持證據：",
                         "- 反方證據：", "## 跨來源一致程度", "- 信心分量：", "## Evidence Sources",
                         "## Risks and Limitations", "## 可能推翻結論的條件", "## Next Observations",
                         "not investment advice"):
            self.assertIn(required, report, required)

    def test_rejected_evidence_is_excluded_from_the_numbered_citations(self):
        payload = self.payload()
        payload["evidence"].append({
            "evidence_id": "EV-REJ", "source": "Bad", "source_url": "https://example.com/rej",
            "fetched_at": FETCHED_AT, "reliability_score": 0.0, "verification_status": "rejected",
            "source_type": "unknown", "content_reference": {}, "related_claim_ids": []})
        markdown = render_competition_report(payload)

        self.assertIn("[#1] EV-1", markdown)
        self.assertNotIn("[#2] EV-REJ", markdown)
        self.assertIn("已排除的證據", markdown)


class CrossSourceConsistencyTests(unittest.TestCase):
    """跨來源一致程度以「領域」為單位，而不是證據筆數。"""

    def test_one_domain_alone_is_not_a_cross_source_confirmation(self):
        evidence = [make_evidence("EV-1", "market"), make_evidence("EV-2", "vegas_channel")]
        signals = [{"side": "bull", "evidence_id": "EV-1", "weight": 1.0},
                   {"side": "bull", "evidence_id": "EV-2", "weight": 1.0}]
        consistency = _source_consistency(signals, evidence)

        self.assertEqual(consistency["state"], "insufficient")
        self.assertEqual(consistency["directional_domain_count"], 1)

    def test_agreeing_domains_are_consistent(self):
        evidence = [make_evidence("EV-1", "market"), make_evidence("EV-2", "derivatives"),
                    make_evidence("EV-3", "onchain")]
        signals = [{"side": "bull", "evidence_id": f"EV-{index}", "weight": 1.0}
                   for index in (1, 2, 3)]
        consistency = _source_consistency(signals, evidence)

        self.assertEqual(consistency["state"], "consistent")
        self.assertEqual(consistency["agreement_pct"], 100.0)

    def test_opposing_domains_are_reported_as_conflicting(self):
        evidence = [make_evidence("EV-1", "market"), make_evidence("EV-2", "derivatives")]
        signals = [{"side": "bull", "evidence_id": "EV-1", "weight": 1.0},
                   {"side": "bear", "evidence_id": "EV-2", "weight": 1.0}]
        consistency = _source_consistency(signals, evidence)

        self.assertEqual(consistency["state"], "conflicting")

    def test_no_directional_signal_is_insufficient(self):
        consistency = _source_consistency([], [make_evidence("EV-1", "market")])
        self.assertEqual(consistency["state"], "insufficient")
        self.assertEqual(consistency["agreement_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()
