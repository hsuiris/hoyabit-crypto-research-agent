"""Lambda Function URL 的提交物下載路由測試。

背景（實測缺陷）：`/download` 與 `/artifact` 原本只加在 `src/app.py` 的 stdlib
`Handler.do_GET`，而 Function URL 走的是 `lambda_handler.handler()` —— 兩邊各自分派路由。
結果是下載按鈕在本機可用、在雲端回首頁，而 Demo 正是跑在雲端。

這組測試釘住兩件事：

1. 兩個入口都要認得同一組路由（`src/app.py` 加了路由但忘記加到 Lambda 這側時會失敗）。
2. ZIP 必須以 base64 回傳並標示 `isBase64Encoded`，否則 Function URL 會把二進位當成文字
   送出，下載到的檔案會損毀 —— 而且瀏覽器不會報錯，只有解壓時才發現。
"""

from __future__ import annotations

import base64
import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import lambda_handler as lambda_module
from src.orchestrator import run
from src.schemas import ARTIFACT_FILENAMES


def _get(path: str, query: dict | None = None) -> dict:
    return {
        "rawPath": path,
        "requestContext": {"http": {"method": "GET", "path": path}},
        "queryStringParameters": query or {},
    }


class RouteParityTests(unittest.TestCase):
    """stdlib server 與 Lambda 必須認得同一組路由。"""

    def test_lambda_serves_the_same_download_scopes_as_the_web_app(self):
        from src.app import DOWNLOAD_SCOPES

        for scope in DOWNLOAD_SCOPES:
            response = lambda_module.handler(_get("/download", {"scope": scope}), None)
            # 沒有可下載的 run 時回 404 是預期的；不能回 200 首頁（代表路由沒配對）。
            self.assertIn(response["statusCode"], (200, 404), scope)
            self.assertNotIn("開始分析", response["body"], f"{scope} 落回首頁，路由沒配對")

    def test_unknown_scope_is_rejected(self):
        response = lambda_module.handler(_get("/download", {"scope": "evil"}), None)
        self.assertEqual(response["statusCode"], 400)

    def test_artifact_path_outside_the_whitelist_is_rejected(self):
        response = lambda_module.handler(_get("/artifact", {"path": "../../.env"}), None)
        self.assertEqual(response["statusCode"], 404)

    def test_run_id_with_a_path_separator_is_rejected(self):
        response = lambda_module.handler(_get("/download", {"run": "../../etc"}), None)
        self.assertEqual(response["statusCode"], 404)

    def test_home_page_still_answers_the_root_path(self):
        response = lambda_module.handler(_get("/"), None)
        self.assertEqual(response["statusCode"], 200)
        self.assertIn("開始分析", response["body"])


class BundleContentTests(unittest.TestCase):
    """有產物時，下載的內容與編碼都要正確。"""

    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        cls.run_dir = Path(cls._directory.name) / "runs" / "RUN-LAMBDA-TEST"
        run("ETH", "Lambda 下載測試", cls.run_dir, live=False, use_llm=False)

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()

    def _with_run(self, event):
        with patch.object(lambda_module, "_LATEST_RUN_DIR", self.run_dir):
            return lambda_module.handler(event, None)

    def test_required_scope_returns_a_base64_encoded_zip(self):
        response = self._with_run(_get("/download", {"scope": "required"}))

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(response["headers"]["content-type"], "application/zip")
        # 少了這個旗標，Function URL 會把 ZIP 當文字送出，下載到的檔案會損毀。
        self.assertTrue(response["isBase64Encoded"])
        self.assertIn("attachment", response["headers"]["content-disposition"])

        payload = base64.b64decode(response["body"])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(sorted(archive.namelist()),
                             ["evidence.json", "execution_log.json", "report.md"])
            self.assertIsNone(archive.testzip())
            self.assertEqual(archive.read("report.md"),
                             (self.run_dir / "report.md").read_bytes())

    def test_all_scope_contains_every_submission_artifact(self):
        response = self._with_run(_get("/download", {"scope": "all"}))

        with zipfile.ZipFile(io.BytesIO(base64.b64decode(response["body"]))) as archive:
            for filename in ARTIFACT_FILENAMES.values():
                self.assertIn(filename, archive.namelist(), filename)

    def test_single_artifact_is_served_as_text(self):
        response = self._with_run(_get("/artifact", {"path": "report.md"}))

        self.assertEqual(response["statusCode"], 200)
        self.assertIn("text/plain", response["headers"]["content-type"])
        self.assertNotIn("content-disposition", response["headers"])
        self.assertTrue(response["body"].startswith("# ETH 市場研究報告"))

    def test_single_artifact_download_flag_adds_the_attachment_header(self):
        response = self._with_run(_get("/artifact", {"path": "evidence.json", "download": "1"}))

        self.assertIn("attachment", response["headers"]["content-disposition"])
        self.assertIn("evidence.json", response["headers"]["content-disposition"])
        json.loads(response["body"])  # 內容必須仍是合法 JSON

    def test_missing_artifact_reports_the_container_limitation(self):
        """回 404 時要說明產物只存在處理該次執行的容器，否則看起來像執行沒有產出。"""
        with patch.object(lambda_module, "_LATEST_RUN_DIR", None):
            response = lambda_module.handler(_get("/download", {"scope": "all"}), None)

        self.assertEqual(response["statusCode"], 404)
        self.assertIn("容器", response["body"])


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
