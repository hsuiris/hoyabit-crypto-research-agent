"""社群帳號可追溯性與聲量集中度：`anonymous_or_low_trace_social` 的實際判斷依據。

背景（實測缺陷）：這條 hard cap 原本只檢查「貼文 dict 裡有沒有 `author` 這個鍵」，而
`_find_key()` 找到第一個就 return，於是：

    25 則全部匿名        -> final_score 0.3500，cap 生效
    只有 1/25 則有作者   -> final_score 0.3875，cap **完全解除**
    同一帳號刷 25 則     -> final_score 0.3875，與 25 個不同帳號一模一樣

一則署名替 24 則匿名擔保了可追溯性，而同一帳號刷量在分數上完全看不見（`independence`
只被新聞的 source lineage 稀釋，社群沒有等價機制）。

這組測試釘住修好後的行為：

1. cap 依**覆蓋率、集中度、profile locator** 判斷，不是「有沒有任何一則帶 author」。
2. 集中度會回饋到 `independence` 分量，單一帳號刷量真的比多帳號低分（cap 只扣 0.0375，
   光改觸發條件不夠）。
3. 三個平台各讀自己真正提供的欄位：Reddit `author`、Bluesky `author_handle`、HN `author`。
4. 沒有統計欄位的舊資料維持既有行為（退回舊版作者欄位檢查），且會在 notes 說明。

**這不是機器人／假帳號偵測。** 公開 feed 取不到帳號年齡與發文歷史，因此這裡量測的只有
可追溯性與集中度；測試也不得斷言任何「帳號真偽」語意。

外部 API 一律 mock，測試不連網路。
"""

from __future__ import annotations

import json
import unittest
from datetime import datetime, timezone
from unittest.mock import patch

from src.credibility import (
    DEFAULT_SOCIAL_TRACE_THRESHOLDS,
    INDEPENDENCE_FLOOR,
    SOCIAL_ACCOUNT_TRACE_FIELDS,
    SOCIAL_ACCOUNT_TRACE_KEY,
    SOCIAL_TRACE_THRESHOLD_KEY,
    load_source_registry,
    registry_entry,
    score_evidence,
)
from src.day1_mvp import Evidence
from src.day2_sources import (
    fetch_social_bluesky,
    fetch_social_hackernews,
    fetch_social_reddit,
    social_account_trace,
    social_content,
)
from src.schemas import HARD_CAPS

NOW = datetime(2026, 3, 1, 12, 0, 0, tzinfo=timezone.utc)
REGISTRY = load_source_registry()
CAP = HARD_CAPS["anonymous_or_low_trace_social"]

# Reddit 子版 feed：Atom 的 <author><name>/u/帳號</name>。刻意用 25 個不同帳號。
REDDIT_FEED = ("""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
""" + "".join(
    """<entry><title>Ethereum staking update {index}</title>
<link href="https://www.reddit.com/r/ethereum/{index}"/><updated>2026-02-28T00:00:00Z</updated>
<author><name>/u/reddit_user_{index}</name></author></entry>
""".format(index=index) for index in range(25)
) + "</feed>").encode("utf-8")


def post(index: int, *, author: str = None, author_key: str = "author",
         profile: bool = True) -> dict:
    """一則採用貼文。`author=None` 代表來源沒有提供帳號（真的匿名，不是填空字串）。"""
    item = {
        "title": "Ethereum staking discussion {}".format(index),
        "url": "https://social.example.com/p/{}".format(index),
        "matched_positive": [],
        "matched_negative": [],
    }
    if author:
        item[author_key] = author
        if profile:
            item["author_profile_url"] = "https://social.example.com/user/{}".format(author)
    return item


def social_evidence(posts, *, platform: str = "reddit", content=None) -> Evidence:
    """一筆 social_public 證據。`content` 可直接給（用於模擬沒有統計欄位的舊資料）。"""
    return Evidence(
        "EV-SOCIAL-ETH-001", "Social feed", "https://social.example.com/feed",
        NOW.isoformat(), "social", "ETH", "recent",
        content if content is not None else social_content(
            posts, 0, 0, platform=platform, query="r/ethereum new", filtered_out=0),
        0.45,
        {"endpoint": "https://social.example.com/feed"},
        "Public discussion tone",
        source_type="social_public",
    )


def scored(posts, *, platform: str = "reddit", content=None) -> dict:
    return score_evidence(social_evidence(posts, platform=platform, content=content),
                          REGISTRY, now=NOW)


ANONYMOUS_25 = [post(index) for index in range(25)]
ONE_AUTHOR_OF_25 = [post(0, author="analyst")] + [post(index) for index in range(1, 25)]
SAME_AUTHOR_25 = [post(index, author="spammer") for index in range(25)]
DISTINCT_25 = [post(index, author="user{:02d}".format(index)) for index in range(25)]


class AccountTraceStatisticsTest(unittest.TestCase):
    """統計本身：分母、覆蓋率、集中度、profile locator。"""

    def test_field_contract_matches_the_credibility_engine(self):
        """兩邊各寫一份字面值就會漂移，鍵名契約由這個測試綁住。"""
        trace = social_account_trace(DISTINCT_25)
        self.assertEqual(set(trace) - {"trace_rule"}, set(SOCIAL_ACCOUNT_TRACE_FIELDS))
        self.assertIn(SOCIAL_ACCOUNT_TRACE_KEY, social_content(
            DISTINCT_25, 0, 0, platform="reddit", query="q", filtered_out=0))

    def test_coverage_is_a_ratio_not_a_boolean(self):
        """修復前的漏洞就在這裡：一則有作者不等於整筆可追溯。"""
        trace = social_account_trace(ONE_AUTHOR_OF_25)
        self.assertEqual(trace["posts_considered"], 25)
        self.assertEqual(trace["attributed_post_count"], 1)
        self.assertEqual(trace["author_coverage"], 0.04)

    def test_concentration_separates_one_account_from_twenty_five(self):
        concentrated = social_account_trace(SAME_AUTHOR_25)
        diverse = social_account_trace(DISTINCT_25)

        self.assertEqual(concentrated["distinct_author_count"], 1)
        self.assertEqual(concentrated["top_author_share"], 1.0)
        self.assertEqual(diverse["distinct_author_count"], 25)
        self.assertEqual(diverse["top_author_share"], 0.04)

    def test_anonymous_posts_yield_no_author_and_no_locator(self):
        trace = social_account_trace(ANONYMOUS_25)
        self.assertEqual(trace["author_coverage"], 0.0)
        self.assertEqual(trace["distinct_author_count"], 0)
        self.assertEqual(trace["top_author_share"], 0.0)
        self.assertEqual(trace["profile_locator_count"], 0)
        self.assertFalse(trace["has_profile_locator"])

    def test_the_denominator_is_the_adopted_posts(self):
        """分母與 post_count／irrelevant_filtered_out 同語意：被過濾掉的貼文不算進來。"""
        content = social_content(DISTINCT_25[:4], 0, 0, platform="reddit", query="q",
                                 filtered_out=21)
        self.assertEqual(content["post_count"], 4)
        self.assertEqual(content["irrelevant_filtered_out"], 21)
        self.assertEqual(content[SOCIAL_ACCOUNT_TRACE_KEY]["posts_considered"], 4)

    def test_the_same_account_is_counted_once_across_naming_variants(self):
        """`/u/Alice`、`alice`、`@Alice` 是同一個帳號，不能算成三個聲音。"""
        posts = [post(0, author="/u/Alice"), post(1, author="alice"), post(2, author="@Alice")]
        trace = social_account_trace(posts)
        self.assertEqual(trace["distinct_author_count"], 1)
        self.assertEqual(trace["top_author_share"], 1.0)

    def test_empty_post_list_reports_zeroes_rather_than_failing(self):
        trace = social_account_trace([])
        self.assertEqual(trace["posts_considered"], 0)
        self.assertEqual(trace["author_coverage"], 0.0)
        self.assertEqual(trace["top_author_share"], 0.0)

    def test_trace_rule_says_it_is_not_bot_detection(self):
        """誠實優先：這句話會跟著證據一起輸出，不得被改成宣稱能識別帳號真偽。"""
        rule = social_account_trace(DISTINCT_25)["trace_rule"]
        self.assertIn("不是機器人", rule)
        self.assertIn("集中度", rule)

    def test_statistics_are_deterministic(self):
        self.assertEqual(json.dumps(social_account_trace(DISTINCT_25), sort_keys=True,
                                    ensure_ascii=False),
                         json.dumps(social_account_trace(DISTINCT_25), sort_keys=True,
                                    ensure_ascii=False))


class HardCapTriggerTest(unittest.TestCase):
    """cap 的新觸發條件。四個情境對應修復前實測的分數表。"""

    def test_all_anonymous_is_capped(self):
        record = scored(ANONYMOUS_25)
        self.assertIn("anonymous_or_low_trace_social", record["score_limiters"])
        self.assertLessEqual(record["final_score"], CAP)

    def test_one_signed_post_out_of_twenty_five_is_still_capped(self):
        """修復前這個情境拿到 0.3875、完全沒有 cap。"""
        record = scored(ONE_AUTHOR_OF_25)
        self.assertIn("anonymous_or_low_trace_social", record["score_limiters"])
        self.assertLessEqual(record["final_score"], CAP)
        self.assertTrue(any("覆蓋率" in note for note in record["notes"]),
                        "報告必須能解釋為什麼被降分")

    def test_one_account_posting_twenty_five_times_is_capped_and_scores_lower(self):
        """cap 只扣 0.0375，所以集中度必須同時回饋到 independence，否則分數看不出差別。"""
        concentrated = scored(SAME_AUTHOR_25)
        diverse = scored(DISTINCT_25)

        self.assertIn("anonymous_or_low_trace_social", concentrated["score_limiters"])
        self.assertNotIn("anonymous_or_low_trace_social", diverse["score_limiters"])
        self.assertEqual(concentrated["components"]["independence"], INDEPENDENCE_FLOOR)
        self.assertGreater(diverse["components"]["independence"],
                           concentrated["components"]["independence"])
        self.assertGreaterEqual(diverse["final_score"] - concentrated["final_score"], 0.03)
        self.assertEqual(concentrated["final_score"], 0.3475)
        self.assertEqual(diverse["final_score"], 0.3875)

    def test_twenty_five_distinct_authors_with_profiles_are_not_capped(self):
        record = scored(DISTINCT_25)
        self.assertNotIn("anonymous_or_low_trace_social", record["score_limiters"])
        self.assertEqual(record["independence_factor"], 1.0)
        self.assertEqual(record["components"]["independence"], 0.45)

    def test_missing_profile_locator_alone_triggers_the_cap(self):
        """帳號名稱可讀但無從連回帳號頁：讀者無法自己回去看這個帳號說過什麼。"""
        posts = [post(index, author="user{:02d}".format(index), profile=False)
                 for index in range(25)]
        record = scored(posts)
        self.assertIn("anonymous_or_low_trace_social", record["score_limiters"])
        self.assertTrue(any("profile locator" in note for note in record["notes"]))

    def test_a_tiny_sample_from_two_accounts_is_capped(self):
        """兩個帳號各發一則不足以代表社群聲量，門檻 min_distinct_author_count 擋下。"""
        record = scored([post(0, author="alice"), post(1, author="bob")])
        self.assertIn("anonymous_or_low_trace_social", record["score_limiters"])
        self.assertTrue(any("可辨識帳號僅" in note for note in record["notes"]))

    def test_explicit_anonymous_flag_still_triggers_the_cap(self):
        content = social_content(DISTINCT_25, 0, 0, platform="reddit", query="q", filtered_out=0)
        content["anonymous"] = True
        record = scored(DISTINCT_25, content=content)
        self.assertIn("anonymous_or_low_trace_social", record["score_limiters"])

    def test_non_social_evidence_is_untouched_by_the_account_rules(self):
        """市場資料沒有帳號概念，不得因為缺帳號統計就被 social cap 波及。"""
        market = Evidence(
            "EV-MKT-001", "Market API", "https://api.example.com/ohlcv", NOW.isoformat(),
            "market", "ETH", "14d", {"symbol": "ETHUSDT", "interval": "1d", "prices": [1, 2]},
            0.8, {"endpoint": "https://api.example.com/ohlcv"}, "price trend",
            source_type="market_api",
        )
        record = score_evidence(market, REGISTRY, now=NOW)
        self.assertEqual(record["score_limiters"], [])
        self.assertEqual(record["components"]["independence"], 0.85)


class IndependenceDilutionTest(unittest.TestCase):
    """集中度稀釋：比照 news lineage 的 `independence_base / size`，受 INDEPENDENCE_FLOOR 保護。"""

    def test_dilution_uses_countable_independent_voices(self):
        # 25 則 / 5 個帳號 = 每個帳號 5 則 -> 0.45 / 5 = 0.09
        posts = [post(index, author="user{}".format(index % 5)) for index in range(25)]
        record = scored(posts)
        self.assertEqual(record["components"]["independence"], 0.09)
        self.assertEqual(record["independence_factor"], 0.2)

    def test_anonymous_posts_collapse_into_a_single_unidentifiable_voice(self):
        """匿名貼文各算一個獨立聲音等於獎勵匿名，所以全部併成一個。"""
        record = scored(ANONYMOUS_25)
        self.assertEqual(record["components"]["independence"], INDEPENDENCE_FLOOR)
        self.assertTrue(any("無法辨識的來源" in note for note in record["notes"]))

    def test_dilution_never_falls_below_the_floor(self):
        record = scored(SAME_AUTHOR_25)
        self.assertGreaterEqual(record["components"]["independence"], INDEPENDENCE_FLOOR)

    def test_diverse_authors_are_not_diluted(self):
        record = scored(DISTINCT_25)
        self.assertEqual(record["independence_factor"], 1.0)
        self.assertFalse(any("稀釋" in note for note in record["notes"]))


class PlatformFieldTest(unittest.TestCase):
    """三個平台各讀自己真正提供的欄位，不互相冒充、不用 feed 名稱或 fetched_at 代填。"""

    @patch("src.day2_sources._get_bytes", return_value=REDDIT_FEED)
    def test_reddit_author_element_is_read(self, _get_bytes):
        content = fetch_social_reddit("ETH").content
        trace = content[SOCIAL_ACCOUNT_TRACE_KEY]

        self.assertEqual(content["post_count"], 25)
        self.assertEqual(trace["author_coverage"], 1.0)
        self.assertEqual(trace["distinct_author_count"], 25)
        self.assertEqual(trace["profile_locator_count"], 25)
        self.assertEqual(content["posts"][0]["author"], "/u/reddit_user_0")
        self.assertEqual(content["posts"][0]["author_profile_url"],
                         "https://www.reddit.com/user/reddit_user_0")

    @patch("src.day2_sources._get_json")
    def test_bluesky_author_handle_is_read(self, mock_get):
        mock_get.return_value = {"posts": [{
            "record": {"text": "Ethereum adoption update {}".format(index)},
            "uri": "at://did:plc:test/app.bsky.feed.post/{}".format(index),
            "author": {"handle": "user{}.bsky.social".format(index)},
            "indexedAt": "2026-02-28T00:00:00Z",
            "likeCount": 1, "replyCount": 0, "repostCount": 0,
        } for index in range(25)]}

        content = fetch_social_bluesky("ETH").content
        trace = content[SOCIAL_ACCOUNT_TRACE_KEY]

        self.assertEqual(trace["author_coverage"], 1.0)
        self.assertEqual(trace["distinct_author_count"], 25)
        self.assertEqual(content["posts"][0]["author_handle"], "user0.bsky.social")
        self.assertEqual(content["posts"][0]["author_profile_url"],
                         "https://bsky.app/profile/user0.bsky.social")

    @patch("src.day2_sources._get_json")
    def test_hackernews_author_is_read(self, mock_get):
        mock_get.return_value = {"hits": [{
            "objectID": str(index), "title": "Ethereum client release {}".format(index),
            "created_at": "2026-02-28T00:00:00Z", "points": 5, "num_comments": 1,
            "author": "hn_user_{}".format(index),
        } for index in range(25)]}

        content = fetch_social_hackernews("ETH").content
        trace = content[SOCIAL_ACCOUNT_TRACE_KEY]

        self.assertEqual(trace["author_coverage"], 1.0)
        self.assertEqual(trace["distinct_author_count"], 25)
        self.assertEqual(content["posts"][0]["author"], "hn_user_0")
        self.assertEqual(content["posts"][0]["author_profile_url"],
                         "https://news.ycombinator.com/user?id=hn_user_0")

    @patch("src.day2_sources._get_json")
    def test_a_platform_without_accounts_is_reported_as_unattributed(self, mock_get):
        """API 沒回帳號時就是無作者：不得拿 feed 名稱、查詢字串或 fetched_at 冒充。"""
        mock_get.return_value = {"hits": [{
            "objectID": str(index), "title": "Ethereum client release {}".format(index),
            "created_at": "2026-02-28T00:00:00Z", "points": 5, "num_comments": 1,
        } for index in range(25)]}

        content = fetch_social_hackernews("ETH").content
        trace = content[SOCIAL_ACCOUNT_TRACE_KEY]

        self.assertEqual(trace["author_coverage"], 0.0)
        self.assertEqual(trace["profile_locator_count"], 0)
        self.assertIsNone(content["posts"][0]["author"])
        self.assertIsNone(content["posts"][0]["author_profile_url"])

    @patch("src.day2_sources._get_bytes", return_value=REDDIT_FEED)
    def test_a_real_reddit_feed_is_no_longer_capped(self, _get_bytes):
        """每則都署名、都能連回帳號的 feed 比匿名貼文更可追溯，分數應反映這件事。"""
        evidence = fetch_social_reddit("ETH")
        evidence.source_type = "social_public"
        record = score_evidence(evidence, REGISTRY, now=NOW)
        self.assertNotIn("anonymous_or_low_trace_social", record["score_limiters"])


class LegacyDataTest(unittest.TestCase):
    """沒有統計欄位的舊資料（手寫測試資料、離線 fixture）維持既有行為。"""

    def _legacy_content(self, posts) -> dict:
        """T3 時代的 social content：沒有 account_trace。"""
        return {"platform": "reddit", "sentiment": "mixed", "positive_terms": 0,
                "negative_terms": 0, "post_count": len(posts), "posts": posts}

    def test_legacy_anonymous_content_is_still_capped(self):
        content = self._legacy_content([{"title": "ETH 討論",
                                         "url": "https://social.example.com/p/1"}])
        record = scored([], content=content)
        self.assertIn("anonymous_or_low_trace_social", record["score_limiters"])
        self.assertLessEqual(record["final_score"], CAP)

    def test_legacy_signed_content_keeps_its_pre_fix_result(self):
        """舊資料的判斷不變（作者欄位存在即不 cap），但必須明講用的是退回路徑。"""
        content = self._legacy_content([{"title": "ETH 討論",
                                         "url": "https://social.example.com/p/1",
                                         "author": "analyst"}])
        record = scored([], content=content)
        self.assertNotIn("anonymous_or_low_trace_social", record["score_limiters"])
        self.assertTrue(any("未附帳號可追溯性統計" in note for note in record["notes"]),
                        "讀不到統計時不得無聲放行")
        self.assertEqual(record["components"]["independence"], 0.45)

    def test_malformed_statistics_fall_back_instead_of_crashing(self):
        content = self._legacy_content([{"title": "ETH 討論", "author": "analyst"}])
        content[SOCIAL_ACCOUNT_TRACE_KEY] = {"unexpected": "shape"}
        record = scored([], content=content)
        self.assertTrue(any("未附帳號可追溯性統計" in note for note in record["notes"]))
        self.assertGreater(record["final_score"], 0.0)

    def test_offline_fixture_social_evidence_still_scores_as_a_fixture(self):
        """離線 fallback 不得因為新邏輯而改變：fixture 的上限仍是 0.20。"""
        fixture = Evidence(
            "EV-SOCIAL-BSKY-ETH-FALLBACK", "Offline Bluesky fixture",
            "https://example.com/social_bluesky", NOW.isoformat(), "social_bluesky", "ETH",
            "recent", {"platform": "bluesky", "sentiment": "unknown", "post_count": 0,
                       "posts": []},
            0.20, {"endpoint": "https://example.com/social_bluesky"}, "degraded",
            source_type="fallback_fixture", verification_status="unavailable",
        )
        record = score_evidence(fixture, REGISTRY, now=NOW)
        self.assertIn("fallback_fixture", record["score_limiters"])
        self.assertLessEqual(record["final_score"], HARD_CAPS["fallback_fixture"])


class RegistryThresholdTest(unittest.TestCase):
    """門檻寫在 config，不是硬編在程式裡。"""

    def test_thresholds_come_from_the_registry_entry(self):
        entry = registry_entry(REGISTRY, "social_public")
        thresholds = entry[SOCIAL_TRACE_THRESHOLD_KEY]
        self.assertEqual(set(thresholds), set(DEFAULT_SOCIAL_TRACE_THRESHOLDS))
        self.assertEqual(thresholds["min_author_coverage"], 0.8)
        self.assertEqual(thresholds["max_top_author_share"], 0.5)
        self.assertTrue(thresholds["require_profile_locator"])

    def test_a_relaxed_registry_threshold_changes_the_verdict(self):
        """證明分數真的讀 config：把集中度門檻放寬到 1.0，同一帳號刷量就不再觸發 cap。"""
        relaxed = {
            "default": dict(REGISTRY["default"]),
            "source_types": dict(REGISTRY["source_types"]),
            "registry_version": REGISTRY["registry_version"],
        }
        entry = dict(relaxed["source_types"]["social_public"])
        entry[SOCIAL_TRACE_THRESHOLD_KEY] = {"min_author_coverage": 0.5,
                                             "max_top_author_share": 1.0,
                                             "min_distinct_author_count": 1,
                                             "require_profile_locator": False}
        relaxed["source_types"]["social_public"] = entry

        record = score_evidence(social_evidence(SAME_AUTHOR_25), relaxed, now=NOW)
        self.assertNotIn("anonymous_or_low_trace_social", record["score_limiters"])
        # 門檻只管 cap；集中度稀釋是獨立的事實陳述，仍然生效。
        self.assertEqual(record["components"]["independence"], INDEPENDENCE_FLOOR)

    def test_broken_thresholds_fall_back_to_the_conservative_defaults(self):
        broken = {
            "default": dict(REGISTRY["default"]),
            "source_types": dict(REGISTRY["source_types"]),
            "registry_version": REGISTRY["registry_version"],
        }
        entry = dict(broken["source_types"]["social_public"])
        entry[SOCIAL_TRACE_THRESHOLD_KEY] = {"min_author_coverage": "很高"}
        broken["source_types"]["social_public"] = entry

        record = score_evidence(social_evidence(ONE_AUTHOR_OF_25), broken, now=NOW)
        self.assertIn("anonymous_or_low_trace_social", record["score_limiters"])

    def test_registry_limitations_do_not_claim_bot_detection(self):
        limitations = registry_entry(REGISTRY, "social_public")["known_limitations"]
        text = " ".join(limitations)
        self.assertIn("不判斷帳號真偽", text)
        self.assertIn("集中度", text)


class DeterminismTest(unittest.TestCase):
    def test_scoring_the_same_input_twice_is_identical(self):
        first = scored(ONE_AUTHOR_OF_25)
        second = scored(ONE_AUTHOR_OF_25)
        self.assertEqual(json.dumps(first, sort_keys=True, ensure_ascii=False),
                         json.dumps(second, sort_keys=True, ensure_ascii=False))

    def test_the_notes_are_json_serialisable_and_reference_no_model_output(self):
        record = scored(SAME_AUTHOR_25)
        json.dumps(record, ensure_ascii=False)  # must not raise
        self.assertTrue(record["notes"])
        for note in record["notes"]:
            self.assertIsInstance(note, str)

    def test_scoring_does_not_mutate_the_evidence(self):
        evidence = social_evidence(SAME_AUTHOR_25)
        before = json.dumps(evidence.content, sort_keys=True, ensure_ascii=False)
        score_evidence(evidence, REGISTRY, now=NOW)
        self.assertEqual(json.dumps(evidence.content, sort_keys=True, ensure_ascii=False), before)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
