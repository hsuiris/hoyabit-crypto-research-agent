import csv
import json
import tempfile
import unittest
from pathlib import Path

from src.day1_mvp import mock_evidence
from src.errors import validate_request
from src.llm import ANALYSIS_SCHEMA, build_prompt
from src.orchestrator import run
from src.validation import validate_evidence


class RealInputUpgradeTest(unittest.TestCase):
    def test_all_competition_coins_and_full_evidence_schema(self):
        for coin in ("BTC", "ETH", "SOL", "BNB", "XRP"):
            normalized, _ = validate_request(coin, "分析市場狀況")
            self.assertEqual(normalized, coin)
            evidence = mock_evidence(coin)
            self.assertEqual(validate_evidence(evidence, [item.evidence_id for item in evidence]), [])
            record = json.loads(json.dumps(evidence[0].__dict__))
            self.assertIn("content_reference", record)
            self.assertIn("related_claim", record)

    def test_competition_ohlcv_csv_is_used(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "ETH.csv"
            with path.open("w", newline="", encoding="utf-8") as stream:
                writer = csv.DictWriter(stream, fieldnames=["date", "open", "high", "low", "close", "volume"])
                writer.writeheader()
                for index in range(16):
                    close = 100 + index
                    writer.writerow({"date": f"2026-01-{index + 1:02d}", "open": close, "high": close + 1, "low": close - 1, "close": close, "volume": 1000})
            output = Path(directory) / "out"
            result = run("ETH", "分析市場狀況", output, ohlcv_path=path)
            self.assertEqual(len(result["indicators"]["prices"]), 15)
            self.assertEqual(result["indicators"]["prices"][-1], 115)
            self.assertEqual(json.loads((output / "execution_log.json").read_text())["status"], "success")

    def test_llm_prompt_requires_reasoning_and_citations(self):
        prompt = build_prompt("ETH", "分析市場狀況", [item.__dict__ for item in mock_evidence("ETH")])
        # 三層分離仍是硬要求，只是敘述改成繁體中文（報告讀者是中文使用者）。
        self.assertIn("facts 只寫資料直接顯示的觀察", prompt)
        self.assertIn("解釋寫在 inferences", prompt)
        self.assertIn("對研究問題的回答寫在 conclusion", prompt)
        self.assertIn("繁體中文", prompt)
        self.assertIn("evidence", prompt)
        self.assertIn("cited_evidence_ids", json.dumps(ANALYSIS_SCHEMA))

    def test_llm_prompt_constrains_judgment_and_observations(self):
        """題目沒問就不要預測價格；觀察重點不得只是把 facts 再列一次。"""
        prompt = build_prompt("ETH", "分析市場狀況", [item.__dict__ for item in mock_evidence("ETH")])
        self.assertIn("都必須直接回答上面的 question", prompt)
        self.assertIn("不要在題目沒有要求時給出價格預測", prompt)
        self.assertIn("不要把 facts 的內容或主語再列一次", prompt)


if __name__ == "__main__":
    unittest.main()

