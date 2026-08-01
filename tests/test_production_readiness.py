import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import lambda_handler
from src.day2_sources import (KNOWN_WHALE_ADDRESSES, WHALE_EXPLORER_URL, fetch_funding_rate,
                              fetch_long_short_ratio, fetch_onchain, fetch_social_bluesky,
                              fetch_social_hackernews, fetch_social_reddit, fetch_vegas_signal,
                              fetch_whale_wallets)
from src.orchestrator import run


class ProductionReadinessTest(unittest.TestCase):
    @patch("src.day2_sources._get_bytes")
    def test_real_social_adapter_schema(self, mock_get):
        """Reddit 改走子版 `.rss`：搜尋 API 對本專案一律 403，feed 仍可取得。"""
        mock_get.return_value = b"""<?xml version="1.0"?>
<feed xmlns="http://www.w3.org/2005/Atom">
<entry><title>ETH adoption upgrade looks bullish</title>
<link href="https://www.reddit.com/r/ethereum/1"/><updated>2026-07-25T00:00:00Z</updated></entry>
<entry><title>Ethereum risks remain mixed</title>
<link href="https://www.reddit.com/r/ethereum/2"/><updated>2026-07-24T00:00:00Z</updated></entry>
</feed>"""
        evidence = fetch_social_reddit("ETH")

        self.assertEqual(evidence.data_type, "social")
        self.assertEqual(evidence.content["platform"], "reddit")
        self.assertEqual(evidence.content["sentiment"], "positive")
        self.assertEqual(evidence.content_reference["post_count"], 2)
        self.assertEqual(evidence.content_reference["subreddit"], "ethereum")
        self.assertTrue(evidence.related_claim)

    @patch("src.day2_sources._get_json")
    def test_bluesky_social_adapter_schema(self, mock_get):
        mock_get.return_value = {"posts": [{
            "record": {"text": "ETH adoption and bullish momentum"},
            "uri": "at://did:plc:test/app.bsky.feed.post/abc",
            "author": {"handle": "analyst.example"},
            "indexedAt": "2026-07-25T00:00:00Z",
            "likeCount": 8,
            "replyCount": 2,
            "repostCount": 1,
        }]}
        evidence = fetch_social_bluesky("ETH")
        self.assertEqual(evidence.source, "Bluesky public search")
        self.assertEqual(evidence.content["sentiment"], "positive")
        self.assertEqual(evidence.content_reference["post_count"], 1)

    @patch("src.day2_sources._get_json")
    def test_hackernews_social_adapter_schema(self, mock_get):
        mock_get.return_value = {"hits": [{"objectID": "1", "title": "ETH adoption upgrade", "created_at": "2026-07-25T00:00:00Z", "points": 10, "num_comments": 4}]}
        evidence = fetch_social_hackernews("ETH")
        self.assertEqual(evidence.source, "Hacker News Algolia search")
        self.assertEqual(evidence.content_reference["post_count"], 1)

    @patch("src.day2_sources._get_json")
    def test_funding_rate_adapter_flags_long_crowding(self, mock_get):
        mock_get.return_value = {"symbol": "ETHUSDT", "markPrice": "3200.5", "lastFundingRate": "0.0005", "nextFundingTime": 1234567890000}
        evidence = fetch_funding_rate("ETH")
        self.assertEqual(evidence.data_type, "derivatives")
        self.assertEqual(evidence.content["bias"], "long_crowded")
        self.assertAlmostEqual(evidence.content["funding_rate_pct"], 0.05)

    @patch("src.day2_sources._get_json")
    def test_funding_rate_adapter_flags_short_crowding(self, mock_get):
        mock_get.return_value = {"symbol": "ETHUSDT", "markPrice": "3200.5", "lastFundingRate": "-0.0007", "nextFundingTime": 1234567890000}
        evidence = fetch_funding_rate("ETH")
        self.assertEqual(evidence.content["bias"], "short_crowded")

    @patch("src.day2_sources._get_json")
    def test_whale_wallets_btc_uses_blockchain_info(self, mock_get):
        mock_get.return_value = {
            "34xp4vRoCGJym3xR7yCVPFHoCNxv4Twseo": {"final_balance": 24859759064156},
            "3M219KR5vEneNb47ewrPfWyb5jQ2DjxRP6": {"final_balance": 17619685912025},
        }
        evidence = fetch_whale_wallets("BTC")
        self.assertEqual(evidence.data_type, "whale")
        self.assertEqual(len(evidence.content["wallets"]), 2)
        self.assertAlmostEqual(evidence.content["wallets"][0]["balance"], 248597.5906, places=2)

    @patch("src.day2_sources._post_json")
    def test_whale_wallets_eth_uses_public_rpc(self, mock_post):
        mock_post.return_value = {"jsonrpc": "2.0", "id": 1, "result": hex(int(1996008 * 1e18))}
        evidence = fetch_whale_wallets("ETH")
        self.assertEqual(evidence.content["wallets"][0]["balance"], 1996008.0)

    def test_whale_wallets_cover_every_supported_coin(self):
        """五幣都要有地址與瀏覽器連結，否則抽到該幣時鏈上領域會少一條來源鏈。"""
        for coin in ("BTC", "ETH", "BNB", "SOL", "XRP"):
            self.assertTrue(KNOWN_WHALE_ADDRESSES.get(coin), coin)
            self.assertIn(coin, WHALE_EXPLORER_URL, coin)

    def test_whale_wallets_reject_an_unconfigured_coin(self):
        with self.assertRaises(NotImplementedError):
            fetch_whale_wallets("DOGE")

    @patch("src.day2_sources._post_json")
    def test_solana_balance_is_read_in_lamports(self, mock_post):
        """SOL 用 lamports（1e9）。共用 wei 的除數會讓餘額差 9 個數量級。"""
        mock_post.return_value = {"result": {"value": 10_755_443_990_000_000}}

        evidence = fetch_whale_wallets("SOL")

        self.assertEqual(evidence.data_type, "whale")
        self.assertEqual(evidence.content["wallets"][0]["balance"], 10_755_443.99)
        # 持有者未經確認，不得沿用「已知交易所地址」的說法。
        self.assertFalse(evidence.content["owner_attribution_verified"])
        self.assertIn("持有者歸屬未經第一方確認", evidence.content["note"])

    @patch("src.day2_sources._post_json")
    def test_xrp_balance_is_read_in_drops(self, mock_post):
        """XRP 用 drops（1e6），且回應結構與 EVM 的 eth_getBalance 完全不同。"""
        mock_post.return_value = {"result": {"account_data": {"Balance": "10605986220000"}}}

        evidence = fetch_whale_wallets("XRP")

        self.assertEqual(evidence.content["wallets"][0]["balance"], 10_605_986.22)
        self.assertIn("livenet.xrpl.org", evidence.content["wallets"][0]["explorer_url"])

    @patch("src.day2_sources._post_json", return_value={"result": {}})
    def test_missing_balance_raises_instead_of_reporting_zero(self, mock_post):
        """讀不到餘額必須拋錯讓 collector 降級，不能靜靜回報 0 餘額。"""
        for coin in ("SOL", "XRP"):
            with self.assertRaises(ValueError, msg=coin):
                fetch_whale_wallets(coin)

    @patch("src.day2_sources._get_json")
    def test_long_short_ratio_flags_long_dominant_bias(self, mock_get):
        mock_get.return_value = [
            {"symbol": "ETHUSDT", "longAccount": "0.60", "shortAccount": "0.40", "longShortRatio": "1.5", "timestamp": 1785214800000 + i * 3600000}
            for i in range(24)
        ]
        evidence = fetch_long_short_ratio("ETH")
        self.assertEqual(evidence.data_type, "long_short_ratio")
        self.assertEqual(evidence.content["bias"], "long_dominant")
        self.assertEqual(evidence.content["consistency_pct"], 100.0)
        self.assertEqual(evidence.content["current"]["long_pct"], 60.0)

    @patch("src.day2_sources._get_json")
    def test_long_short_ratio_consistency_reflects_mixed_history(self, mock_get):
        rows = [
            {"symbol": "ETHUSDT", "longAccount": "0.60", "shortAccount": "0.40", "longShortRatio": "1.5", "timestamp": 1785214800000},
            {"symbol": "ETHUSDT", "longAccount": "0.40", "shortAccount": "0.60", "longShortRatio": "0.667", "timestamp": 1785218400000},
            {"symbol": "ETHUSDT", "longAccount": "0.60", "shortAccount": "0.40", "longShortRatio": "1.5", "timestamp": 1785222000000},
        ]
        mock_get.return_value = rows
        evidence = fetch_long_short_ratio("ETH")
        # current is long-dominant (last row); only 2 of 3 rows agree
        self.assertAlmostEqual(evidence.content["consistency_pct"], 66.7, places=1)

    @patch("src.day2_sources._get_json")
    def test_vegas_signal_runs_both_timeframes(self, mock_get):
        price = 100.0
        rows = []
        for i in range(1000):
            price *= 1.001
            rows.append([i, price, price * 1.001, price * 0.999, price, 1000.0, i, "0", 1, "0", "0", "0"])
        mock_get.return_value = rows
        evidence = fetch_vegas_signal("ETH")
        self.assertEqual(evidence.data_type, "vegas_channel")
        self.assertEqual(evidence.content["4h"]["trend"], "bullish_aligned")
        self.assertEqual(evidence.content["1h"]["trend"], "bullish_aligned")

    @patch("src.day2_sources._post_json")
    def test_onchain_eth_raises_on_provider_error_instead_of_storing_it(self, mock_post):
        # Regression test: the RPC provider can return a JSON-RPC error envelope with no
        # "result"; the old Etherscan-based code stored provider error text as if it were data.
        mock_post.return_value = {"jsonrpc": "2.0", "id": 1, "error": {"code": -32000, "message": "unauthorized"}}
        with self.assertRaises(ValueError):
            fetch_onchain("ETH")

    @patch("src.day2_sources._post_json")
    def test_onchain_eth_uses_public_rpc_block_number(self, mock_post):
        mock_post.return_value = {"jsonrpc": "2.0", "id": 1, "result": "0x18715cf"}
        evidence = fetch_onchain("ETH")
        self.assertEqual(evidence.content["latest_block_hex"], "0x18715cf")

    @patch("src.orchestrator.analyze_with_llm", side_effect=RuntimeError("provider unavailable"))
    def test_llm_failure_is_logged_and_falls_back(self, _):
        with tempfile.TemporaryDirectory() as directory:
            run("ETH", "分析市場", Path(directory), live=False, use_llm=True)
            log = json.loads((Path(directory) / "execution_log.json").read_text(encoding="utf-8"))
            llm_step = next(step for step in log["steps"] if step["name"] == "llm_reasoning")
            self.assertEqual(llm_step["status"], "fallback:RuntimeError")

    def test_lambda_public_homepage(self):
        response = lambda_handler.handler({"requestContext": {"http": {"method": "GET"}}}, None)
        self.assertEqual(response["statusCode"], 200)
        self.assertIn("加密市場分析", response["body"])

    def test_function_url_has_both_required_permissions(self):
        template = (Path(__file__).parents[1] / "aws" / "template.yaml").read_text(encoding="utf-8")
        self.assertIn("lambda:InvokeFunctionUrl", template)
        self.assertIn("lambda:InvokeFunction", template)
        self.assertIn("InvokedViaFunctionUrl: true", template)


if __name__ == "__main__":
    unittest.main()
