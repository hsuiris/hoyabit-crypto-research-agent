"""T6：既有 stdlib Web Demo 的競賽資訊展示與容錯測試。"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.app import _comparison_page, _home_page, _result_page
from src.orchestrator import run, run_comparison
from src.schemas import ARTIFACT_FILENAMES


class CompetitionWebDemoTests(unittest.TestCase):
    """所有資料均使用 offline fallback，測試不連外。"""

    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        cls.output = Path(cls._directory.name)
        cls.result = run(
            "ETH", "近期市場、新聞與鏈上訊號呈現什麼風險？", cls.output,
            live=False, use_llm=False,
        )
        cls.report = (cls.output / "report.md").read_text(encoding="utf-8")
        cls.evidence = json.loads((cls.output / "evidence.json").read_text(encoding="utf-8"))
        cls.execution = json.loads((cls.output / "execution_log.json").read_text(encoding="utf-8"))
        cls.page = _result_page(cls.result, cls.report, cls.evidence, cls.execution)
        cls.comparison = run_comparison(
            "BTC", "ETH", "BTC 與 ETH 哪個風險較低？", cls.output / "comparison",
            live=False, use_llm=False,
        )

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()

    def test_home_keeps_single_comparison_live_and_llm_inputs(self):
        page = _home_page()
        for required in ("name=\"coin\"", "name=\"question\"", "name=\"compare_with\"",
                         "name=\"live\"", "name=\"use_llm\""):
            self.assertIn(required, page)

    def test_single_coin_page_renders_competition_sections(self):
        for required in (
            "Research Plan", "Task modes", "Time window", "Required domains", "Assumptions",
            "Claim：", "Fact", "Inference", "Conclusion", "Supporting Evidence",
            "Contradicting Evidence", "Invalidation Conditions", "Confidence Breakdown",
            "Evidence Traceability", "Execution Log", "heuristic",
        ):
            self.assertIn(required, self.page)

    def test_evidence_details_are_traceable_and_summarised(self):
        evidence_id = self.evidence[0]["evidence_id"]
        self.assertIn(f'id="evidence-{evidence_id}"', self.page)
        for required in ("Source type", "Fetched at", "Content reference", "Verification status",
                         "Related Claim", "內容摘要"):
            self.assertIn(required, self.page)

    def test_artifact_links_include_all_six_submission_outputs(self):
        for filename in ARTIFACT_FILENAMES.values():
            self.assertIn(f"/artifact?path={filename}", self.page)

    def test_old_fixture_missing_competition_fields_degrades_to_na(self):
        legacy = dict(self.evidence[0])
        for key in ("source_type", "verification_status", "related_claim_ids", "score_limiters",
                    "content_reference", "published_at", "event_time"):
            legacy.pop(key, None)
        page = _result_page(self.result, self.report, [legacy], self.execution)
        self.assertIn("Evidence Traceability", page)
        self.assertIn("N/A", page)

    def test_partial_run_without_claims_does_not_crash(self):
        partial = dict(self.result)
        partial["claims"] = []
        page = _result_page(partial, self.report, [], {"steps": []})
        self.assertIn("本次沒有可展示的 Claim", page)
        self.assertIn("N/A", page)

    def test_comparison_page_displays_shared_time_window_and_plan(self):
        page = _comparison_page(self.comparison)
        self.assertIn("共用時間範圍", page)
        self.assertIn("共用研究計畫", page)
        self.assertIn("Comparison dimensions", page)


if __name__ == "__main__":
    unittest.main()
