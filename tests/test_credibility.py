"""T3：Evidence credibility 引擎的行為測試。

重點不是「分數看起來合理」，而是幾條不能被繞過的規則：hard cap 一定生效、fallback 不會拿高分、
同源轉載不會灌高獨立性、舊新聞不會因為剛抓下來就變新鮮、相同輸入必得相同輸出。
所有測試都傳入固定的 ``now``，確保與執行時間無關。
"""

from __future__ import annotations

import json
import unittest
from dataclasses import asdict
from datetime import datetime, timedelta, timezone
from pathlib import Path

from src.credibility import (
    CONSERVATIVE_ENTRY,
    DEFAULT_REGISTRY_PATH,
    FRESHNESS_CAP_WITHOUT_EVENT_TIME,
    group_by_lineage,
    lineage_id_for,
    load_source_registry,
    registry_entry,
    score_evidence,
    score_evidence_batch,
)
from src.day1_mvp import Evidence
from src.schemas import (
    CREDIBILITY_COMPONENT_KEYS,
    CREDIBILITY_WEIGHTS,
    HARD_CAPS,
    SCORING_VERSION,
    SOURCE_TYPES,
)

NOW = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
REGISTRY = load_source_registry()


def iso(delta_days: float) -> str:
    """相對 NOW 的時間字串；正數代表多久以前。"""
    return (NOW - timedelta(days=delta_days)).isoformat()


def make_evidence(**overrides) -> Evidence:
    """建立一筆有完整 metadata 的 Evidence，測試再逐項覆寫要驗的欄位。"""
    fields = {
        "evidence_id": "EV-001",
        "source": "ExampleFeed",
        "source_url": "https://feed.example.com/articles/1",
        "fetched_at": iso(0),
        "data_type": "news",
        "coin": "ETH",
        "time_range": "14d",
        "content": {"headline": "ETH 網路活動上升"},
        "reliability_score": 0.5,
    }
    fields.update(overrides)
    return Evidence(**fields)


class SourceRegistryTest(unittest.TestCase):
    def test_registry_file_covers_all_source_types(self):
        self.assertTrue(DEFAULT_REGISTRY_PATH.exists())
        configured = REGISTRY["source_types"]
        for source_type in SOURCE_TYPES:
            self.assertIn(source_type, configured, source_type)
            entry = registry_entry(REGISTRY, source_type)
            for key in ("source_quality", "traceability", "method_transparency", "independence_base"):
                self.assertGreaterEqual(entry[key], 0.0)
                self.assertLessEqual(entry[key], 1.0)
            self.assertIn("first_party", entry)
            self.assertIn("is_secondary_news", entry)
            self.assertIn("freshness_policy", entry)

    def test_missing_source_type_falls_back_to_conservative_entry(self):
        """registry 缺該來源時走保守預設，不拋例外。"""
        empty_registry = {"default": {}, "source_types": {}}
        entry = registry_entry(empty_registry, "market_api")
        self.assertFalse(entry["registry_hit"])
        self.assertEqual(entry["source_quality"], CONSERVATIVE_ENTRY["source_quality"])

        result = score_evidence(make_evidence(source_type="market_api"), empty_registry, now=NOW)
        self.assertGreater(result["final_score"], 0.0)
        self.assertIn("registry 未登錄此 source_type，改用保守基準", result["notes"])

    def test_broken_registry_path_still_returns_usable_registry(self):
        registry = load_source_registry(Path("config") / "does-not-exist.json")
        self.assertEqual(registry["source_types"], {})
        result = score_evidence(make_evidence(source_type="market_api"), registry, now=NOW)
        self.assertEqual(result["scoring_version"], SCORING_VERSION)

    def test_unknown_source_type_string_is_normalised(self):
        result = score_evidence(make_evidence(source_type="totally-bogus"), REGISTRY, now=NOW)
        self.assertEqual(result["source_type"], "unknown")


class HardCapTest(unittest.TestCase):
    def test_missing_source_locator_caps_at_030(self):
        evidence = make_evidence(source_url="", source_type="major_media", published_at=iso(0))
        result = score_evidence(evidence, REGISTRY, now=NOW)
        self.assertIn("missing_source_locator", result["score_limiters"])
        self.assertEqual(result["hard_cap"], HARD_CAPS["missing_source_locator"])
        self.assertLessEqual(result["final_score"], 0.30)
        self.assertGreater(result["raw_score"], result["final_score"])

    def test_missing_fetched_at_is_rejected(self):
        evidence = make_evidence(fetched_at="", source_type="major_media", published_at=iso(0))
        result = score_evidence(evidence, REGISTRY, now=NOW)
        self.assertIn("missing_fetched_at", result["score_limiters"])
        self.assertTrue(result["rejected"])
        self.assertEqual(result["final_score"], 0.0)
        self.assertEqual(result["verification_status"], "rejected")

    def test_fallback_fixture_caps_at_020(self):
        evidence = make_evidence(
            source_type="fallback_fixture",
            source_url="https://example.com/offline-fixture",
            content={"note": "offline fixture", "symbol": "ETHUSDT"},
        )
        result = score_evidence(evidence, REGISTRY, now=NOW)
        self.assertIn("fallback_fixture", result["score_limiters"])
        self.assertLessEqual(result["final_score"], HARD_CAPS["fallback_fixture"])

    def test_content_fallback_flag_also_triggers_cap(self):
        evidence = make_evidence(source_type="market_api", content={"symbol": "ETHUSDT", "fallback": True})
        result = score_evidence(evidence, REGISTRY, now=NOW)
        self.assertIn("fallback_fixture", result["score_limiters"])
        self.assertLessEqual(result["final_score"], HARD_CAPS["fallback_fixture"])

    def test_single_secondary_news_source_caps_at_060(self):
        evidence = make_evidence(source_type="secondary_media", published_at=iso(0))
        result = score_evidence(evidence, REGISTRY, now=NOW)
        self.assertIn("single_secondary_news_source", result["score_limiters"])
        self.assertLessEqual(result["final_score"], HARD_CAPS["single_secondary_news_source"])

    def test_secondary_news_with_independent_lineage_is_not_capped(self):
        secondary = make_evidence(
            evidence_id="EV-101", source_type="secondary_media",
            source_url="https://aggregator.example.com/story-a", published_at=iso(0),
            content={"headline": "聚合站報導 A"},
        )
        major = make_evidence(
            evidence_id="EV-102", source="MajorPaper", source_type="major_media",
            source_url="https://major.example.org/story-b", published_at=iso(0),
            content={"headline": "主要媒體報導 B"},
        )
        results = score_evidence_batch([secondary, major], REGISTRY, now=NOW)
        self.assertNotIn("single_secondary_news_source", results[0]["score_limiters"])

    def test_anonymous_social_caps_at_035(self):
        evidence = make_evidence(
            source_type="social_public", data_type="social", published_at=iso(0),
            content={"posts": [{"title": "ETH 討論", "url": "https://social.example.com/p/1"}]},
        )
        result = score_evidence(evidence, REGISTRY, now=NOW)
        self.assertIn("anonymous_or_low_trace_social", result["score_limiters"])
        self.assertLessEqual(result["final_score"], HARD_CAPS["anonymous_or_low_trace_social"])

    def test_unverifiable_entity_attribution_caps_at_060(self):
        evidence = make_evidence(
            source_type="blockchain_raw", data_type="whale",
            content={"address": "0xabc0000000000000000000000000000000000001",
                     "label": "Demo Exchange Wallet"},
        )
        result = score_evidence(evidence, REGISTRY, now=NOW)
        self.assertIn("unverifiable_entity_attribution", result["score_limiters"])
        self.assertNotIn("unverifiable_intent_attribution", result["score_limiters"])
        self.assertLessEqual(result["final_score"], HARD_CAPS["unverifiable_entity_attribution"])

    def test_unverifiable_intent_attribution_caps_at_055(self):
        evidence = make_evidence(
            source_type="blockchain_raw", data_type="whale",
            content={"transaction_hash": "0xfeed0000000000000000000000000000000000000000000000000000000000ff",
                     "intent": "疑似準備拋售"},
        )
        result = score_evidence(evidence, REGISTRY, now=NOW)
        self.assertIn("unverifiable_intent_attribution", result["score_limiters"])
        self.assertLessEqual(result["final_score"], HARD_CAPS["unverifiable_intent_attribution"])
        self.assertEqual(result["hard_cap"], HARD_CAPS["unverifiable_intent_attribution"])

    def test_verified_attribution_removes_cap(self):
        evidence = make_evidence(
            source_type="blockchain_raw", data_type="whale", verification_status="verified",
            content={"address": "0xabc0000000000000000000000000000000000001",
                     "label": "Demo Exchange Wallet"},
        )
        result = score_evidence(evidence, REGISTRY, now=NOW)
        self.assertNotIn("unverifiable_entity_attribution", result["score_limiters"])

    def test_hard_cap_uses_the_strictest_applicable_cap(self):
        evidence = make_evidence(
            source_url="", source_type="secondary_media", published_at=iso(0),
        )
        result = score_evidence(evidence, REGISTRY, now=NOW)
        self.assertIn("missing_source_locator", result["score_limiters"])
        self.assertIn("single_secondary_news_source", result["score_limiters"])
        self.assertEqual(result["hard_cap"], HARD_CAPS["missing_source_locator"])
        self.assertLessEqual(result["final_score"], HARD_CAPS["missing_source_locator"])


class FreshnessTest(unittest.TestCase):
    def test_stale_news_is_not_refreshed_by_recent_fetch(self):
        """剛抓下來的舊新聞不得得到高 freshness。"""
        stale = make_evidence(source_type="major_media", published_at=iso(200), fetched_at=iso(0))
        fresh = make_evidence(source_type="major_media", published_at=iso(0), fetched_at=iso(0))
        stale_result = score_evidence(stale, REGISTRY, now=NOW)
        fresh_result = score_evidence(fresh, REGISTRY, now=NOW)

        self.assertEqual(stale_result["freshness_basis"], "published_at")
        self.assertLessEqual(stale_result["components"]["freshness"], 0.15)
        self.assertLess(stale_result["components"]["freshness"], fresh_result["components"]["freshness"])
        self.assertLess(stale_result["final_score"], fresh_result["final_score"])

    def test_event_time_takes_priority_over_published_at(self):
        evidence = make_evidence(source_type="major_media", event_time=iso(200), published_at=iso(0))
        result = score_evidence(evidence, REGISTRY, now=NOW)
        self.assertEqual(result["freshness_basis"], "event_time")
        self.assertLessEqual(result["components"]["freshness"], 0.15)

    def test_news_without_event_time_is_capped_on_freshness(self):
        evidence = make_evidence(source_type="major_media", fetched_at=iso(0))
        result = score_evidence(evidence, REGISTRY, now=NOW)
        self.assertEqual(result["freshness_basis"], "fetched_at")
        self.assertLessEqual(result["components"]["freshness"], FRESHNESS_CAP_WITHOUT_EVENT_TIME)

    def test_market_api_snapshot_may_use_fetched_at(self):
        evidence = make_evidence(
            source_type="market_api", data_type="market", fetched_at=iso(0),
            content={"symbol": "ETHUSDT", "interval": "1d", "prices": [1, 2, 3]},
        )
        result = score_evidence(evidence, REGISTRY, now=NOW)
        self.assertEqual(result["freshness_basis"], "fetched_at")
        self.assertEqual(result["components"]["freshness"], 1.0)


class LineageTest(unittest.TestCase):
    @staticmethod
    def _reposts(count: int) -> list:
        """count 篇引用同一原始消息的轉載，來自不同網域。"""
        return [
            make_evidence(
                evidence_id="EV-{:03d}".format(index + 1),
                source="Aggregator{}".format(index + 1),
                source_url="https://aggregator{}.example.com/story".format(index + 1),
                source_type="secondary_media",
                published_at=iso(0),
                content={
                    "headline": "轉載：ETH 網路活動上升",
                    "original_source_url": "https://origin.example.org/eth-activity",
                },
            )
            for index in range(count)
        ]

    def test_twenty_reposts_collapse_into_one_lineage(self):
        reposts = self._reposts(20)
        groups = group_by_lineage(reposts)
        self.assertEqual(len(groups), 1)
        self.assertEqual(len(next(iter(groups.values()))), 20)
        self.assertEqual(len({lineage_id_for(item) for item in reposts}), 1)

    def test_reposts_reduce_independence(self):
        reposts = self._reposts(20)
        batch = score_evidence_batch(reposts, REGISTRY, now=NOW)
        alone = score_evidence(reposts[0], REGISTRY, now=NOW)

        self.assertEqual(batch[0]["lineage_size"], 20)
        self.assertLess(batch[0]["components"]["independence"], alone["components"]["independence"])
        self.assertLess(batch[0]["independence_factor"], 1.0)
        self.assertLessEqual(batch[0]["final_score"], alone["final_score"])
        # 同源轉載不構成多個獨立確認：cap 仍然套用。
        self.assertIn("single_secondary_news_source", batch[0]["score_limiters"])
        self.assertEqual(len({item["source_lineage_id"] for item in batch}), 1)

    def test_explicit_lineage_id_is_respected(self):
        first = make_evidence(evidence_id="EV-001", source_lineage_id="lin:manual:same-story")
        second = make_evidence(
            evidence_id="EV-002", source_url="https://other.example.net/x",
            source_lineage_id="lin:manual:same-story",
        )
        self.assertEqual(lineage_id_for(first), "lin:manual:same-story")
        self.assertEqual(len(group_by_lineage([first, second])), 1)

    def test_distinct_stories_stay_separate(self):
        first = make_evidence(evidence_id="EV-001", content={"headline": "ETH 網路活動上升"})
        second = make_evidence(
            evidence_id="EV-002", source_url="https://feed.example.com/articles/2",
            content={"headline": "ETH 開發者活動持平"},
        )
        self.assertEqual(len(group_by_lineage([first, second])), 2)

    def test_transaction_hash_groups_onchain_evidence(self):
        tx = "0xabc0000000000000000000000000000000000000000000000000000000000001"
        first = make_evidence(evidence_id="EV-001", source_type="blockchain_raw", content={"transaction_hash": tx})
        second = make_evidence(
            evidence_id="EV-002", source="Explorer", source_url="https://explorer.example.io/tx",
            source_type="blockchain_raw", content={"tx_hash": tx},
        )
        self.assertEqual(len(group_by_lineage([first, second])), 1)


class DeterminismAndShapeTest(unittest.TestCase):
    def test_same_input_gives_exactly_the_same_score(self):
        evidence = make_evidence(source_type="major_media", published_at=iso(3))
        first = score_evidence(evidence, REGISTRY, now=NOW)
        second = score_evidence(evidence, REGISTRY, now=NOW.isoformat())
        third = score_evidence(asdict(evidence), REGISTRY, now=NOW)
        self.assertEqual(first, second)
        self.assertEqual(first, third)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(third, sort_keys=True))

    def test_batch_matches_single_scoring_for_independent_evidence(self):
        market = make_evidence(
            evidence_id="EV-001", source_type="market_api", data_type="market",
            source_url="https://api.example.com/ohlcv", content={"symbol": "ETHUSDT", "interval": "1d"},
        )
        batch = score_evidence_batch([market], REGISTRY, now=NOW)
        single = score_evidence(market, REGISTRY, now=NOW)
        self.assertEqual(batch[0]["final_score"], single["final_score"])
        self.assertEqual(batch[0]["components"], single["components"])

    def test_batch_preserves_input_order(self):
        items = [
            make_evidence(evidence_id="EV-001", content={"headline": "A"}),
            make_evidence(evidence_id="EV-002", content={"headline": "B"}),
            make_evidence(evidence_id="EV-003", content={"headline": "C"}),
        ]
        results = score_evidence_batch(items, REGISTRY, now=NOW)
        self.assertEqual([item["evidence_id"] for item in results], ["EV-001", "EV-002", "EV-003"])

    def test_components_and_limiters_are_json_serialisable(self):
        evidence = make_evidence(source_url="", source_type="secondary_media")
        result = score_evidence(evidence, REGISTRY, now=NOW)
        encoded = json.dumps(result, ensure_ascii=False)
        decoded = json.loads(encoded)

        self.assertEqual(tuple(result["components"]), CREDIBILITY_COMPONENT_KEYS)
        self.assertEqual(set(decoded["components"]), set(CREDIBILITY_COMPONENT_KEYS))
        for value in decoded["components"].values():
            self.assertIsInstance(value, float)
            self.assertGreaterEqual(value, 0.0)
            self.assertLessEqual(value, 1.0)
        for limiter in decoded["score_limiters"]:
            self.assertIn(limiter, HARD_CAPS)
        self.assertIsInstance(decoded["notes"], list)
        self.assertEqual(decoded["scoring_version"], SCORING_VERSION)

    def test_raw_score_matches_declared_weights(self):
        evidence = make_evidence(
            source_type="market_api", data_type="market",
            source_url="https://api.example.com/ohlcv",
            content={"symbol": "ETHUSDT", "interval": "1d"},
        )
        result = score_evidence(evidence, REGISTRY, now=NOW)
        expected = round(
            sum(CREDIBILITY_WEIGHTS[key] * result["components"][key] for key in CREDIBILITY_COMPONENT_KEYS),
            4,
        )
        self.assertEqual(result["raw_score"], expected)
        self.assertLessEqual(result["final_score"], result["raw_score"])

    def test_scoring_does_not_mutate_input_evidence(self):
        evidence = make_evidence(source_type="major_media", published_at=iso(1))
        before = asdict(evidence)
        score_evidence(evidence, REGISTRY, now=NOW)
        self.assertEqual(asdict(evidence), before)
        self.assertEqual(evidence.score_breakdown, {})
        self.assertEqual(evidence.score_limiters, [])
        self.assertEqual(evidence.scoring_version, "")

    def test_illegal_verification_status_is_normalised(self):
        evidence = make_evidence(verification_status="totally-verified")
        result = score_evidence(evidence, REGISTRY, now=NOW)
        self.assertEqual(result["verification_status"], "unverified")

    def test_fallback_never_outscores_first_party_data(self):
        fallback = make_evidence(
            evidence_id="EV-001", source_type="fallback_fixture",
            source_url="https://example.com/offline", content={"symbol": "ETHUSDT"},
        )
        market = make_evidence(
            evidence_id="EV-002", source_type="market_api", data_type="market",
            source_url="https://api.example.com/ohlcv", content={"symbol": "ETHUSDT", "interval": "1d"},
        )
        results = score_evidence_batch([fallback, market], REGISTRY, now=NOW)
        self.assertLess(results[0]["final_score"], results[1]["final_score"])


if __name__ == "__main__":
    unittest.main()
