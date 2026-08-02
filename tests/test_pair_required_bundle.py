"""比較執行的「三份提交物」ZIP 必須有內容。

背景（實測缺陷）：`_collect_downloadables()` 在 `scope=required` 時只掃 run 根目錄，而比較執行
把 `report.md`／`evidence.json`／`execution_log.json` 寫在 `<run>/ETH/`、`<run>/BTC/` 子目錄，
根目錄只有 `comparison.*`／`claims.json`／`manifest.json`／`research_plan.json`。於是打包清單為空，
`/download?scope=required&run=<比較 run>` 在本機與 Lambda 兩側都回 404 —— 而命題「提交項目」
要求分析報告、Evidence List 與執行紀錄一鍵取得，那正是評審最可能先按的一顆按鈕。

這組測試釘住四件事：

1. 單幣執行的行為完全不變（含檔名順序與位元組）。
2. 比較執行退到子目錄後，六個項目齊備且帶幣種前綴。
3. 退路只走 `REQUIRED_ARTIFACTS` 白名單，不夾帶 `_checkpoint.json` 這類內部狀態檔。
4. 順序穩定：同一個 run 連續打包兩次得到同一份清單。

所有產物都以 `live=False, use_llm=False` 的離線路徑產生，測試不觸網、不呼叫模型。
"""

from __future__ import annotations

import base64
import io
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import lambda_handler as lambda_module
from src.app import REQUIRED_ARTIFACTS, build_artifact_zip
from src.orchestrator import run, run_comparison

# 比較執行寫在 run 根目錄的產物：它們屬於 scope=all，不該出現在「三份提交物」ZIP 裡。
_COMPARISON_ROOT_ONLY = ("comparison.md", "comparison.json", "claims.json",
                         "manifest.json", "research_plan.json")


def _get(path: str, query: dict | None = None) -> dict:
    return {
        "rawPath": path,
        "requestContext": {"http": {"method": "GET", "path": path}},
        "queryStringParameters": query or {},
    }


class _OfflineRuns(unittest.TestCase):
    """一個單幣 run 與一個比較 run，兩者都是真實產物（離線路徑）。"""

    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        root = Path(cls._directory.name) / "runs"
        cls.single_dir = root / "RUN-SINGLE"
        cls.pair_dir = root / "RUN-PAIR"
        run("ETH", "required 打包測試", cls.single_dir, live=False, use_llm=False)
        run_comparison("ETH", "BTC", "哪個風險較低？", cls.pair_dir,
                       live=False, use_llm=False)

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()


class SingleCoinRequiredBundleTests(_OfflineRuns):
    """單幣執行是既有行為的基準線，修比較執行不得動到它。"""

    def test_single_coin_required_scope_keeps_the_exact_three_names_and_order(self):
        _, names = build_artifact_zip(self.single_dir, "required")

        self.assertEqual(names, ["report.md", "evidence.json", "execution_log.json"])

    def test_single_coin_required_scope_round_trips_the_bytes_unchanged(self):
        payload, names = build_artifact_zip(self.single_dir, "required")

        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(sorted(archive.namelist()), sorted(names))
            for name in names:
                self.assertEqual(archive.read(name),
                                 (self.single_dir / name).read_bytes(), name)


class ComparisonRequiredBundleTests(_OfflineRuns):
    """比較執行的三份提交物在子目錄；required 範圍必須跟進去收。"""

    def test_comparison_required_scope_contains_both_legs(self):
        payload, names = build_artifact_zip(self.pair_dir, "required")

        self.assertTrue(names, "比較 run 的 required 清單是空的 → /download 會回 404")
        for coin in ("ETH", "BTC"):
            for name in REQUIRED_ARTIFACTS:
                self.assertIn(f"{coin}/{name}", names)
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(len(archive.namelist()), 6, archive.namelist())
            self.assertEqual(archive.read("ETH/report.md"),
                             (self.pair_dir / "ETH" / "report.md").read_bytes())

    def test_comparison_required_scope_excludes_the_root_only_artifacts(self):
        """比較檔與 manifest 屬於 scope=all；混進 required 會讓「三份提交物」名不副實。"""
        _, names = build_artifact_zip(self.pair_dir, "required")

        for name in _COMPARISON_ROOT_ONLY:
            self.assertNotIn(name, names)
            self.assertNotIn(f"ETH/{name}", names)
            self.assertNotIn(f"BTC/{name}", names)

    def test_comparison_required_scope_never_packages_non_whitelisted_files(self):
        (self.pair_dir / "_checkpoint.json").write_text("{}", encoding="utf-8")
        (self.pair_dir / "ETH" / "_checkpoint.json").write_text("{}", encoding="utf-8")
        (self.pair_dir / "ETH" / "secret.txt").write_text("nope", encoding="utf-8")
        try:
            _, names = build_artifact_zip(self.pair_dir, "required")
        finally:
            (self.pair_dir / "_checkpoint.json").unlink(missing_ok=True)
            (self.pair_dir / "ETH" / "_checkpoint.json").unlink(missing_ok=True)
            (self.pair_dir / "ETH" / "secret.txt").unlink(missing_ok=True)

        for leaked in ("_checkpoint.json", "ETH/_checkpoint.json", "ETH/secret.txt"):
            self.assertNotIn(leaked, names)

    def test_comparison_all_scope_behaviour_is_unchanged(self):
        _, names = build_artifact_zip(self.pair_dir, "all")

        for name in _COMPARISON_ROOT_ONLY:
            self.assertIn(name, names)
        for coin in ("ETH", "BTC"):
            for name in (*REQUIRED_ARTIFACTS, "claims.json", "manifest.json",
                         "research_plan.json"):
                self.assertIn(f"{coin}/{name}", names)

    def test_packaging_the_same_run_twice_yields_the_same_ordered_names(self):
        """順序漂移會讓兩次下載的 ZIP 無法互相核對。"""
        _, first = build_artifact_zip(self.pair_dir, "required")
        _, second = build_artifact_zip(self.pair_dir, "required")

        self.assertEqual(first, second)


class ComparisonRequiredBundleOverLambdaTests(_OfflineRuns):
    """Demo 跑在 Lambda：同一顆按鈕在 Function URL 這一側也必須是 200。"""

    def test_lambda_required_download_returns_a_valid_zip_for_a_comparison_run(self):
        with patch.object(lambda_module, "_LATEST_RUN_DIR", self.pair_dir):
            response = lambda_module.handler(_get("/download", {"scope": "required"}), None)

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(response["headers"]["content-type"], "application/zip")
        self.assertTrue(response["isBase64Encoded"])
        with zipfile.ZipFile(io.BytesIO(base64.b64decode(response["body"]))) as archive:
            self.assertIsNone(archive.testzip())
            self.assertEqual(
                sorted(archive.namelist()),
                ["BTC/evidence.json", "BTC/execution_log.json", "BTC/report.md",
                 "ETH/evidence.json", "ETH/execution_log.json", "ETH/report.md"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
