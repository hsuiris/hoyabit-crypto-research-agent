import json
import tempfile
import unittest
from pathlib import Path

from src.orchestrator import run


class Day2OrchestratorTest(unittest.TestCase):
    def test_offline_orchestrator(self):
        with tempfile.TemporaryDirectory() as directory:
            result = run("ETH", "What moved ETH?", Path(directory), live=False)
            self.assertEqual(result["coin"], "ETH")
            self.assertEqual(len(result["evidence_ids"]), 9)
            log = json.loads((Path(directory) / "execution_log.json").read_text(encoding="utf-8"))
            self.assertEqual(log["status"], "success")
            self.assertEqual(log["collection"], ["mock_mode"])


if __name__ == "__main__":
    unittest.main()

