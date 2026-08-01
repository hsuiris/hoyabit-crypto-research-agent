"""domain 詞彙表回歸測試。

背景（實測缺陷，非假想）：`required_domains` 由 Planner 產出、由 claim_graph 當成
domain_coverage 的分母，兩端各自維護詞彙表。Bedrock（nova-lite）在正式部署上回傳的是
``market_data／news_events／social_sentiment／technical_analysis``，與 claim_graph 的
``market／news／social／onchain／derivatives／macro`` 完全對不上，交集是空集合，
coverage 靜靜地變成 0.0，於是每個 Claim 都被判成 `insufficient_evidence`。

沒有任何錯誤訊息、沒有 warning、Citation Gate 也是 PASS —— 報告只會說「資料不足」。
這組測試把兩件事釘住：

1. 兩端共用同一組字面值（`CLAIM_DOMAINS` 是唯一定義來源）。
2. 詞彙表對不上時不會靜默歸零，而且修法沒有打開「把 plan 寫窄換高分」的後門。
"""

from __future__ import annotations

import unittest

from src.claim_graph import (
    DEFAULT_REQUIRED_DOMAINS,
    DOMAIN_BY_DATA_TYPE,
    KNOWN_LIMITERS,
    LIMITER_LOW_DOMAIN_COVERAGE,
    LIMITER_PLAN_DOMAIN_VOCABULARY,
    MIN_REQUIRED_DOMAIN_COUNT,
    EvidencePool,
    evaluate_claim,
)
from src.planner import build_research_plan
from src.schemas import CLAIM_DOMAINS, MIN_DOMAIN_COVERAGE, normalise_domains

# 正式部署（us-west-2／amazon.nova-lite-v1:0）實際回傳的 required_domains。
BEDROCK_OBSERVED_DOMAINS = ["market_data", "news_events", "social_sentiment", "technical_analysis"]


def _evidence(evidence_id: str, data_type: str, *, quality: float = 0.8775) -> dict:
    """一筆足以參與計分的證據；lineage 各自獨立，避免同源收斂干擾本測試。"""
    return {
        "evidence_id": evidence_id,
        "data_type": data_type,
        "source": evidence_id,
        "source_url": f"https://example.com/{evidence_id}",
        "reliability_score": quality,
        "claim_relevance": 1.0,
        "independence_factor": 1.0,
        "source_type": "market_api",
        "content": {"canonical_url": f"https://example.com/{evidence_id}"},
    }


class VocabularyIsSharedTests(unittest.TestCase):
    """兩端必須共用同一組 domain 字面值，否則交集會靜默歸零。"""

    def test_claim_domains_matches_the_evidence_domain_values(self):
        self.assertEqual(
            sorted(CLAIM_DOMAINS), sorted(set(DOMAIN_BY_DATA_TYPE.values())),
            "CLAIM_DOMAINS 與 DOMAIN_BY_DATA_TYPE 的值域分岔會讓 domain_coverage 歸零",
        )

    def test_default_required_domains_are_canonical(self):
        for domain in DEFAULT_REQUIRED_DOMAINS:
            self.assertIn(domain, CLAIM_DOMAINS)

    def test_every_alias_target_is_canonical(self):
        from src.schemas import DOMAIN_ALIASES

        for alias, domain in DOMAIN_ALIASES.items():
            self.assertIn(domain, CLAIM_DOMAINS, f"別名 {alias!r} 指向未定義的 domain {domain!r}")
            self.assertNotIn(alias, CLAIM_DOMAINS, f"{alias!r} 已是 canonical，不該再列為別名")

    def test_vocabulary_limiter_is_known(self):
        self.assertIn(LIMITER_PLAN_DOMAIN_VOCABULARY, KNOWN_LIMITERS)


class NormaliseDomainsTests(unittest.TestCase):
    def test_bedrock_observed_domains_are_mapped(self):
        self.assertEqual(normalise_domains(BEDROCK_OBSERVED_DOMAINS), ["market", "news", "social"])

    def test_canonical_names_pass_through_unchanged(self):
        names = ["market", "news", "social", "onchain", "derivatives", "macro"]
        self.assertEqual(normalise_domains(names), names)

    def test_separator_and_case_variants_collapse(self):
        self.assertEqual(normalise_domains(["On-Chain", "on chain", "ONCHAIN"]), ["onchain"])

    def test_planner_sub_labels_map_to_their_domain(self):
        """Planner 的關鍵字表比 domain 細；announcement 屬 news、whale 屬 onchain。"""
        self.assertEqual(normalise_domains(["announcement"]), ["news"])
        self.assertEqual(normalise_domains(["whale"]), ["onchain"])

    def test_order_is_preserved_and_deduped(self):
        self.assertEqual(normalise_domains(["news", "market_data", "news_events", "price"]),
                         ["news", "market"])

    def test_unrecognised_names_are_dropped_not_guessed(self):
        self.assertEqual(normalise_domains(["foo", "bar", ""]), [])

    def test_none_is_tolerated(self):
        self.assertEqual(normalise_domains(None), [])


class PlannerEmitsCanonicalDomainsTests(unittest.TestCase):
    """落地的 research_plan.json 必須已經是 canonical，讀者不需要自己換算。"""

    def test_llm_plan_domains_are_normalised(self):
        class _Client:
            def generate_json(self, **kwargs):
                return {
                    "coins": ["ETH"],
                    "task_modes": ["describe_market_state"],
                    "primary_question": "q",
                    "time_window": {"days": 14, "source": "default"},
                    "hypotheses": [],
                    "required_domains": BEDROCK_OBSERVED_DOMAINS,
                    "comparison_dimensions": [],
                    "assumptions": [],
                    "stop_conditions": {"max_evidence": 36, "max_followup_rounds": 1},
                }

        plan, log = build_research_plan("分析 ETH 近兩週市場狀況", ["ETH"], client=_Client())

        self.assertEqual(log["path"], "llm")
        self.assertEqual(plan.required_domains, ["market", "news", "social"])
        for domain in plan.required_domains:
            self.assertIn(domain, CLAIM_DOMAINS)

    def test_fallback_plan_domains_are_canonical(self):
        """關鍵字命中公告與巨鯨時，落地的仍是 news／onchain。"""
        plan, _ = build_research_plan("ETH 官方公告與巨鯨動向對鏈上有什麼影響？", ["ETH"])

        for domain in plan.required_domains:
            self.assertIn(domain, CLAIM_DOMAINS)
        self.assertIn("news", plan.required_domains)
        self.assertIn("onchain", plan.required_domains)

    def test_schema_constrains_domains_to_the_vocabulary(self):
        from src.planner import RESEARCH_PLAN_SCHEMA

        self.assertEqual(
            RESEARCH_PLAN_SCHEMA["properties"]["required_domains"]["items"]["enum"],
            list(CLAIM_DOMAINS),
        )


class CoverageNoLongerSilentlyZeroTests(unittest.TestCase):
    """缺陷本體：詞彙表對不上時 coverage 不得歸零。"""

    def test_multi_domain_claim_is_no_longer_scored_as_insufficient(self):
        """四個領域的證據配上模型的近義詞 plan —— 修正前 coverage 是 0.0。"""
        pool = EvidencePool([
            _evidence("EV-M", "market"), _evidence("EV-N", "news"),
            _evidence("EV-S", "social"), _evidence("EV-O", "onchain"),
        ])
        result = evaluate_claim(
            pool,
            supporting_ids=["EV-M", "EV-N", "EV-O"],
            contradicting_ids=["EV-S"],
            required_domains=BEDROCK_OBSERVED_DOMAINS,
        )

        self.assertEqual(result["confidence"]["components"]["domain_coverage"], 1.0)
        self.assertNotIn(LIMITER_LOW_DOMAIN_COVERAGE, result["confidence"]["limiters"])
        self.assertNotEqual(result["verdict"], "insufficient_evidence")

    def test_plan_requested_domains_keeps_the_raw_names(self):
        """分母換成 canonical，但 plan 原本寫了什麼必須留下痕跡。"""
        pool = EvidencePool([_evidence("EV-M", "market"), _evidence("EV-N", "news")])
        result = evaluate_claim(pool, supporting_ids=["EV-M", "EV-N"],
                                required_domains=BEDROCK_OBSERVED_DOMAINS)

        self.assertEqual(result["plan_requested_domains"], BEDROCK_OBSERVED_DOMAINS)
        for domain in result["required_domains"]:
            self.assertIn(domain, CLAIM_DOMAINS)

    def test_evidence_domain_outside_the_plan_still_counts(self):
        """第二條歸零路徑：plan 沒列到的領域，證據用上了就必須進分母也進分子。"""
        pool = EvidencePool([_evidence("EV-TVL", "tvl")])  # tvl → onchain，plan 沒要求
        result = evaluate_claim(pool, supporting_ids=["EV-TVL"],
                                required_domains=BEDROCK_OBSERVED_DOMAINS)

        self.assertIn("onchain", result["required_domains"])
        self.assertEqual(result["covered_domains"], ["onchain"])
        self.assertGreater(result["confidence"]["components"]["domain_coverage"], 0.0)

    def test_single_domain_evidence_is_still_insufficient(self):
        """修法不得放寬誠實性：單一領域仍必須判資料不足。"""
        pool = EvidencePool([_evidence("EV-TVL", "tvl")])
        result = evaluate_claim(pool, supporting_ids=["EV-TVL"],
                                required_domains=BEDROCK_OBSERVED_DOMAINS)

        self.assertLess(result["confidence"]["components"]["domain_coverage"], MIN_DOMAIN_COVERAGE)
        self.assertEqual(result["verdict"], "insufficient_evidence")

    def test_unknown_data_type_does_not_pad_the_coverage(self):
        """未知 data_type 的 `other` 不進分母也不進分子，不能靠它換高分。"""
        pool = EvidencePool([_evidence("EV-M", "market"), _evidence("EV-X", "something_new")])
        result = evaluate_claim(pool, supporting_ids=["EV-M", "EV-X"],
                                required_domains=["market", "news", "social"])

        self.assertNotIn("other", result["required_domains"])
        self.assertEqual(result["covered_domains"], ["market"])


class UnrecognisedPlanDomainsTests(unittest.TestCase):
    """plan 的領域名稱全部無法對應時，退回預設分母並留下痕跡。"""

    def _case(self, required_domains):
        pool = EvidencePool([_evidence("EV-M", "market"), _evidence("EV-N", "news")])
        return evaluate_claim(pool, supporting_ids=["EV-M"], contradicting_ids=["EV-N"],
                              required_domains=required_domains)

    def test_unrecognised_plan_domains_fall_back_to_the_defaults(self):
        result = self._case(["foo", "bar"])

        self.assertIn(LIMITER_PLAN_DOMAIN_VOCABULARY, result["confidence"]["limiters"])
        self.assertEqual(result["plan_requested_domains"], ["foo", "bar"])
        for domain in DEFAULT_REQUIRED_DOMAINS:
            self.assertIn(domain, result["required_domains"])

    def test_unrecognised_plan_domains_cannot_raise_confidence(self):
        """退回預設是最寬的分母，不能比合法 plan 更容易得分。"""
        legitimate = self._case(list(DEFAULT_REQUIRED_DOMAINS))
        garbage = self._case(["foo", "bar"])

        self.assertLessEqual(garbage["confidence"]["score"], legitimate["confidence"]["score"])

    def test_empty_plan_does_not_flag_the_vocabulary_limiter(self):
        """沒有 plan 與 plan 寫錯是兩件事，痕跡不能混在一起。"""
        result = self._case([])

        self.assertNotIn(LIMITER_PLAN_DOMAIN_VOCABULARY, result["confidence"]["limiters"])

    def test_aliases_collapsing_to_one_domain_still_hit_the_floor(self):
        """market_data／trading_volume／technical_analysis 全歸 market，分母下限必須生效。"""
        pool = EvidencePool([_evidence("EV-M", "market")])
        result = evaluate_claim(
            pool, supporting_ids=["EV-M"],
            required_domains=["market_data", "trading_volume", "technical_analysis"])

        self.assertEqual(len(result["required_domains"]), MIN_REQUIRED_DOMAIN_COUNT)
        self.assertLess(result["confidence"]["components"]["domain_coverage"], MIN_DOMAIN_COVERAGE)
        self.assertEqual(result["verdict"], "insufficient_evidence")

    def test_scoring_is_deterministic(self):
        first = self._case(BEDROCK_OBSERVED_DOMAINS)
        second = self._case(BEDROCK_OBSERVED_DOMAINS)

        self.assertEqual(first["confidence"], second["confidence"])
        self.assertEqual(first["required_domains"], second["required_domains"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
