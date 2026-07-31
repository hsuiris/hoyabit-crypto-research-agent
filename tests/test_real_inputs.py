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
        self.assertIn("Separate facts from inferences and conclusion", prompt)
        self.assertIn("evidence", prompt)
        self.assertIn("cited_evidence_ids", json.dumps(ANALYSIS_SCHEMA))


if __name__ == "__main__":
    unittest.main()

