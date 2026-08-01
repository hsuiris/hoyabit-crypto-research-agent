"""T5 函式庫層：Deterministic Report Renderer（Track B4）。

只驗證「渲染規則」，不驗證文案細節：段落順序必須固定、缺資料一律安全降級、
insufficient_evidence 不得渲染成方向性結論、rejected Evidence 不得出現在主要段落、
相同輸入必須逐字相同輸出。測試資料以手寫 dict 構造，形狀對齊
`docs/competition-tasks/T5-citation-output.md` 與 `src/schemas.py`，
不 import `src.claim_graph`（避免耦合到 T4 的實作細節，只耦合到資料形狀契約）。
"""

from __future__ import annotations

import unittest

from src.report_renderer import render_report
from src.schemas import VERDICT_INSUFFICIENT_EVIDENCE


def make_evidence(evidence_id, source="TestSource", url="https://example.com/a",
                  fetched_at="2026-01-14T00:00:00+00:00", reliability=0.80,
                  verification_status="unverified"):
    return {
        "evidence_id": evidence_id,
        "source": source,
        "source_url": url,
        "fetched_at": fetched_at,
        "data_type": "market",
        "coin": "ETH",
        "time_range": "14d",
        "content": {"note": "fixture"},
        "reliability_score": reliability,
        "verification_status": verification_status,
    }


def make_confidence(score=0.72, level="high", limiters=None):
    return {
        "score": score,
        "level": level,
        "type": "heuristic",
        "components": {
            "weighted_evidence_quality": 0.8,
            "domain_coverage": 0.75,
            "source_diversity": 0.66,
            "signal_consistency": 0.9,
            "counter_evidence_coverage": 0.5,
        },
        "limiters": limiters or [],
    }


def make_claim(claim_id="CL-001", statement="ETH 近期訊號偏多", verdict="supported",
              supporting=("EV-001",), contradicting=("EV-002",), confidence=None):
    return {
        "claim_id": claim_id,
        "statement": statement,
        "claim_type": "market_judgment",
        "verdict": verdict,
        "facts": [
            {"statement": "期間報酬為正", "evidence_ids": list(supporting)},
        ],
        "inference": "價格動能為正，支持偏多判讀。",
        "conclusion": "整體訊號偏多。",
        "supporting_evidence_ids": list(supporting),
        "contradicting_evidence_ids": list(contradicting),
        "confidence": confidence or make_confidence(),
        "limitations": ["樣本區間有限。"],
        "invalidation_conditions": ["反向訊號權重超過支持側時即被推翻。"],
        "watchpoints": ["追蹤下一輪資金費率變化。"],
    }


def make_full_input():
    return {
        "run": {
            "run_id": "RUN-001",
            "started_at": "2026-01-14T00:00:00+00:00",
            "completed_at": "2026-01-14T00:10:00+00:00",
            "question": "近期市場訊號呈現什麼風險？",
            "coins": ["ETH"],
            "mode": "single_coin",
        },
        "plan": {
            "coins": ["ETH"],
            "task_modes": ["describe_market_state", "identify_risks"],
            "primary_question": "近期市場訊號呈現什麼風險？",
            "time_window": {"days": 14, "source": "default"},
            "hypotheses": [
                {
                    "hypothesis_id": "H1",
                    "statement": "ETH 短期會延續漲勢",
                    "support_questions": ["是否有量能支持？"],
                    "contradiction_questions": ["是否有反向資金流？"],
                    "falsification_conditions": ["跌破關鍵支撐即推翻。"],
                },
            ],
            "required_domains": ["market", "news"],
            "comparison_dimensions": [],
            "assumptions": ["沿用預設 14 天時間窗。"],
            "stop_conditions": {"max_evidence": 36, "max_followup_rounds": 1},
        },
        "evidence": [
            make_evidence("EV-001", source="CoinGecko"),
            make_evidence("EV-002", source="Binance"),
        ],
        "claims_document": {
            "scoring_version": "claim-confidence-v1",
            "confidence_type": "heuristic",
            "claim_source": "deterministic_fallback",
            "fallback_reason": "",
            "claims": [make_claim()],
            "hypothesis_assessments": [
                {
                    "hypothesis_id": "H1",
                    "claim_id": "CL-001",
                    "statement": "ETH 短期會延續漲勢",
                    "support_strength": 0.8,
                    "contradiction_strength": 0.2,
                    "verdict": "supported",
                    "confidence": 0.72,
                    "independent_support_chains": 2,
                },
            ],
            "rejected_evidence_ids": [],
        },
        "critique": {
            "status": "PASS_WITH_WARNINGS",
            "flags": [
                {"category": "stale_or_weak", "claim_id": "CL-001", "note": "部分證據時效偏舊。"},
            ],
            "confidence_adjustment": -0.05,
        },
    }


# 段落標題順序：新增段落時務必連同這個清單一起更新，這是本模組「結構固定」承諾的具體檢查點。
_EXPECTED_HEADER_ORDER = [
    "## 分析標的與題目",
    "## 資料截止與時間範圍",
    "## 研究計畫摘要",
    "## 立場與判斷總覽",
    "## 判斷 1：",
    "## 假設檢驗",
    "## 稽核（Critic）",
    "## 證據來源",
    "## 免責聲明",
]


class RenderReportStructureTests(unittest.TestCase):
    """1. 完整輸入 → 段落順序與標題固定。"""

    def test_full_input_has_fixed_section_order(self):
        markdown = render_report(make_full_input())
        positions = []
        for header in _EXPECTED_HEADER_ORDER:
            index = markdown.find(header)
            self.assertGreaterEqual(index, 0, "missing section header: %r" % header)
            positions.append(index)
        self.assertEqual(positions, sorted(positions), "section headers out of fixed order")

    def test_full_input_starts_with_h1_title(self):
        markdown = render_report(make_full_input())
        self.assertTrue(markdown.startswith("# ETH 市場研究報告"))

    def test_full_input_cites_evidence_with_footnote_numbers(self):
        markdown = render_report(make_full_input())
        self.assertIn("EV-001[#1]", markdown)
        self.assertIn("EV-002[#2]", markdown)


class RenderReportMissingDataTests(unittest.TestCase):
    """2/3. 缺 claims／critique 時要安全降級，不拋例外。"""

    def test_missing_claims_shows_insufficient_message_without_exception(self):
        data = make_full_input()
        data.pop("claims_document")
        data.pop("claims", None)
        markdown = render_report(data)
        self.assertIn("資料不足", markdown)
        self.assertIn("insufficient_evidence", markdown)

    def test_missing_critique_shows_na(self):
        data = make_full_input()
        data.pop("critique")
        markdown = render_report(data)
        section = markdown.split("## 稽核（Critic）", 1)[1]
        self.assertIn("N/A", section)

    def test_completely_empty_input_does_not_raise(self):
        try:
            markdown = render_report({})
        except Exception as error:  # noqa: BLE001 - 這裡就是要確認渲染器完全不拋例外
            self.fail("render_report raised on empty input: %r" % error)
        self.assertIsInstance(markdown, str)
        self.assertIn("未指定標的", markdown)

    def test_none_input_does_not_raise(self):
        try:
            markdown = render_report(None)
        except Exception as error:  # noqa: BLE001
            self.fail("render_report raised on None input: %r" % error)
        self.assertIsInstance(markdown, str)


class RenderReportComparisonTests(unittest.TestCase):
    """4. Comparison 模式（兩幣）可渲染。"""

    def test_comparison_mode_renders_both_coins(self):
        data = make_full_input()
        data["run"]["coins"] = ["ETH", "BTC"]
        data["run"]["mode"] = "comparison"
        data["plan"]["coins"] = ["ETH", "BTC"]
        data["plan"]["task_modes"] = ["compare_assets"]
        data["plan"]["comparison_dimensions"] = ["price_performance", "volatility"]
        markdown = render_report(data)
        self.assertIn("# ETH vs BTC 市場研究報告", markdown)
        self.assertIn("price_performance", markdown)


class RenderReportDeterminismTests(unittest.TestCase):
    """5. 相同輸入呼叫兩次 → 逐字相同。"""

    def test_same_input_produces_identical_output(self):
        data = make_full_input()
        first = render_report(data)
        second = render_report(make_full_input())
        self.assertEqual(first, second)

    def test_rendering_does_not_mutate_input(self):
        data = make_full_input()
        before = render_report(data)
        after_first_call = render_report(data)
        self.assertEqual(before, after_first_call)


class RenderReportInsufficientEvidenceTests(unittest.TestCase):
    """6. insufficient_evidence 的呈現不含方向性結論字樣。"""

    def test_insufficient_evidence_claim_has_no_directional_wording(self):
        data = make_full_input()
        insufficient_claim = make_claim(
            claim_id="CL-002",
            statement="ETH：本次證據不足以形成方向判斷",
            verdict=VERDICT_INSUFFICIENT_EVIDENCE,
            supporting=(),
            contradicting=(),
            confidence=make_confidence(score=0.2, level="low", limiters=["no_supporting_evidence"]),
        )
        # insufficient claim 本身不該帶有方向性推論／結論文字，即使上游不小心塞了也要被渲染器蓋掉。
        insufficient_claim["inference"] = "訊號強烈偏多，價格即將上漲。"
        insufficient_claim["conclusion"] = "強烈看多。"
        data["claims_document"]["claims"] = [insufficient_claim]
        markdown = render_report(data)

        self.assertIn("insufficient_evidence", markdown)
        self.assertIn("證據不足", markdown)
        self.assertNotIn("強烈看多", markdown)
        self.assertNotIn("即將上漲", markdown)

    def test_insufficient_evidence_claim_marked_with_warning(self):
        data = make_full_input()
        insufficient_claim = make_claim(
            verdict=VERDICT_INSUFFICIENT_EVIDENCE,
            supporting=(), contradicting=(),
            confidence=make_confidence(score=0.1, level="low"),
        )
        data["claims_document"]["claims"] = [insufficient_claim]
        markdown = render_report(data)
        self.assertIn("本判斷資料不足", markdown)


class RenderReportRejectedEvidenceTests(unittest.TestCase):
    """7. rejected evidence 不出現在主要段落。"""

    def test_rejected_evidence_excluded_from_sources_and_citations(self):
        data = make_full_input()
        data["evidence"].append(make_evidence("EV-REJECTED", source="LowTraceSocial",
                                              verification_status="rejected"))
        data["claims_document"]["rejected_evidence_ids"] = ["EV-REJECTED"]
        # 即使有 Claim 意外引用了被 reject 的 ID，渲染器也必須把它從引用中濾掉，不得原樣顯示。
        data["claims_document"]["claims"][0]["supporting_evidence_ids"].append("EV-REJECTED")
        markdown = render_report(data)

        self.assertNotIn("EV-REJECTED", markdown.split("## 證據來源", 1)[1].split("## 免責聲明")[0])
        claim_section = markdown.split("## 判斷 1：", 1)[1].split("## 假設檢驗", 1)[0]
        self.assertNotIn("EV-REJECTED", claim_section)


class RenderReportLegacyFixtureTests(unittest.TestCase):
    """8. 舊 fixture（缺 T3 新欄位）仍可渲染。"""

    def test_legacy_evidence_without_t3_fields_renders(self):
        legacy_evidence = {
            "evidence_id": "EV-LEGACY-001",
            "source": "CoinGecko",
            "source_url": "https://api.coingecko.com/api/v3/coins/ethereum",
            "fetched_at": "2026-07-31T12:42:56.089037+00:00",
            "data_type": "market",
            "coin": "ETH",
            "time_range": "14d",
            "content": {"return_pct": 2.63},
            "reliability_score": 0.9,
            # 沒有 source_type / verification_status / score_breakdown 等 T3 欄位。
        }
        data = {
            "run": {"run_id": "RUN-LEGACY", "question": "舊 fixture 是否仍可渲染？", "coins": ["ETH"]},
            "evidence": [legacy_evidence],
            "claims_document": {
                "claim_source": "deterministic_fallback",
                "claims": [make_claim(supporting=("EV-LEGACY-001",), contradicting=())],
            },
        }
        try:
            markdown = render_report(data)
        except Exception as error:  # noqa: BLE001
            self.fail("render_report raised on legacy fixture: %r" % error)
        self.assertIn("EV-LEGACY-001[#1]", markdown)
        self.assertIn("EV-LEGACY-001", markdown.split("## 證據來源", 1)[1])

    def test_legacy_claim_without_confidence_field_renders(self):
        legacy_claim = {
            "claim_id": "CL-LEGACY",
            "statement": "舊格式 Claim",
            "verdict": "supported",
            "facts": [],
            "inference": "舊格式沒有 confidence 欄位。",
            "conclusion": "仍應可渲染。",
            "supporting_evidence_ids": [],
            "contradicting_evidence_ids": [],
            # 沒有 confidence / limitations / invalidation_conditions / watchpoints。
        }
        data = {"claims_document": {"claims": [legacy_claim]}}
        try:
            markdown = render_report(data)
        except Exception as error:  # noqa: BLE001
            self.fail("render_report raised on legacy claim: %r" % error)
        self.assertIn("舊格式 Claim", markdown)


if __name__ == "__main__":
    unittest.main()
