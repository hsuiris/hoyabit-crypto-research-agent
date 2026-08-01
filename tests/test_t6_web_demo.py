"""T6：既有 stdlib Web Demo 的競賽資訊展示與容錯測試。"""

from __future__ import annotations

import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from src.app import _comparison_page, _home_page, _result_page, build_artifact_zip
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
        """介面文字以繁體中文為主；Fact／Inference／Conclusion 保留英文括註對應命題用語。"""
        for required in (
            "研究計畫", "任務類型", "研究時間窗", "必要資料領域", "預設假設",
            "主張：", "事實（Fact）", "推論（Inference）", "結論（Conclusion）", "支持證據",
            "反方證據", "推翻條件", "信心分量", "證據可追溯性", "執行紀錄", "heuristic",
        ):
            self.assertIn(required, self.page)

    def test_page_does_not_leave_english_section_labels(self):
        """回歸：這些英文標籤已全部中文化，重新出現代表有段落沒跟上。"""
        # 不含 `sec-sub` 的小字英文副標：那是刻意保留的輔助標籤（中文標題 + 英文副標）。
        for stale in ("Task modes", "Evidence Traceability", "Confidence Breakdown",
                      "Supporting Evidence", "Watchpoints", "Source type", "Fetched at",
                      "Related Claim", "Invalidation Conditions"):
            self.assertNotIn(stale, self.page)

    def test_evidence_details_are_traceable_and_summarised(self):
        evidence_id = self.evidence[0]["evidence_id"]
        self.assertIn(f'id="evidence-{evidence_id}"', self.page)
        for required in ("來源類別", "取得時間", "內容依據", "驗證狀態",
                         "對應主張", "內容摘要"):
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
        self.assertIn("證據可追溯性", page)
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
        self.assertIn("比較維度", page)


class ArtifactDownloadTests(unittest.TestCase):
    """命題的三份資料文件必須能一鍵取得，不該讓評審逐檔右鍵另存。"""

    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        cls.run_dir = Path(cls._directory.name) / "runs" / "RUN-TEST"
        run("ETH", "下載測試", cls.run_dir, live=False, use_llm=False)

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()

    def test_required_scope_contains_exactly_the_three_submission_documents(self):
        payload, names = build_artifact_zip(self.run_dir, "required")

        self.assertEqual(names, ["report.md", "evidence.json", "execution_log.json"])
        self.assertTrue(payload.startswith(b"PK"), "回傳的不是 ZIP")

    def test_required_scope_files_round_trip_unchanged(self):
        """打包不得改動內容：解出來的位元組要與磁碟上的檔案完全相同。"""
        payload, names = build_artifact_zip(self.run_dir, "required")

        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(sorted(archive.namelist()), sorted(names))
            for name in names:
                self.assertEqual(archive.read(name), (self.run_dir / name).read_bytes(), name)

    def test_all_scope_contains_every_submission_artifact(self):
        _, names = build_artifact_zip(self.run_dir, "all")

        for filename in ARTIFACT_FILENAMES.values():
            self.assertIn(filename, names)

    def test_internal_state_files_are_never_packaged(self):
        """`_checkpoint.json` 是內部狀態，不該被當成提交物送出去。"""
        (self.run_dir / "_checkpoint.json").write_text("{}", encoding="utf-8")
        (self.run_dir / "secret.txt").write_text("nope", encoding="utf-8")
        try:
            _, names = build_artifact_zip(self.run_dir, "all")
        finally:
            (self.run_dir / "_checkpoint.json").unlink(missing_ok=True)
            (self.run_dir / "secret.txt").unlink(missing_ok=True)

        self.assertNotIn("_checkpoint.json", names)
        self.assertNotIn("secret.txt", names)

    def test_comparison_legs_are_included_in_the_all_scope(self):
        """比較執行的兩腳在子目錄裡；少了它們，打包出來的報告會缺內容。"""
        with tempfile.TemporaryDirectory() as directory:
            pair_dir = Path(directory) / "comparison"
            run_comparison("BTC", "ETH", "哪個風險較低？", pair_dir, live=False, use_llm=False)
            _, names = build_artifact_zip(pair_dir, "all")

        self.assertIn("BTC/report.md", names)
        self.assertIn("ETH/report.md", names)

    def test_result_page_offers_both_bundle_downloads(self):
        report = (self.run_dir / "report.md").read_text(encoding="utf-8")
        evidence = json.loads((self.run_dir / "evidence.json").read_text(encoding="utf-8"))
        execution = json.loads((self.run_dir / "execution_log.json").read_text(encoding="utf-8"))
        page = _result_page({"coin": "ETH", "question": "下載測試"}, report, evidence, execution)

        self.assertIn("/download?scope=required", page)
        self.assertIn("/download?scope=all", page)
        # 個別檔案要能直接存檔，而不是只在分頁裡開啟。
        self.assertIn("/artifact?path=report.md&amp;download=1", page)


if __name__ == "__main__":
    unittest.main()
