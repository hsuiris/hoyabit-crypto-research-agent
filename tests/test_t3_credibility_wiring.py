"""T3：credibility 引擎接進既有管線之後的行為測試。

`tests/test_credibility.py` 驗的是引擎本身的算術；這個檔案驗的是**接線**：

* Collector 有沒有給出正確的 source_type 與資料本身的時間；
* Orchestrator 是否在任何模組讀到 `reliability_score` 之前就把分數算完並覆寫；
* 降級與 fixture 是否一定被標成非實證，且拿不到高分；
* 同一則消息的轉載不會被算成多個獨立確認；
* validator 是否擋得住「分數與展開不一致」或「突破 hard cap」的證據。

所有測試都離線執行：網路呼叫一律 patch，時間一律傳入固定 `now`。
"""

import json
import tempfile
import time
import unittest
from dataclasses import MISSING, asdict, fields
from datetime import datetime, timedelta, timezone
from pathlib import Path
from unittest.mock import patch
from urllib.error import URLError

from src.credibility import load_source_registry, score_evidence
from src.day1_mvp import Evidence, mock_evidence
from src.day2_sources import (
    DATA_TYPE_SOURCE_TYPES,
    _SOURCE_LOADERS,
    _annotate_news_lineage,
    _fallback_evidence,
    collect_evidence_detailed,
    fetch_official_announcements,
    stamp_credibility_metadata,
)
from src.orchestrator import enrich_and_score_evidence, run
from src.schemas import (
    CREDIBILITY_COMPONENT_KEYS,
    HARD_CAPS,
    SOURCE_TYPES,
    VERIFICATION_STATUSES,
)
from src.validation import validate_evidence

NOW = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
REGISTRY = load_source_registry()

RSS_FEED = b"""<?xml version="1.0"?><rss version="2.0"><channel>
<item><title>Ethereum Foundation Board Update</title><link>https://blog.ethereum.org/a</link><pubDate>Wed, 29 Jul 2026 00:00:00 GMT</pubDate></item>
<item><title>Protocol Update</title><link>https://blog.ethereum.org/b</link><pubDate>Tue, 28 Jul 2026 00:00:00 GMT</pubDate></item>
</channel></rss>"""


def iso(days_ago: float) -> str:
    return (NOW - timedelta(days=days_ago)).isoformat()


def make_evidence(**overrides) -> Evidence:
    fields_ = {
        "evidence_id": "EV-T3-001",
        "source": "ExampleSource",
        "source_url": "https://example.org/records/1",
        "fetched_at": iso(0),
        "data_type": "market",
        "coin": "ETH",
        "time_range": "14d",
        "content": {},
        "reliability_score": 0.5,
    }
    fields_.update(overrides)
    return Evidence(**fields_)


def scored(evidence: Evidence) -> dict:
    return score_evidence(evidence, REGISTRY, now=NOW)


class SourceTypeMappingTest(unittest.TestCase):
    def test_every_collected_source_has_a_registry_category(self):
        for label, _ in _SOURCE_LOADERS:
            self.assertIn(label, DATA_TYPE_SOURCE_TYPES, label)
        for label in ("price_history",):
            self.assertIn(label, DATA_TYPE_SOURCE_TYPES, label)
        for data_type, source_type in DATA_TYPE_SOURCE_TYPES.items():
            self.assertIn(source_type, SOURCE_TYPES, data_type)

    def test_unmapped_data_type_stays_unknown_rather_than_guessing(self):
        evidence = stamp_credibility_metadata(make_evidence(data_type="brand_new_signal"))
        self.assertEqual(evidence.source_type, "unknown")

    def test_chain_and_whale_evidence_are_both_raw_chain_reads(self):
        self.assertEqual(DATA_TYPE_SOURCE_TYPES["onchain"], "blockchain_raw")
        self.assertEqual(DATA_TYPE_SOURCE_TYPES["whale"], "blockchain_raw")

    def test_news_is_classified_as_syndication_not_first_party(self):
        self.assertEqual(DATA_TYPE_SOURCE_TYPES["news"], "secondary_media")

    @patch("src.day2_sources._get_bytes", return_value=RSS_FEED)
    def test_first_party_announcement_is_official_and_syndication_is_not(self, _get_bytes):
        first_party = stamp_credibility_metadata(fetch_official_announcements("ETH"))
        self.assertTrue(first_party.content["first_party"])
        self.assertEqual(first_party.source_type, "official_announcement")

        # XRP／BNB 的第一方來源是 GitHub 上的參考客戶端發布 feed；取不到時 OFFICIAL_FEEDS 會退回
        # 官方網域限定的 Google News 查詢。那是二手聚合，不得被當成官方聲明計分。
        def only_google_news(url, *args, **kwargs):
            if "github.com" in url:
                raise URLError("release feed unavailable")
            return RSS_FEED

        with patch("src.day2_sources._get_bytes", side_effect=only_google_news):
            syndicated = stamp_credibility_metadata(fetch_official_announcements("XRP"))
        self.assertFalse(syndicated.content["first_party"])
        self.assertEqual(syndicated.source_type, "secondary_media")

    @patch("src.day2_sources._get_bytes", return_value=RSS_FEED)
    def test_release_feed_is_still_scored_as_an_official_announcement(self, _get_bytes):
        evidence = stamp_credibility_metadata(fetch_official_announcements("BNB"))

        self.assertTrue(evidence.content["first_party"])
        self.assertEqual(evidence.source_type, "official_announcement")

    def test_explicit_source_type_is_never_overwritten(self):
        evidence = stamp_credibility_metadata(
            make_evidence(data_type="market", source_type="fallback_fixture"))
        self.assertEqual(evidence.source_type, "fallback_fixture")

    def test_stamping_is_idempotent(self):
        first = stamp_credibility_metadata(make_evidence(data_type="news", content={
            "items": [{"title": "Same story", "url": "https://a.example/1", "published": iso(1)}]}))
        before = asdict(first)
        stamp_credibility_metadata(first)
        self.assertEqual(asdict(first), before)


class FallbackAndFixtureTest(unittest.TestCase):
    def test_every_fallback_label_is_marked_unavailable_fixture(self):
        for label, _ in _SOURCE_LOADERS:
            fallback = _fallback_evidence(label, "ETH", ConnectionError("boom"))
            self.assertIsNotNone(fallback, label)
            self.assertEqual(fallback.source_type, "fallback_fixture", label)
            self.assertEqual(fallback.verification_status, "unavailable", label)
            self.assertLessEqual(scored(fallback)["final_score"], HARD_CAPS["fallback_fixture"], label)

    def test_offline_fixtures_are_labelled_as_fixtures(self):
        for item in mock_evidence("ETH"):
            self.assertEqual(item.source_type, "fallback_fixture", item.evidence_id)
            self.assertEqual(item.verification_status, "fallback", item.evidence_id)

    def test_collected_offline_evidence_is_stamped_and_capped(self):
        evidence, log, _ = collect_evidence_detailed("ETH", live=False)
        self.assertEqual(log, ["mock_mode"])
        for item in evidence:
            self.assertTrue(item.source_lineage_id, item.evidence_id)
            self.assertLessEqual(scored(item)["final_score"], HARD_CAPS["fallback_fixture"])

    def test_fallback_never_outranks_a_real_first_party_read(self):
        real = make_evidence(data_type="onchain", source_type="blockchain_raw",
                             content={"chain": "Ethereum", "latest_block_hex": "0x1"},
                             content_reference={"endpoint": "https://ethereum.publicnode.com"})
        fallback = _fallback_evidence("onchain", "ETH", ConnectionError("boom"))
        self.assertGreater(scored(real)["final_score"], scored(fallback)["final_score"])


class TimeSemanticsTest(unittest.TestCase):
    """資料本身的時間必須與「我什麼時候抓的」分開，否則舊新聞剛抓下來就變新鮮。"""

    def test_news_published_at_comes_from_the_newest_item(self):
        evidence = stamp_credibility_metadata(make_evidence(data_type="news", content={"items": [
            {"title": "older", "url": "https://a.example/1", "published": "Mon, 02 Feb 2026 00:00:00 GMT"},
            {"title": "newer", "url": "https://a.example/2", "published": "Fri, 20 Feb 2026 00:00:00 GMT"},
        ]}))
        self.assertTrue(evidence.published_at.startswith("2026-02-20"))
        self.assertIsNone(evidence.event_time)

    def test_stale_news_is_not_refreshed_by_a_fresh_fetch(self):
        stale = stamp_credibility_metadata(make_evidence(
            evidence_id="EV-STALE", data_type="news", fetched_at=iso(0),
            content={"items": [{"title": "two years old", "url": "https://a.example/1",
                                "published": iso(730)}]}))
        fresh = stamp_credibility_metadata(make_evidence(
            evidence_id="EV-FRESH", data_type="news", fetched_at=iso(0),
            content={"items": [{"title": "yesterday", "url": "https://a.example/2",
                                "published": iso(1)}]}))
        stale_record, fresh_record = scored(stale), scored(fresh)
        self.assertEqual(stale_record["freshness_basis"], "published_at")
        self.assertLess(stale_record["components"]["freshness"], fresh_record["components"]["freshness"])
        self.assertLess(stale_record["final_score"], fresh_record["final_score"])

    def test_social_event_time_accepts_epoch_seconds(self):
        created = NOW - timedelta(hours=6)
        evidence = stamp_credibility_metadata(make_evidence(data_type="social", content={
            "posts": [{"title": "thread", "created_utc": created.timestamp()}]}))
        self.assertTrue(evidence.event_time.startswith(created.strftime("%Y-%m-%dT%H")))
        self.assertEqual(scored(evidence)["freshness_basis"], "event_time")

    def test_macro_event_time_comes_from_the_index_history(self):
        evidence = stamp_credibility_metadata(make_evidence(data_type="macro", content={
            "fear_greed_history": [{"time": "2026-02-15", "value": 47},
                                   {"time": "2026-02-28", "value": 52}]}))
        self.assertTrue(evidence.event_time.startswith("2026-02-28"))

    def test_market_event_time_comes_from_the_last_observed_day(self):
        evidence = stamp_credibility_metadata(make_evidence(data_type="market", content={
            "dates": ["2026-02-14", "2026-02-28"], "prices": [1, 2]}))
        self.assertTrue(evidence.event_time.startswith("2026-02-28"))


class NewsLineageTest(unittest.TestCase):
    """同一則消息被 20 家轉載，是一個確認，不是 20 個。"""

    def test_twenty_reposts_of_one_story_collapse_to_one_lineage(self):
        items = [{"title": "Exchange lists ETH staking product",
                  "url": f"https://outlet{index}.example/story", "published": iso(1)}
                 for index in range(20)]
        summary = _annotate_news_lineage(items)
        self.assertEqual(summary["independent_story_count"], 1)
        self.assertEqual(summary["duplicate_item_count"], 19)
        self.assertEqual(len({item["lineage_id"] for item in items}), 1)
        self.assertFalse(items[0]["duplicate_of_lineage"])
        self.assertTrue(all(item["duplicate_of_lineage"] for item in items[1:]))

    def test_distinct_stories_stay_independent(self):
        items = [{"title": "Story A", "url": "https://a.example/1", "published": iso(1)},
                 {"title": "Story B", "url": "https://b.example/1", "published": iso(1)}]
        summary = _annotate_news_lineage(items)
        self.assertEqual(summary["independent_story_count"], 2)
        self.assertEqual(summary["duplicate_item_count"], 0)

    def test_news_evidence_carries_the_lineage_summary(self):
        evidence = stamp_credibility_metadata(make_evidence(data_type="news", content={"items": [
            {"title": "Same wire copy", "url": f"https://outlet{index}.example/x", "published": iso(1)}
            for index in range(3)
        ]}))
        self.assertEqual(evidence.content["independent_story_count"], 1)
        self.assertEqual(evidence.content["duplicate_item_count"], 2)
        self.assertTrue(evidence.source_lineage_id)


class SemanticLimitTest(unittest.TestCase):
    """鏈上、官方與社群證據能證明什麼，必須反映在分數上限裡。"""

    def _transfer(self, **content):
        return make_evidence(
            evidence_id="EV-TRANSFER", data_type="onchain", source_type="blockchain_raw",
            source_url="https://etherscan.io/tx/0xabc",
            content={"transaction_hash": "0xabc", "value_eth": 12000, **content},
            content_reference={"endpoint": "https://ethereum.publicnode.com", "chain": "Ethereum"},
        )

    def test_transfer_itself_is_high_confidence(self):
        record = scored(self._transfer())
        self.assertEqual(record["score_limiters"], [])
        self.assertGreater(record["final_score"], 0.70)

    def test_attributing_the_transfer_to_an_owner_is_capped(self):
        record = scored(self._transfer(label="Binance cold wallet"))
        self.assertIn("unverifiable_entity_attribution", record["score_limiters"])
        self.assertLessEqual(record["final_score"], HARD_CAPS["unverifiable_entity_attribution"])

    def test_attributing_selling_intent_is_capped_harder_than_ownership(self):
        record = scored(self._transfer(intent="preparing to sell"))
        self.assertIn("unverifiable_intent_attribution", record["score_limiters"])
        self.assertLessEqual(record["final_score"], HARD_CAPS["unverifiable_intent_attribution"])
        self.assertLess(HARD_CAPS["unverifiable_intent_attribution"],
                        HARD_CAPS["unverifiable_entity_attribution"])

    @patch("src.day2_sources._get_bytes", return_value=RSS_FEED)
    def test_official_announcement_proves_publication_not_outcome(self, _get_bytes):
        evidence = stamp_credibility_metadata(fetch_official_announcements("ETH"))
        record = scored(evidence)
        # 高可信的是「官方發布了這則公告」；商業成果需要另外的 Evidence，所以狀態不得自動升級。
        self.assertEqual(evidence.verification_status, "unverified")
        self.assertEqual(record["verification_status"], "unverified")
        self.assertTrue(any("不證明商業成果" in note for note in record["known_limitations"]))

    def test_anonymous_social_discussion_is_capped(self):
        evidence = stamp_credibility_metadata(make_evidence(
            data_type="social", source_url="https://www.reddit.com/search.json?q=ETH",
            content={"sentiment": "mixed", "posts": [
                {"title": "ETH thread", "url": "https://www.reddit.com/r/x/1", "score": 12}]}))
        record = scored(evidence)
        self.assertEqual(evidence.source_type, "social_public")
        self.assertIn("anonymous_or_low_trace_social", record["score_limiters"])
        self.assertLessEqual(record["final_score"], HARD_CAPS["anonymous_or_low_trace_social"])
        self.assertTrue(any("不能單獨確認外部事件" in note for note in record["known_limitations"]))


class OrchestratorWiringTest(unittest.TestCase):
    def test_scoring_overwrites_the_adapter_hint_and_keeps_it_for_audit(self):
        evidence = [make_evidence(data_type="market", reliability_score=0.99, content={
            "dates": ["2026-02-28"], "prices": [100.0], "interval": "1d"},
            content_reference={"endpoint": "https://api.example/market", "query": {"days": 14}})]
        records = enrich_and_score_evidence(evidence, registry=REGISTRY, now=NOW)
        item = evidence[0]
        self.assertEqual(item.reliability_score, records[0]["final_score"])
        self.assertNotEqual(item.reliability_score, 0.99)
        self.assertEqual(item.score_breakdown["legacy_reliability_hint"], 0.99)
        self.assertEqual(set(item.score_breakdown["components"]), set(CREDIBILITY_COMPONENT_KEYS))
        self.assertEqual(item.score_breakdown["weights"]["source_quality"], 0.30)
        self.assertEqual(item.scoring_version, item.score_breakdown["scoring_version"])
        self.assertIn(item.verification_status, VERIFICATION_STATUSES)

    def test_same_input_and_same_now_give_exactly_the_same_scores(self):
        def build():
            return [
                make_evidence(evidence_id="EV-A", data_type="market",
                              content={"dates": ["2026-02-28"], "prices": [1.0]}),
                make_evidence(evidence_id="EV-B", data_type="news",
                              content={"items": [{"title": "Story", "url": "https://a.example/1",
                                                  "published": iso(2)}]}),
                make_evidence(evidence_id="EV-C", data_type="social",
                              content={"posts": [{"title": "thread", "created_utc": NOW.timestamp()}]}),
            ]

        first = enrich_and_score_evidence(build(), registry=REGISTRY, now=NOW)
        second = enrich_and_score_evidence(build(), registry=REGISTRY, now=NOW)
        self.assertEqual(json.dumps(first, sort_keys=True), json.dumps(second, sort_keys=True))

    def test_scored_breakdown_is_json_serialisable(self):
        evidence = [make_evidence(data_type="news", content={
            "items": [{"title": "Story", "url": "https://a.example/1", "published": iso(1)}]})]
        enrich_and_score_evidence(evidence, registry=REGISTRY, now=NOW)
        json.dumps(asdict(evidence[0]))  # must not raise

    def test_offline_run_explains_every_evidence_score(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run("ETH", "T3 接線測試", output, live=False, use_llm=False)

            records = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
            for record in records:
                breakdown = record["score_breakdown"]
                self.assertEqual(breakdown["final_score"], record["reliability_score"])
                self.assertEqual(set(breakdown["components"]), set(CREDIBILITY_COMPONENT_KEYS))
                self.assertTrue(record["source_lineage_id"])
                self.assertIn("scored_at", breakdown)

            report = (output / "report.md").read_text(encoding="utf-8")
            sources_section = report.split("## 證據來源", 1)[1]
            self.assertIn("原始", sources_section)
            self.assertIn("分量 source_quality=", sources_section)
            self.assertIn("類別 fallback_fixture", sources_section)

            self.assertEqual(result["credibility"]["scoring_version"], "credibility-v1")
            self.assertEqual(result["credibility"]["evidence_count"], len(records))

            # 離線執行的證據全是 fixture，報告必須自己講出這件事。
            limitations = report.split("## 風險與限制", 1)[1]
            self.assertIn("離線 fixture 或降級來源", limitations)
            self.assertIn("不足以支撐方向性結論", limitations)

    def test_execution_log_records_deterministic_scoring_before_reasoning(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run("ETH", "T3 執行記錄", output, live=False, use_llm=False)
            log = json.loads((output / "execution_log.json").read_text(encoding="utf-8"))
            names = [step["name"] for step in log["steps"]]
            self.assertIn("score_evidence", names)
            # 分數必須在任何分析之前算完，LLM 才沒有機會決定它。
            self.assertLess(names.index("score_evidence"), names.index("llm_reasoning"))
            self.assertLess(names.index("collect_evidence"), names.index("score_evidence"))
            step = next(entry for entry in log["steps"] if entry["name"] == "score_evidence")
            self.assertEqual(step["scored_by"], "deterministic_rules")
            self.assertEqual(step["status"], "degraded")  # offline fixtures are degraded evidence
            self.assertEqual(step["limiter_counts"]["fallback_fixture"], len(step["fallback_ids"]))
            self.assertEqual(step["rejected_ids"], [])

    def test_degraded_live_run_keeps_fallbacks_visible_but_never_high_scoring(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            run("ETH", "T3 降級測試", output, live=True, use_llm=False,
                deadline=time.monotonic() - 1)
            records = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
            self.assertTrue(records)
            for record in records:
                self.assertEqual(record["source_type"], "fallback_fixture")
                self.assertIn(record["verification_status"], {"unavailable", "fallback"})
                self.assertLessEqual(record["reliability_score"], HARD_CAPS["fallback_fixture"])

    def test_history_csv_is_the_substantive_evidence_in_an_offline_run(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run("ETH", "T3 離線實證", output, live=False, use_llm=False,
                         history_path=Path("data") / "ETH.csv")
            credibility = result["credibility"]
            self.assertEqual(credibility["substantive_count"], 1)
            records = json.loads((output / "evidence.json").read_text(encoding="utf-8"))
            history = next(item for item in records if item["data_type"] == "price_history")
            self.assertEqual(history["source_type"], "local_csv")
            self.assertGreater(history["reliability_score"], HARD_CAPS["fallback_fixture"])


class ValidationTest(unittest.TestCase):
    def _scored(self, **overrides) -> Evidence:
        evidence = make_evidence(**overrides)
        enrich_and_score_evidence([evidence], registry=REGISTRY, now=NOW)
        return evidence

    def test_a_scored_record_passes(self):
        evidence = self._scored(data_type="market", content={"dates": ["2026-02-28"], "prices": [1.0]})
        self.assertEqual(validate_evidence([evidence], [evidence.evidence_id], require_scored=True), [])

    def test_unscored_evidence_is_rejected_only_when_scoring_is_required(self):
        evidence = make_evidence()
        self.assertEqual(validate_evidence([evidence], []), [])
        errors = validate_evidence([evidence], [], require_scored=True)
        self.assertTrue(any("missing score_breakdown" in error for error in errors))

    def test_illegal_source_type_is_rejected(self):
        evidence = self._scored()
        evidence.source_type = "definitely_not_a_category"
        self.assertTrue(any("unknown source_type" in error
                            for error in validate_evidence([evidence], [])))

    def test_illegal_verification_status_is_rejected(self):
        evidence = self._scored()
        evidence.verification_status = "totally_confirmed"
        self.assertTrue(any("unknown verification_status" in error
                            for error in validate_evidence([evidence], [])))

    def test_score_that_disagrees_with_its_breakdown_is_rejected(self):
        evidence = self._scored()
        evidence.reliability_score = 0.99
        self.assertTrue(any("does not match score_breakdown" in error
                            for error in validate_evidence([evidence], [])))

    def test_breaching_a_hard_cap_is_rejected(self):
        evidence = self._scored()
        evidence.score_limiters = ["fallback_fixture"]
        evidence.score_breakdown["score_limiters"] = ["fallback_fixture"]
        evidence.score_breakdown["final_score"] = 0.9
        evidence.reliability_score = 0.9
        errors = validate_evidence([evidence], [])
        self.assertTrue(any("hard cap fallback_fixture" in error for error in errors))

    def test_unknown_limiter_names_are_rejected(self):
        evidence = self._scored()
        evidence.score_breakdown["score_limiters"] = ["invented_cap"]
        self.assertTrue(any("unknown score limiters" in error
                            for error in validate_evidence([evidence], [])))

    def test_fallback_fixture_may_not_score_above_its_cap(self):
        evidence = self._scored()
        evidence.source_type = "fallback_fixture"
        evidence.verification_status = "fallback"
        evidence.reliability_score = 0.8
        evidence.score_breakdown["final_score"] = 0.8
        errors = validate_evidence([evidence], [])
        self.assertTrue(any("fallback evidence must not exceed" in error for error in errors))

    def test_fallback_fixture_must_be_labelled_as_degraded(self):
        evidence = self._scored()
        evidence.source_type = "fallback_fixture"
        evidence.verification_status = "verified"
        evidence.reliability_score = 0.1
        evidence.score_breakdown["final_score"] = 0.1
        errors = validate_evidence([evidence], [])
        self.assertTrue(any("must be labelled unavailable/fallback/rejected" in error for error in errors))

    def test_degraded_status_requires_the_fixture_category(self):
        evidence = self._scored()
        evidence.verification_status = "unavailable"
        errors = validate_evidence([evidence], [])
        self.assertTrue(any("requires source_type" in error for error in errors))

    def test_rejected_evidence_must_score_zero(self):
        evidence = self._scored()
        evidence.verification_status = "rejected"
        self.assertTrue(any("rejected evidence must score 0" in error
                            for error in validate_evidence([evidence], [])))

    def test_scored_evidence_without_lineage_is_rejected(self):
        evidence = self._scored()
        evidence.source_lineage_id = ""
        self.assertTrue(any("source_lineage_id" in error
                            for error in validate_evidence([evidence], [])))

    def test_missing_fetched_at_is_rejected_by_the_validator(self):
        evidence = make_evidence(fetched_at="")
        errors = validate_evidence([evidence], [])
        self.assertTrue(any("invalid fetched_at" in error for error in errors))


class BackwardCompatibilityTest(unittest.TestCase):
    """既有 positional constructor 必須完全不受影響。"""

    def test_nine_positional_arguments_still_construct_evidence(self):
        evidence = Evidence(
            "EV-LEGACY-001", "LegacySource", "https://legacy.example/1", iso(0),
            "market", "ETH", "14d", {"return_pct": 1.0}, 0.8,
        )
        self.assertEqual(evidence.evidence_id, "EV-LEGACY-001")
        self.assertEqual(evidence.reliability_score, 0.8)
        self.assertEqual(evidence.source_type, "unknown")
        self.assertEqual(evidence.verification_status, "unverified")
        self.assertEqual(evidence.score_breakdown, {})
        self.assertEqual(evidence.score_limiters, [])
        self.assertEqual(evidence.related_claim_ids, [])
        self.assertEqual(evidence.independence_factor, 1.0)

    def test_eleven_positional_arguments_still_construct_evidence(self):
        evidence = Evidence(
            "EV-LEGACY-002", "LegacySource", "https://legacy.example/2", iso(0),
            "news", "ETH", "14d", {"headline": "x"}, 0.6,
            {"feed_url": "https://legacy.example/feed"}, "legacy claim",
        )
        self.assertEqual(evidence.content_reference["feed_url"], "https://legacy.example/feed")
        self.assertEqual(evidence.related_claim, "legacy claim")
        self.assertEqual(evidence.source_type, "unknown")

    def test_the_first_eleven_fields_keep_their_order(self):
        names = [field.name for field in fields(Evidence)]
        self.assertEqual(names[:11], [
            "evidence_id", "source", "source_url", "fetched_at", "data_type", "coin",
            "time_range", "content", "reliability_score", "content_reference", "related_claim",
        ])

    def test_new_fields_all_have_defaults(self):
        for field in fields(Evidence)[9:]:
            self.assertTrue(field.default is not MISSING or field.default_factory is not MISSING,
                            field.name)

    def test_a_legacy_record_can_still_be_scored(self):
        evidence = Evidence(
            "EV-LEGACY-003", "LegacySource", "https://legacy.example/3", iso(0),
            "market", "ETH", "14d", {"prices": [1.0], "dates": ["2026-02-28"]}, 0.8,
        )
        enrich_and_score_evidence([evidence], registry=REGISTRY, now=NOW)
        self.assertEqual(evidence.source_type, "market_api")
        self.assertEqual(evidence.score_breakdown["legacy_reliability_hint"], 0.8)
        self.assertEqual(validate_evidence([evidence], [], require_scored=True), [])


if __name__ == "__main__":
    unittest.main()
