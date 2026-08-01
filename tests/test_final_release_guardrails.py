"""E1：公開 Lambda Function URL 必須是 test-only demo。

背景：這個 Function URL 的 AuthType 是 NONE，任何取得網址的人都能呼叫。formal 執行對同一
「問題＋幣種」只允許一次（見 `src/run_manager.py` 的 formal lock），一旦被匿名觸發就會把這
唯一一次額度用掉，且事後無法回溯是誰送出的。因此公開端點必須：

1. 首頁不得提供 formal（正式）可選項，並清楚標示只提供 test mode。
2. `mode=formal`（任何大小寫）與任何帶有實際值的 authorized_rerun／rerun_of／rerun_reason／
   rerun 旗標，一律在建立 RunManager／呼叫 collector／LLM 之前回 HTTP 403，不得偷偷把
   formal 降級成 test 後繼續執行。
3. 其他未知 mode 回 HTTP 400。
4. 未帶 mode 或 mode=test 仍走既有成功路徑，且既有的 `/download`、`/artifact` 路由不受影響。

本機 CLI／`src/app.py` 的 formal 能力不在本檔案的守備範圍，也不受這裡的守衛影響。

全部離線：成功路徑測試以測試替身強制走 deterministic 離線管線（`live=False, use_llm=False`），
不觸網、不需要 LLM 憑證；403／400 測試直接 mock 掉 `_prepare_run` 與 `run`，用呼叫次數證明
管線（RunManager／collector／Bedrock）完全沒有被觸發。
"""

from __future__ import annotations

import base64
import io
import json
import os
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

import lambda_handler as lambda_module
from src.orchestrator import run as real_run
from src.run_manager import RUN_MODE_FORMAL, RUN_MODE_TEST


def _get(path: str, query: dict | None = None) -> dict:
    return {
        "rawPath": path,
        "requestContext": {"http": {"method": "GET", "path": path}},
        "queryStringParameters": query or {},
    }


def _post_form(body: str, path: str = "/") -> dict:
    return {
        "rawPath": path,
        "requestContext": {"http": {"method": "POST", "path": path}},
        "headers": {"content-type": "application/x-www-form-urlencoded"},
        "body": body,
    }


def _post_json(payload: dict, path: str = "/") -> dict:
    return {
        "rawPath": path,
        "requestContext": {"http": {"method": "POST", "path": path}},
        "headers": {"content-type": "application/json"},
        "body": json.dumps(payload, ensure_ascii=False),
    }


def _offline_pipeline(coin, question, output_dir, **kwargs):
    """測試替身：不論呼叫端傳入什麼 live／use_llm，一律強制走 deterministic 離線管線。

    用來證明「守衛放行之後，既有成功路徑沒有被破壞」，同時不觸網、不需要 LLM 憑證。
    """
    kwargs["live"] = False
    kwargs["use_llm"] = False
    return real_run(coin, question, output_dir, **kwargs)


class HomePageTestOnlyTests(unittest.TestCase):
    """首頁不得提供 formal 可選項，且必須清楚標示公開展示只提供 test mode。"""

    def test_home_page_has_no_formal_selectable_option(self):
        response = lambda_module.handler(_get("/"), None)
        body = response["body"]

        self.assertEqual(response["statusCode"], 200)
        self.assertNotIn("<option value='formal'>", body)
        self.assertNotIn('<option value="formal">', body)
        self.assertNotIn(">Formal<", body)
        # 執行性質下拉選單本身應已移除，只留下固定 hidden field。
        self.assertNotIn("<select name='mode'>", body)

    def test_home_page_states_test_only_demo(self):
        body = lambda_module.handler(_get("/"), None)["body"]

        self.assertIn("test mode", body)
        self.assertIn("test-only", body)

    def test_home_page_still_submits_a_fixed_test_mode(self):
        body = lambda_module.handler(_get("/"), None)["body"]

        self.assertIn("name='mode' value='test'", body)

    def test_home_page_keeps_query_and_run_button(self):
        """需求 1：首頁仍保留 query／coin 與執行按鈕，只是不再有 formal 選項。"""
        body = lambda_module.handler(_get("/"), None)["body"]

        self.assertIn("name='coin'", body)
        self.assertIn("name='question'", body)
        self.assertIn("開始分析", body)


class FormalModeRejectionTests(unittest.TestCase):
    """mode=formal 與正式重跑旗標必須在管線啟動前被 403 擋下。"""

    def test_form_formal_mode_is_rejected_before_the_pipeline_runs(self):
        with patch.object(lambda_module, "_prepare_run") as mock_prepare, \
             patch.object(lambda_module, "run") as mock_run:
            response = lambda_module.handler(
                _post_form(f"coin=ETH&question=Q&mode={RUN_MODE_FORMAL}"), None)

        self.assertEqual(response["statusCode"], 403)
        mock_prepare.assert_not_called()
        mock_run.assert_not_called()

    def test_form_formal_mode_is_case_insensitive(self):
        with patch.object(lambda_module, "_prepare_run") as mock_prepare, \
             patch.object(lambda_module, "run") as mock_run:
            response = lambda_module.handler(
                _post_form("coin=ETH&question=Q&mode=FORMAL"), None)

        self.assertEqual(response["statusCode"], 403)
        mock_prepare.assert_not_called()
        mock_run.assert_not_called()

    def test_json_formal_mode_is_rejected_before_the_pipeline_runs(self):
        with patch.object(lambda_module, "_prepare_run") as mock_prepare, \
             patch.object(lambda_module, "run") as mock_run:
            response = lambda_module.handler(
                _post_json({"coin": "ETH", "question": "Q", "mode": RUN_MODE_FORMAL}), None)

        self.assertEqual(response["statusCode"], 403)
        mock_prepare.assert_not_called()
        mock_run.assert_not_called()

    def test_json_authorized_rerun_flags_are_rejected(self):
        with patch.object(lambda_module, "_prepare_run") as mock_prepare, \
             patch.object(lambda_module, "run") as mock_run:
            response = lambda_module.handler(
                _post_json({
                    "coin": "ETH", "question": "Q", "mode": RUN_MODE_TEST,
                    "authorized_rerun": True, "rerun_of": "RUN-EXISTING", "rerun_reason": "demo",
                }), None)

        self.assertEqual(response["statusCode"], 403)
        mock_prepare.assert_not_called()
        mock_run.assert_not_called()

    def test_form_generic_rerun_flag_is_rejected(self):
        with patch.object(lambda_module, "_prepare_run") as mock_prepare, \
             patch.object(lambda_module, "run") as mock_run:
            response = lambda_module.handler(
                _post_form("coin=ETH&question=Q&mode=test&rerun=1"), None)

        self.assertEqual(response["statusCode"], 403)
        mock_prepare.assert_not_called()
        mock_run.assert_not_called()

    def test_rerun_flag_alone_is_rejected_even_without_formal_mode(self):
        """rerun 類旗標本身就是拒絕條件，不需要同時帶 mode=formal。"""
        with patch.object(lambda_module, "_prepare_run") as mock_prepare, \
             patch.object(lambda_module, "run") as mock_run:
            response = lambda_module.handler(
                _post_json({"coin": "ETH", "question": "Q", "rerun_of": "RUN-EXISTING"}), None)

        self.assertEqual(response["statusCode"], 403)
        mock_prepare.assert_not_called()
        mock_run.assert_not_called()

    def test_rejection_body_has_no_sensitive_detail(self):
        """需求 6：錯誤回應不可含 stack trace、AWS 帳號、Function URL、token 或原始 exception。"""
        response = lambda_module.handler(
            _post_json({"coin": "ETH", "question": "Q", "mode": RUN_MODE_FORMAL}), None)
        body = response["body"]

        for forbidden in ("Traceback", "arn:aws", "lambda-url", "Exception",
                          "boto3", ".on.aws", "AccessKey"):
            self.assertNotIn(forbidden, body)


class UnknownModeTests(unittest.TestCase):
    """其他未知 mode 回 400，同樣不得觸發管線。"""

    def test_unknown_mode_via_json_is_rejected_with_400(self):
        with patch.object(lambda_module, "_prepare_run") as mock_prepare, \
             patch.object(lambda_module, "run") as mock_run:
            response = lambda_module.handler(
                _post_json({"coin": "ETH", "question": "Q", "mode": "turbo"}), None)

        self.assertEqual(response["statusCode"], 400)
        mock_prepare.assert_not_called()
        mock_run.assert_not_called()

    def test_unknown_mode_via_form_is_rejected_with_400(self):
        with patch.object(lambda_module, "_prepare_run") as mock_prepare, \
             patch.object(lambda_module, "run") as mock_run:
            response = lambda_module.handler(
                _post_form("coin=ETH&question=Q&mode=turbo"), None)

        self.assertEqual(response["statusCode"], 400)
        mock_prepare.assert_not_called()
        mock_run.assert_not_called()

    def test_unknown_mode_response_escapes_user_input(self):
        with patch.object(lambda_module, "_prepare_run"), \
             patch.object(lambda_module, "run"):
            response = lambda_module.handler(
                _post_json({"coin": "ETH", "question": "Q",
                            "mode": "<script>alert(1)</script>"}), None)

        self.assertEqual(response["statusCode"], 400)
        self.assertNotIn("<script>", response["body"])


class SuccessPathUnaffectedTests(unittest.TestCase):
    """未帶 mode 與 mode=test 仍走既有成功路徑（需求 2 的維持既有行為分支）。

    每個測試都用 `patch.object(lambda_module, "_LATEST_RUN_DIR", ...)` 包住呼叫：
    `handler()` 成功時會把這個模組層級全域變數指向本次的 tempdir，而 tempdir 會在
    `with tempfile.TemporaryDirectory()` 結束時被刪除。若不還原，這個全域會在測試結束後
    繼續指向一個已經不存在的路徑，讓之後任何沒有自己 patch 這個變數的測試
    （例如既有的 `tests/test_lambda_download_routes.py`）意外讀到失效路徑而出錯。
    `patch.object` 離開 `with` 區塊時一律還原成進入前的值，因此可以安全地讓
    `handler()` 在區塊內自由賦值。
    """

    def test_omitted_mode_still_succeeds_via_json(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"ARTIFACT_ROOT": directory}, clear=False), \
                 patch.object(lambda_module, "run", side_effect=_offline_pipeline), \
                 patch.object(lambda_module, "_LATEST_RUN_DIR", None):
                response = lambda_module.handler(
                    _post_json({"coin": "ETH", "question": "Q"}), None)

        self.assertEqual(response["statusCode"], 200)
        payload = json.loads(response["body"])
        self.assertEqual(payload["run_mode"], RUN_MODE_TEST)

    def test_explicit_test_mode_still_succeeds_via_form(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"ARTIFACT_ROOT": directory}, clear=False), \
                 patch.object(lambda_module, "run", side_effect=_offline_pipeline), \
                 patch.object(lambda_module, "_LATEST_RUN_DIR", None):
                response = lambda_module.handler(
                    _post_form("coin=ETH&question=Q&mode=test"), None)

        self.assertEqual(response["statusCode"], 200)
        self.assertIn("分析完成", response["body"])

    def test_explicit_test_mode_uppercase_still_succeeds(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"ARTIFACT_ROOT": directory}, clear=False), \
                 patch.object(lambda_module, "run", side_effect=_offline_pipeline), \
                 patch.object(lambda_module, "_LATEST_RUN_DIR", None):
                response = lambda_module.handler(
                    _post_json({"coin": "ETH", "question": "Q", "mode": "TEST"}), None)

        self.assertEqual(response["statusCode"], 200)
        payload = json.loads(response["body"])
        self.assertEqual(payload["run_mode"], RUN_MODE_TEST)


class DownloadArtifactRegressionTests(unittest.TestCase):
    """守衛只掛在首頁 POST 流程；既有的 /download、/artifact 行為不可回歸。"""

    @classmethod
    def setUpClass(cls):
        cls._directory = tempfile.TemporaryDirectory()
        cls.run_dir = Path(cls._directory.name) / "runs" / "RUN-E1-GUARDRAIL-TEST"
        real_run("ETH", "E1 test-only 守衛回歸測試", cls.run_dir, live=False, use_llm=False)

    @classmethod
    def tearDownClass(cls):
        cls._directory.cleanup()

    def test_download_required_scope_still_returns_a_zip(self):
        with patch.object(lambda_module, "_LATEST_RUN_DIR", self.run_dir):
            response = lambda_module.handler(_get("/download", {"scope": "required"}), None)

        self.assertEqual(response["statusCode"], 200)
        self.assertEqual(response["headers"]["content-type"], "application/zip")
        self.assertTrue(response["isBase64Encoded"])
        payload = base64.b64decode(response["body"])
        with zipfile.ZipFile(io.BytesIO(payload)) as archive:
            self.assertEqual(sorted(archive.namelist()),
                             ["evidence.json", "execution_log.json", "report.md"])

    def test_artifact_route_still_serves_a_single_file(self):
        with patch.object(lambda_module, "_LATEST_RUN_DIR", self.run_dir):
            response = lambda_module.handler(_get("/artifact", {"path": "report.md"}), None)

        self.assertEqual(response["statusCode"], 200)
        self.assertIn("text/plain", response["headers"]["content-type"])

    def test_home_page_get_is_unaffected_by_the_guard(self):
        """GET 首頁完全不經過 `_public_mode_guard`（守衛只掛在 POST），確認不會誤擋。"""
        response = lambda_module.handler(_get("/"), None)

        self.assertEqual(response["statusCode"], 200)


if __name__ == "__main__":  # pragma: no cover
    unittest.main()
