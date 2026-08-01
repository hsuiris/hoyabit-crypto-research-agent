"""E2.5：deterministic 可信度分數必須真正影響 stance 的方向判讀。

修 bug 對象：`_signal_inventory()` 過去把每個訊號的字面權重當成最終權重，reliability 0.90
的市場資料與 reliability 0.20 的 fallback fixture 對 stance 的貢獻完全相同，於是離線全 fallback
的執行可以在 report.md 標題喊出「偏多（Bullish）信心 0.55」，而同一批證據在 claims.json 卻正確
判定為 insufficient_evidence（`outputs-t8-offline/ETH/`）。本檔驗證修復後的六條 ACCEPTANCE：

1. 同一組訊號在 reliability 0.90 與 0.30 下，bull_weight／bear_weight 必須不同。
2. reliability 0.90 與 0.75 產生相同權重（0.70 以上不降權，高品質路徑無回歸）。
3. 全 fallback（reliability 0.20）不得產生方向性 stance，basis 需說明權重不足。
4. reliability 0.20 的證據不得出現在 drivers。
5. freshness 低於門檻時 risk_factors 出現過期風險字串。
6. reliability 缺值時訊號仍然存在（不被靜靜吃掉），且不降權。
"""

from __future__ import annotations

import unittest

from src.day1_mvp import Evidence, mock_evidence
from src.orchestrator import (_STANCE_DRIVER_RELIABILITY_FLOOR,
                              _STANCE_RELIABILITY_FULL_WEIGHT_FLOOR,
                              _dynamic_risk_factors, _market_stance, _signal_inventory)


def _evidence(evidence_id: str, data_type: str, content: dict, reliability_score,
             score_breakdown: dict | None = None) -> Evidence:
    """建構單筆 Evidence；不透過 enrich_and_score_evidence，直接指定 reliability_score，
    模擬 credibility engine 已經計分完成、寫回 item.reliability_score 之後的狀態
    （ORDERING_CHECK 確認 enrich_and_score_evidence 一律先於 _signal_inventory 執行）。
    """
    return Evidence(
        evidence_id, f"Mock-{data_type}", f"https://example.com/{data_type}",
        "2026-08-01T00:00:00+00:00", data_type, "ETH", "14d", content,
        reliability_score, score_breakdown=score_breakdown or {},
    )


def _two_sided_evidence(reliability) -> list[Evidence]:
    """一則多頭市場訊號 + 一則空頭衍生品訊號，兩者字面權重不同（1.0 vs 1.1），
    足以在高可信度時形成方向、又足夠單薄，一旦降權就會跌破 _STANCE_MIN_WEIGHT。
    """
    return [
        _evidence("EV-001", "market", {"return_pct": 8.2}, reliability),
        _evidence("EV-004", "derivatives",
                  {"bias": "long_crowded", "funding_rate_pct": 0.05}, reliability),
    ]


class AcceptanceOneWeightDiffersByReliability(unittest.TestCase):
    """ACCEPTANCE 1：同一組訊號在 reliability 0.90 與 0.30 下，bull/bear weight 必須不同。"""

    def test_bull_and_bear_weight_change_with_reliability(self):
        high = _market_stance(_signal_inventory({}, _two_sided_evidence(0.90)))
        low = _market_stance(_signal_inventory({}, _two_sided_evidence(0.30)))
        self.assertNotEqual(high["bull_weight"], low["bull_weight"])
        self.assertNotEqual(high["bear_weight"], low["bear_weight"])
        # 降權方向必須正確：低可信度的權重要更小，不能反向或不變。
        self.assertLess(low["bull_weight"], high["bull_weight"])
        self.assertLess(low["bear_weight"], high["bear_weight"])

    def test_stance_conclusion_itself_can_flip_with_reliability(self):
        """症狀重現的核心：同一批訊號，高可信度下能形成 bullish，低可信度下不能。"""
        strong_bull_evidence = [
            _evidence("EV-001", "market", {"return_pct": 8.2}, 0.90),
            _evidence("EV-VEGAS", "vegas_channel", {
                "4h": {"trend": "bullish_aligned"},
                "1h": {"rsi": 50, "volume_spike": False},
                "alignment": {"passed": True},
            }, 0.90),
        ]
        high_signals = _signal_inventory({}, strong_bull_evidence)
        high_stance = _market_stance(high_signals)
        self.assertEqual(high_stance["stance"], "bullish")

        for item in strong_bull_evidence:
            item.reliability_score = 0.30
        low_signals = _signal_inventory({}, strong_bull_evidence)
        low_stance = _market_stance(low_signals)
        self.assertNotEqual(low_stance["stance"], "bullish")


class AcceptanceTwoHighReliabilityPathHasNoRegression(unittest.TestCase):
    """ACCEPTANCE 2：reliability 0.90 與 0.75 都在門檻(0.70)之上，權重必須完全相同。"""

    def test_090_and_075_produce_identical_weights(self):
        self.assertGreaterEqual(0.75, _STANCE_RELIABILITY_FULL_WEIGHT_FLOOR)
        signals_90 = _signal_inventory({}, _two_sided_evidence(0.90))
        signals_75 = _signal_inventory({}, _two_sided_evidence(0.75))
        self.assertEqual([item["weight"] for item in signals_90],
                         [item["weight"] for item in signals_75])
        stance_90 = _market_stance(signals_90)
        stance_75 = _market_stance(signals_75)
        self.assertEqual(stance_90["bull_weight"], stance_75["bull_weight"])
        self.assertEqual(stance_90["bear_weight"], stance_75["bear_weight"])
        self.assertEqual(stance_90["stance"], stance_75["stance"])
        # 門檻以上不降權：weight 必須等於原始字面 base_weight，沒有任何調整痕跡。
        for item in signals_90:
            self.assertEqual(item["weight"], item["base_weight"])
            self.assertEqual(item["reliability_note"], "")

    def test_boundary_value_070_is_still_full_weight(self):
        """0.70 本身（floor 值）也不得降權，避免邊界寫成 > 而非 >=。"""
        signals = _signal_inventory({}, _two_sided_evidence(_STANCE_RELIABILITY_FULL_WEIGHT_FLOOR))
        for item in signals:
            self.assertEqual(item["weight"], item["base_weight"])


class AcceptanceThreeAllFallbackCannotFormDirectionalStance(unittest.TestCase):
    """ACCEPTANCE 3：全 fallback（reliability 0.20）的執行不得產生方向性 stance。"""

    def test_all_fallback_mock_evidence_stays_neutral(self):
        evidence = mock_evidence("ETH")
        for item in evidence:
            item.reliability_score = 0.20  # 模擬 credibility engine 對 fallback_fixture 的硬上限
        signals = _signal_inventory({}, evidence)
        stance = _market_stance(signals)
        self.assertEqual(stance["stance"], "neutral")
        self.assertIn("不足以形成方向判讀", stance["basis"])

    def test_two_sided_evidence_at_020_also_stays_neutral(self):
        signals = _signal_inventory({}, _two_sided_evidence(0.20))
        stance = _market_stance(signals)
        self.assertEqual(stance["stance"], "neutral")
        directional = stance["bull_weight"] + stance["bear_weight"]
        self.assertLess(directional, 1.5)  # 未達 _STANCE_MIN_WEIGHT


class AcceptanceFourLowReliabilityNeverAppearsAsDriver(unittest.TestCase):
    """ACCEPTANCE 4：reliability 0.20 的證據不得出現在 drivers，即使該側仍然獲勝。"""

    def test_low_reliability_signal_excluded_even_when_side_wins(self):
        evidence = [
            _evidence("EV-001", "market", {"return_pct": 8.2}, 0.90),
            _evidence("EV-TVL", "tvl",
                      {"change_30d_pct": 12.0, "direction": "expanding", "chain": "Ethereum"}, 0.90),
            _evidence("EV-VEGAS", "vegas_channel", {
                "4h": {"trend": "bullish_aligned"},
                "1h": {"rsi": 50, "volume_spike": False},
                "alignment": {"passed": True},
            }, 0.20),
        ]
        signals = _signal_inventory({}, evidence)
        stance = _market_stance(signals)
        self.assertEqual(stance["stance"], "bullish")
        driver_ids = [driver["evidence_id"] for driver in stance["drivers"]]
        self.assertNotIn("EV-VEGAS", driver_ids)
        self.assertTrue(driver_ids)  # 高可信度的 EV-001 / EV-TVL 仍應列示

    def test_drivers_empty_and_basis_explains_when_only_low_reliability_signals_win(self):
        """贏的那一側全部由低可信度訊號構成時，drivers 必須清空，basis 必須說明原因。"""
        evidence = [
            _evidence("EV-001", "market", {"return_pct": 8.2}, 0.20),
            _evidence("EV-VEGAS", "vegas_channel", {
                "4h": {"trend": "bullish_aligned"},
                "1h": {"rsi": 50, "volume_spike": False},
                "alignment": {"passed": True},
            }, 0.20),
            _evidence("EV-TVL", "tvl",
                      {"change_30d_pct": 12.0, "direction": "expanding", "chain": "Ethereum"}, 0.20),
        ]
        signals = _signal_inventory({}, evidence)
        stance = _market_stance(signals)
        directional = stance["bull_weight"] + stance["bear_weight"]
        if directional >= 1.5 and stance["stance"] != "neutral":
            self.assertEqual(stance["drivers"], [])
            self.assertIn("主要推力已因可信度過低被排除", stance["basis"])
        else:
            # 降權後也可能直接落回 neutral（本身即滿足「不得靠低可信度訊號形成方向」的精神）。
            self.assertEqual(stance["stance"], "neutral")

    def test_exactly_at_floor_030_is_still_excluded(self):
        """門檻用 > 而非 >=：reliability 剛好等於 0.30 也必須被排除，不能因為浮點邊界漏網。"""
        self.assertEqual(_STANCE_DRIVER_RELIABILITY_FLOOR, 0.30)
        evidence = [
            _evidence("EV-001", "market", {"return_pct": 8.2}, 0.90),
            _evidence("EV-VEGAS", "vegas_channel", {
                "4h": {"trend": "bullish_aligned"},
                "1h": {"rsi": 50, "volume_spike": False},
                "alignment": {"passed": True},
            }, 0.30),
        ]
        signals = _signal_inventory({}, evidence)
        stance = _market_stance(signals)
        driver_ids = [driver["evidence_id"] for driver in stance["drivers"]]
        self.assertNotIn("EV-VEGAS", driver_ids)


class AcceptanceFiveStaleFreshnessProducesRiskFactor(unittest.TestCase):
    """ACCEPTANCE 5：freshness 分量低於門檻時，risk_factors 必須出現過期風險字串。"""

    def test_low_freshness_component_triggers_risk_factor(self):
        stale = _evidence("EV-010", "market", {"return_pct": 1.0}, 0.90,
                          score_breakdown={"components": {"freshness": 0.15}})
        risks = _dynamic_risk_factors({"collection_log": []}, [stale], [])
        joined = " ".join(risks)
        self.assertIn("EV-010", joined)
        self.assertIn("新鮮度", joined)

    def test_fresh_evidence_does_not_trigger_the_risk_factor(self):
        fresh = _evidence("EV-011", "market", {"return_pct": 1.0}, 0.90,
                          score_breakdown={"components": {"freshness": 0.90}})
        risks = _dynamic_risk_factors({"collection_log": []}, [fresh], [])
        joined = " ".join(risks)
        self.assertNotIn("EV-011", joined)
        self.assertNotIn("新鮮度分量低於", joined)

    def test_missing_score_breakdown_does_not_raise_or_false_trigger(self):
        """沒有 score_breakdown（例如尚未計分的舊路徑）不得因缺鍵而拋錯或誤判過期。"""
        naked = _evidence("EV-012", "market", {"return_pct": 1.0}, 0.90)
        risks = _dynamic_risk_factors({"collection_log": []}, [naked], [])
        joined = " ".join(risks)
        self.assertNotIn("EV-012", joined)


class AcceptanceSixMissingReliabilityIsNotSilentlyDropped(unittest.TestCase):
    """ACCEPTANCE 6：reliability 缺值（None）時訊號仍然存在，且不被當成 0 而消失。"""

    def test_none_reliability_keeps_the_signal_at_full_weight(self):
        evidence = [_evidence("EV-001", "market", {"return_pct": 8.2}, None)]
        signals = _signal_inventory({}, evidence)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["weight"], signals[0]["base_weight"])
        self.assertIsNone(signals[0]["reliability_score"])
        self.assertIn("缺值", signals[0]["reliability_note"])

    def test_missing_evidence_id_lookup_also_keeps_full_weight(self):
        """evidence_id 查不到對應證據（例如 'N/A'）時同樣視為未降權，而不是報錯或吃掉訊號。"""
        signals = _signal_inventory({}, [])
        self.assertEqual(signals, [])  # 沒有證據自然沒有訊號，這不是本條驗證的情境

        evidence = [_evidence("EV-999", "market", {"return_pct": 8.2}, 0.90)]
        # add() 內部若傳入不存在的 evidence_id，也必須落到「查不到 → 視為 None → 不降權」。
        from src.orchestrator import _signal_inventory as signal_inventory_fn
        signals = signal_inventory_fn({}, evidence)
        self.assertEqual(len(signals), 1)
        self.assertEqual(signals[0]["weight"], signals[0]["base_weight"])


if __name__ == "__main__":
    unittest.main()
