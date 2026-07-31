import tempfile
import unittest
from pathlib import Path

from src.errors import AgentInputError
from src.orchestrator import run


class Day5QualityTest(unittest.TestCase):
    def test_invalid_input_is_actionable(self):
        with self.assertRaisesRegex(AgentInputError, "僅支援"):
            run("DOGE", "分析", Path(tempfile.mkdtemp()))
        with self.assertRaisesRegex(AgentInputError, "不可為空白"):
            run("ETH", " ", Path(tempfile.mkdtemp()))

    def test_report_has_sources_and_limits(self):
        with tempfile.TemporaryDirectory() as directory:
            run("ETH", "What moved ETH?", Path(directory))
            report = (Path(directory) / "report.md").read_text(encoding="utf-8")
            self.assertIn("Evidence Sources", report)
            self.assertIn("https://example.com/market", report)
            self.assertIn("Risks and Limitations", report)
            self.assertIn("not investment advice", report)


if __name__ == "__main__":
    unittest.main()

