"""資金費率備援鏈測試。

背景：`derivatives` 領域只有 funding rate 與 long/short ratio 兩個 collector，兩者原本都只打
Binance。Binance 在 AWS us-west-2 被地域封鎖，因此雲端執行時整個領域從報告裡消失 —— 這不是
「資料不足」，是唯一的來源被擋住。

備援鏈補上 Kraken Futures（美國合規交易所）與 dYdX v4（去中心化）。這組測試釘住三件事：

1. Binance 可用時行為完全不變（8 小時費率直接使用，不做換算）。
2. Binance 失敗時會換下一個交易所，且**費率換算成 8 小時等值**——Kraken 與 dYdX 都是每小時
   結算，直接把每小時費率填進 ±0.01% 的 bias 門檻會讓同一個市場狀態跳一個等級。
3. 走備援不等於降級：資料仍是即時第一手，但證據必須記錄實際回答的交易所與其原生結算週期，
   否則讀者無法重現那個數字，也無法知道 bias 是哪個交易所的。

外部 API 一律 mock，測試不依賴網路。
"""

from __future__ import annotations

import unittest
from unittest.mock import patch
from urllib.error import URLError

from src.claim_graph import DOMAIN_BY_DATA_TYPE
from src.day2_sources import (
    DYDX_MARKETS,
    FUNDING_RATE_PROVIDERS,
    FUNDING_SETTLEMENT_HOURS,
    FUTURES_SYMBOLS,
    KRAKEN_FUTURES_SYMBOLS,
    _SOURCE_LOADERS,
    fetch_funding_rate,
)

# 實測值（2026-08-01）：ETH 每小時 -0.00002281，8 小時等值 -0.01825%。
KRAKEN_TICKERS = {"tickers": [
    {"symbol": "PF_ETHUSD", "fundingRate": -0.04256642848808271, "markPrice": 1866.0314112078},
    {"symbol": "PF_XBTUSD", "fundingRate": 0.08520756363902102, "markPrice": 63011.17591907267},
]}
DYDX_MARKETS_PAYLOAD = {"markets": {
    "ETH-USD": {"nextFundingRate": "-0.00002229787234042553", "oraclePrice": "1867.200928"},
    "BTC-USD": {"nextFundingRate": "0.00000169946808510638", "oraclePrice": "63018.37939"},
}}
BINANCE_PREMIUM = {"lastFundingRate": "0.0001", "markPrice": "1866.00",
                   "nextFundingTime": 1785600000000}


def _blocked(*args, **kwargs):
    """模擬 Binance 對美國 IP 的地域封鎖。"""
    raise URLError("HTTP Error 451: Unavailable For Legal Reasons")


class ProviderConfigurationTests(unittest.TestCase):
    def test_binance_stays_the_preferred_provider(self):
        """Binance 是唯一原生 8 小時的來源，不需換算，因此排第一。"""
        self.assertEqual(FUNDING_RATE_PROVIDERS[0][0], "Binance Futures")

    def test_every_supported_coin_is_mapped_on_every_provider(self):
        for coin in FUTURES_SYMBOLS:
            self.assertIn(coin, KRAKEN_FUTURES_SYMBOLS, coin)
            self.assertIn(coin, DYDX_MARKETS, coin)

    def test_there_is_more_than_one_provider(self):
        """只有一個來源時，一次地域封鎖就會讓 derivatives 領域整個消失。"""
        self.assertGreaterEqual(len(FUNDING_RATE_PROVIDERS), 2)

    def test_derivatives_domain_survival_depends_on_a_non_binance_provider(self):
        """結構保證：derivatives 領域只有兩個 collector，且另一個沒有非 Binance 替代品。

        `long_short_ratio` 的大戶多空比在 Kraken Futures 與 dYdX 都沒有等價端點，因此整個
        derivatives 領域能不能在雲端存活，完全取決於 funding rate 有沒有非 Binance 的來源。
        這條測試會在有人把備援砍成只剩 Binance 時失敗。
        """
        collectors = sorted(label for label, _ in _SOURCE_LOADERS
                            if DOMAIN_BY_DATA_TYPE.get(label) == "derivatives")
        self.assertEqual(collectors, ["derivatives", "long_short_ratio"])

        non_binance = [name for name, _ in FUNDING_RATE_PROVIDERS if "Binance" not in name]
        self.assertTrue(non_binance, "沒有非 Binance 的資金費率來源，雲端會整個領域歸零")


class BinanceUnchangedTests(unittest.TestCase):
    @patch("src.day2_sources._get_json", return_value=BINANCE_PREMIUM)
    def test_binance_rate_is_used_without_conversion(self, mock_get):
        evidence = fetch_funding_rate("ETH")
        content = evidence.content

        self.assertEqual(content["provider"], "Binance Futures")
        # 0.0001 -> 0.01%，原生就是 8 小時，不得再乘 8。
        self.assertEqual(content["funding_rate_pct"], 0.01)
        self.assertEqual(content["provider_native_interval_hours"], 8)
        self.assertEqual(content["symbol"], "ETHUSDT")
        self.assertIsNone(content["providers_tried"])

    @patch("src.day2_sources._get_json", return_value=BINANCE_PREMIUM)
    def test_evidence_identity_and_domain_are_unchanged(self, mock_get):
        """evidence ID 與 data_type 不能變，否則腳註編號與領域對應會跟著跑。"""
        evidence = fetch_funding_rate("ETH")

        self.assertEqual(evidence.evidence_id, "EV-DERIV-ETH-001")
        self.assertEqual(evidence.data_type, "derivatives")
        self.assertEqual(evidence.reliability_score, 0.75)


class FallbackChainTests(unittest.TestCase):
    def _with(self, side_effect):
        with patch("src.day2_sources._get_json", side_effect=side_effect):
            return fetch_funding_rate("ETH")

    def test_kraken_answers_when_binance_is_geo_blocked(self):
        def responses(url, *args, **kwargs):
            if "binance" in url:
                _blocked()
            if "kraken" in url:
                return KRAKEN_TICKERS
            raise AssertionError("不該問到 dYdX：Kraken 已經回答了")

        evidence = self._with(responses)
        content = evidence.content

        self.assertEqual(content["provider"], "Kraken Futures")
        self.assertEqual(content["symbol"], "PF_ETHUSD")
        # 每小時 fundingRate / markPrice，再換算成 8 小時等值。
        self.assertAlmostEqual(content["funding_rate_pct"], -0.01825, places=4)
        self.assertEqual(content["provider_native_interval_hours"], 1)
        self.assertEqual(content["funding_interval_hours"], FUNDING_SETTLEMENT_HOURS)

    def test_dydx_answers_when_binance_and_kraken_both_fail(self):
        def responses(url, *args, **kwargs):
            if "dydx" in url:
                return DYDX_MARKETS_PAYLOAD
            _blocked()

        evidence = self._with(responses)
        content = evidence.content

        self.assertEqual(content["provider"], "dYdX v4")
        self.assertEqual(content["symbol"], "ETH-USD")
        self.assertAlmostEqual(content["funding_rate_pct"], -0.01784, places=4)
        self.assertEqual(content["provider_native_interval_hours"], 1)

    def test_the_two_hourly_providers_agree_after_normalisation(self):
        """換算若寫錯，兩個獨立來源不會落在同一個量級 —— 這條就是換算的守門測試。"""
        kraken = dict(KRAKEN_TICKERS["tickers"][0])
        hourly_kraken = kraken["fundingRate"] / kraken["markPrice"]
        hourly_dydx = float(DYDX_MARKETS_PAYLOAD["markets"]["ETH-USD"]["nextFundingRate"])

        self.assertAlmostEqual(hourly_kraken * FUNDING_SETTLEMENT_HOURS * 100, -0.01825, places=4)
        self.assertAlmostEqual(hourly_dydx * FUNDING_SETTLEMENT_HOURS * 100, -0.01784, places=4)

    def test_failed_providers_are_recorded(self):
        """讀者要能看出這筆資料不是首選來源給的，以及首選為什麼沒回答。"""
        def responses(url, *args, **kwargs):
            if "kraken" in url:
                return KRAKEN_TICKERS
            _blocked()

        content = self._with(responses).content

        self.assertTrue(content["providers_tried"])
        self.assertIn("Binance Futures", content["providers_tried"][0])
        self.assertIn("URLError", content["providers_tried"][0])

    def test_the_reading_is_labelled_as_single_venue(self):
        """同一時點不同交易所的費率可能相反，報告不得把它當全市場共識。"""
        def responses(url, *args, **kwargs):
            if "kraken" in url:
                return KRAKEN_TICKERS
            _blocked()

        content = self._with(responses).content

        self.assertIn("Kraken Futures", content["scope_note"])
        self.assertIn("不可視為全市場共識", content["scope_note"])

    def test_all_providers_failing_raises_so_the_collector_degrades(self):
        with self.assertRaises(ValueError) as caught:
            self._with(_blocked)

        message = str(caught.exception)
        for provider_name, _ in FUNDING_RATE_PROVIDERS:
            self.assertIn(provider_name, message)


class BiasThresholdTests(unittest.TestCase):
    """bias 門檻是以 8 小時為基準訂的；換算若漏掉，同一狀態會跳一個等級。"""

    def _kraken_rate(self, hourly_fraction: float) -> dict:
        payload = {"tickers": [{"symbol": "PF_ETHUSD",
                                "fundingRate": hourly_fraction * 100.0, "markPrice": 100.0}]}

        def responses(url, *args, **kwargs):
            if "kraken" in url:
                return payload
            _blocked()

        with patch("src.day2_sources._get_json", side_effect=responses):
            return fetch_funding_rate("ETH").content

    def test_hourly_rate_below_the_threshold_stays_balanced(self):
        # 每小時 0.000001 -> 8 小時 0.0008%，仍在 ±0.01% 內。
        self.assertEqual(self._kraken_rate(0.000001)["bias"], "balanced")

    def test_normalised_rate_above_the_threshold_flags_long_crowded(self):
        # 每小時 0.00001 -> 8 小時 0.008%（仍 balanced）；0.00002 -> 0.016%（越線）。
        self.assertEqual(self._kraken_rate(0.00001)["bias"], "balanced")
        self.assertEqual(self._kraken_rate(0.00002)["bias"], "long_crowded")

    def test_normalised_negative_rate_flags_short_crowded(self):
        self.assertEqual(self._kraken_rate(-0.00002)["bias"], "short_crowded")


class ProviderErrorHandlingTests(unittest.TestCase):
    def test_kraken_missing_symbol_moves_on_instead_of_crashing(self):
        def responses(url, *args, **kwargs):
            if "binance" in url:
                _blocked()
            if "kraken" in url:
                return {"tickers": [{"symbol": "PF_SOMETHINGELSE"}]}
            return DYDX_MARKETS_PAYLOAD

        with patch("src.day2_sources._get_json", side_effect=responses):
            self.assertEqual(fetch_funding_rate("ETH").content["provider"], "dYdX v4")

    def test_kraken_zero_mark_price_does_not_divide_by_zero(self):
        def responses(url, *args, **kwargs):
            if "binance" in url:
                _blocked()
            if "kraken" in url:
                return {"tickers": [{"symbol": "PF_ETHUSD", "fundingRate": 1.0, "markPrice": 0}]}
            return DYDX_MARKETS_PAYLOAD

        with patch("src.day2_sources._get_json", side_effect=responses):
            self.assertEqual(fetch_funding_rate("ETH").content["provider"], "dYdX v4")

    def test_dydx_missing_market_moves_on(self):
        def responses(url, *args, **kwargs):
            if "dydx" in url:
                return {"markets": {}}
            _blocked()

        with patch("src.day2_sources._get_json", side_effect=responses):
            with self.assertRaises(ValueError):
                fetch_funding_rate("ETH")


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
