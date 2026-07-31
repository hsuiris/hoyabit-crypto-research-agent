import json
import tempfile
import unittest
from pathlib import Path

from src.day1_mvp import run


class Day1MvpTest(unittest.TestCase):
    def test_end_to_end_outputs(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run("ETH", "近期上漲的主要原因是什麼？", Path(directory))
            self.assertEqual(result["coin"], "ETH")
            self.assertEqual(len(result["evidence_ids"]), 9)
            self.assertTrue((Path(directory) / "report.md").exists())
            evidence = json.loads((Path(directory) / "evidence.json").read_text(encoding="utf-8"))
            self.assertEqual(len(evidence), 9)
            self.assertEqual(json.loads((Path(directory) / "execution_log.json").read_text(encoding="utf-8"))["status"], "success")


if __name__ == "__main__":
    unittest.main()

