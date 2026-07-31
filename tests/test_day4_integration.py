import json
import tempfile
import unittest
from pathlib import Path

from src.orchestrator import run


class Day4IntegrationTest(unittest.TestCase):
    def test_full_pipeline(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run("ETH", "What moved ETH?", Path(directory), live=False)
            self.assertIn("return_pct", result["indicators"])
            self.assertTrue((Path(directory) / "report.md").read_text(encoding="utf-8").startswith("# ETH"))
            evidence = json.loads((Path(directory) / "evidence.json").read_text(encoding="utf-8"))
            self.assertEqual({item["evidence_id"] for item in evidence}, set(result["evidence_ids"]))
            log = json.loads((Path(directory) / "execution_log.json").read_text(encoding="utf-8"))
            self.assertEqual(log["status"], "success")
            self.assertGreaterEqual(log["duration_ms"], 0)


if __name__ == "__main__":
    unittest.main()

