"""社群面來源測試。

背景（實測缺陷）：社群面原本是「第一個成功就停」的備援鏈，而 Reddit 與 Bluesky 都回 403，
所以每次都由 Hacker News 回答。而 HN 的查詢字串是 `"{ticker} crypto"`，實測抓回來的是：

    Getting 25 Gbps Thunderbolt Ethernet on My Mac Studio      <- ETH 匹配 Ethernet
    Twenty-five years ago it was cryptography, ...             <- crypto 匹配 cryptography

這些內容曾實際出現在 `demo-fixtures` 的證據清單裡，被標為「BTC 社群情緒」。命題明說主辦方會
抽查「引用內容真實性」，那種證據無法解釋。

這組測試釘住三件事：

1. 相關性過濾：貼文必須以**完整詞**提及幣種，`ETH` 不得匹配 `Ethernet`。
2. 三個平台是三個獨立 collector，不是備援鏈 —— 一個平台失敗不得讓整個社群面消失。
3. 全部平台都沒有相關貼文時要拋錯（讓 collector 誠實降級），不得回報空樣本的情緒值。

外部 API 一律 mock，測試不依賴網路。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.error import HTTPError

from src.claim_graph import DOMAIN_BY_DATA_TYPE
from src.credibility import SOCIAL_ACCOUNT_TRACE_KEY
from src.day2_sources import (
    COIN_SEARCH_NAMES,
    COIN_SUBREDDITS,
    DATA_TYPE_SOURCE_TYPES,
    _FALLBACK_SPECS,
    _SOURCE_LOADERS,
    COLLECTION_AGENTS,
    coin_search_terms,
    fetch_social,
    fetch_social_bluesky,
    fetch_social_hackernews,
    fetch_social_reddit,
    mentions_coin,
    score_social_text,
)

SOCIAL_LABELS = ("social", "social_bluesky", "social_hackernews")

REDDIT_FEED = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>Ethereum staking yield update</title>
<link href="https://www.reddit.com/r/ethereum/1"/><updated>2026-07-25T00:00:00Z</updated></entry>
<entry><title>Daily General Discussion</title>
<link href="https://www.reddit.com/r/ethereum/2"/><updated>2026-07-25T01:00:00Z</updated></entry>
</feed>"""


def _bluesky(texts):
    return {"posts": [{
        "record": {"text": text},
        "uri": f"at://did:plc:test/app.bsky.feed.post/{index}",
        "author": {"handle": "analyst.example"},
        "indexedAt": "2026-07-25T00:00:00Z",
        "likeCount": 1, "replyCount": 0, "repostCount": 0,
    } for index, text in enumerate(texts)]}


def _hackernews(titles):
    return {"hits": [{"objectID": str(index), "title": title,
                      "created_at": "2026-07-25T00:00:00Z", "points": 5, "num_comments": 1}
                     for index, title in enumerate(titles)]}


class RelevanceFilterTests(unittest.TestCase):
    """整個修法的核心：完整詞比對。"""

    def test_ticker_does_not_match_a_longer_word(self):
        self.assertFalse(mentions_coin("Getting 25 Gbps Thunderbolt Ethernet on My Mac", "ETH"))
        self.assertFalse(mentions_coin("A story about ethernet cables", "ETH"))

    def test_crypto_substring_does_not_qualify(self):
        self.assertFalse(
            mentions_coin("Twenty-five years ago it was cryptography, today model weights", "ETH"))

    def test_full_name_and_ticker_both_qualify(self):
        self.assertTrue(mentions_coin("Ethereum Foundation announces a roadmap", "ETH"))
        self.assertTrue(mentions_coin("ETH's staking yield is now 3.2%", "ETH"))
        self.assertTrue(mentions_coin("Ether price breaks resistance", "ETH"))

    def test_every_supported_coin_has_search_terms_and_a_subreddit(self):
        for coin in ("BTC", "ETH", "SOL", "BNB", "XRP"):
            self.assertIn(coin, COIN_SEARCH_NAMES, coin)
            self.assertIn(coin, COIN_SUBREDDITS, coin)
            self.assertTrue(mentions_coin(COIN_SEARCH_NAMES[coin][0], coin), coin)

    def test_search_uses_the_full_name_not_the_ticker(self):
        """查詢字串用全名，這是 HN 抓到 Ethernet 的直接原因。"""
        self.assertEqual(coin_search_terms("ETH")[0], "Ethereum")
        self.assertEqual(coin_search_terms("BTC")[0], "Bitcoin")

    def test_sentiment_lexicon_is_case_and_punctuation_insensitive(self):
        positive, negative = score_social_text("Bullish! adoption... and a CRASH")
        self.assertIn("bullish", positive)
        self.assertIn("adoption", positive)
        self.assertIn("crash", negative)


class IndependentCollectorTests(unittest.TestCase):
    """三個平台是三個 collector，不是備援鏈。"""

    def test_all_three_platforms_are_registered_as_collectors(self):
        labels = [label for label, _ in _SOURCE_LOADERS]
        for label in SOCIAL_LABELS:
            self.assertIn(label, labels, label)

    def test_all_three_share_the_social_domain(self):
        """三個平台有貼文不代表跨領域確認，只代表社群面有三條獨立證據。"""
        for label in SOCIAL_LABELS:
            self.assertEqual(DOMAIN_BY_DATA_TYPE[label], "social", label)
            self.assertEqual(DATA_TYPE_SOURCE_TYPES[label], "social_public", label)

    def test_the_social_agent_owns_all_three(self):
        agent = next(a for a in COLLECTION_AGENTS if a[0] == "social_agent")
        self.assertEqual(tuple(agent[2]), SOCIAL_LABELS)

    def test_the_two_new_labels_have_explicit_fallback_fixtures(self):
        """沒有 fallback 規格時失敗會落到 Day-1 mock，而那兩個平台沒有 mock。"""
        for label in ("social_bluesky", "social_hackernews"):
            self.assertIn(label, _FALLBACK_SPECS, label)


class AccountTraceWiringTests(unittest.TestCase):
    """帳號可追溯性統計的分母必須是**過濾後實際採用的貼文**。

    統計本身與 credibility 的判斷在 `tests/test_social_account_trace.py`；這裡只釘住
    collector 的接線：三個平台都要帶統計，且分母與 `post_count` 一致。
    """

    @patch("src.day2_sources._get_bytes", return_value=REDDIT_FEED)
    def test_reddit_statistics_count_only_the_adopted_posts(self, mock_get):
        content = fetch_social_reddit("ETH").content
        trace = content[SOCIAL_ACCOUNT_TRACE_KEY]
        self.assertEqual(content["post_count"], 1)
        self.assertEqual(content["irrelevant_filtered_out"], 1)
        self.assertEqual(trace["posts_considered"], 1)

    @patch("src.day2_sources._get_json")
    def test_bluesky_statistics_count_only_the_adopted_posts(self, mock_get):
        mock_get.return_value = _bluesky([
            "Ethereum adoption is bullish",
            "Thunderbolt Ethernet on my Mac Studio",   # 被過濾，不進統計分母
            "Ether price breaks out",
        ])
        content = fetch_social_bluesky("ETH").content
        trace = content[SOCIAL_ACCOUNT_TRACE_KEY]
        self.assertEqual(content["post_count"], 2)
        self.assertEqual(trace["posts_considered"], 2)
        # 同一個 handle 發兩則 = 一個聲音，不是兩個。
        self.assertEqual(trace["distinct_author_count"], 1)
        self.assertEqual(trace["top_author_share"], 1.0)

    @patch("src.day2_sources._get_json")
    def test_hackernews_statistics_count_only_the_adopted_posts(self, mock_get):
        mock_get.return_value = _hackernews([
            "Getting 25 Gbps Thunderbolt Ethernet on My Mac Studio",
            "Sharden is live. An autonomous protocol on Ethereum",
        ])
        content = fetch_social_hackernews("ETH").content
        trace = content[SOCIAL_ACCOUNT_TRACE_KEY]
        self.assertEqual(content["post_count"], 1)
        self.assertEqual(trace["posts_considered"], 1)


class RedditFeedTests(unittest.TestCase):
    @patch("src.day2_sources._get_bytes", return_value=REDDIT_FEED)
    def test_subreddit_feed_is_used_and_off_topic_posts_are_filtered(self, mock_get):
        evidence = fetch_social_reddit("ETH")
        content = evidence.content

        self.assertIn("/r/ethereum/", evidence.source_url)
        self.assertEqual(content["post_count"], 1)          # 只有提到 Ethereum 的那則
        self.assertEqual(content["irrelevant_filtered_out"], 1)  # Daily General Discussion
        self.assertEqual(content["platform"], "reddit")

    @patch("src.day2_sources.time.sleep")
    @patch("src.day2_sources._get_bytes")
    def test_a_rate_limited_first_attempt_is_retried_once(self, mock_get, mock_sleep):
        """Reddit 的 x-ratelimit-remaining 恆為 0，同一個 feed 會隨機回 200 或 429。"""
        mock_get.side_effect = [HTTPError("u", 429, "Too Many Requests", {}, None), REDDIT_FEED]

        evidence = fetch_social_reddit("ETH")

        self.assertEqual(mock_get.call_count, 2)
        self.assertTrue(mock_sleep.called, "重試前必須等待，否則只是立刻再撞一次限流")
        self.assertEqual(evidence.content["post_count"], 1)

    @patch("src.day2_sources.time.sleep")
    @patch("src.day2_sources._get_bytes",
           side_effect=HTTPError("u", 429, "Too Many Requests", {}, None))
    def test_reddit_gives_up_after_the_retry(self, mock_get, mock_sleep):
        """重試一次就放棄：採集有時間預算，Bluesky 與 HN 會接手社群面。"""
        with self.assertRaises(HTTPError):
            fetch_social_reddit("ETH")
        self.assertEqual(mock_get.call_count, 2)

    def test_an_unconfigured_coin_is_rejected(self):
        with self.assertRaises(NotImplementedError):
            fetch_social_reddit("DOGE")


class BlueskyTests(unittest.TestCase):
    @patch("src.day2_sources._get_json")
    def test_the_working_host_is_used(self, mock_get):
        """public.api.bsky.app 回 403；社群面靜默崩塌就是從這裡開始的。"""
        mock_get.return_value = _bluesky(["Ethereum adoption is bullish"])

        evidence = fetch_social_bluesky("ETH")

        self.assertIn("api.bsky.app", evidence.source_url)
        self.assertNotIn("public.api.bsky.app", evidence.source_url)
        self.assertEqual(evidence.data_type, "social_bluesky")

    @patch("src.day2_sources._get_json")
    def test_off_topic_posts_are_filtered_and_counted(self, mock_get):
        mock_get.return_value = _bluesky([
            "Ethereum adoption is bullish",
            "Thunderbolt Ethernet on my Mac Studio",   # 必須被濾掉
            "Ether price breaks out",
        ])

        content = fetch_social_bluesky("ETH").content

        self.assertEqual(content["post_count"], 2)
        self.assertEqual(content["irrelevant_filtered_out"], 1)
        self.assertEqual(content["sentiment"], "positive")

    @patch("src.day2_sources._get_json")
    def test_all_posts_irrelevant_raises_instead_of_reporting_empty_sentiment(self, mock_get):
        mock_get.return_value = _bluesky(["Thunderbolt Ethernet", "cryptography history"])

        with self.assertRaises(ValueError):
            fetch_social_bluesky("ETH")


class HackerNewsTests(unittest.TestCase):
    @patch("src.day2_sources._get_json")
    def test_stories_only_and_full_name_query(self, mock_get):
        """`tags=(story,comment)` 會抓到 title 為 null 的留言，於是改用母文標題。"""
        mock_get.return_value = _hackernews(["Quantum-secure wallet for Ethereum"])

        evidence = fetch_social_hackernews("ETH")

        self.assertIn("tags=story", evidence.source_url)
        self.assertNotIn("comment", evidence.source_url)
        self.assertIn("Ethereum", evidence.source_url)
        self.assertEqual(evidence.data_type, "social_hackernews")

    @patch("src.day2_sources._get_json")
    def test_the_original_false_positives_are_now_filtered(self, mock_get):
        """這兩則是實測抓到、且曾進入 fixture 的內容。"""
        mock_get.return_value = _hackernews([
            "Getting 25 Gbps Thunderbolt Ethernet on My Mac Studio",
            "Twenty-five years ago it was cryptography, today it's model weights",
            "Sharden is live. An autonomous protocol on Ethereum",
        ])

        content = fetch_social_hackernews("ETH").content

        self.assertEqual(content["post_count"], 1)
        self.assertEqual(content["irrelevant_filtered_out"], 2)
        self.assertNotIn("Ethernet", str(content["posts"]))


class FirstWorkingPlatformTests(unittest.TestCase):
    """`fetch_social()` 已不在 collector 清單上，但仍要能用。"""

    @patch("src.day2_sources.time.sleep")
    @patch("src.day2_sources._get_bytes", side_effect=HTTPError("u", 403, "Blocked", {}, None))
    @patch("src.day2_sources._get_json")
    def test_it_skips_a_blocked_platform(self, mock_json, mock_bytes, mock_sleep):
        mock_json.return_value = _bluesky(["Ethereum adoption is bullish"])

        evidence = fetch_social("ETH")

        self.assertEqual(evidence.data_type, "social_bluesky")

    @patch("src.day2_sources.time.sleep")
    @patch("src.day2_sources._get_bytes", side_effect=HTTPError("u", 403, "Blocked", {}, None))
    @patch("src.day2_sources._get_json", side_effect=HTTPError("u", 403, "Blocked", {}, None))
    def test_every_platform_failing_raises(self, mock_json, mock_bytes, mock_sleep):
        with self.assertRaises(ValueError):
            fetch_social("ETH")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
