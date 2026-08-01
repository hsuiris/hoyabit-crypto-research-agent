"""D4：Lambda 執行環境的 AWS SDK 能力回報。

這些測試守住兩件事：

1. `sdk_capability()` 是純觀測函式——任何情況都回報結構化結果，不拋例外，
   因為它是為了讓雲端的靜默降級可被發現，本身不該成為新的失敗來源。
2. 專案的本機路徑不依賴 boto3。boto3 只出現在部署包與 AWS 執行環境，
   屬於部署目標環境的一部分，不是專案原始碼的相依（見 .kiro/steering/tech.md）。

全部離線，不呼叫 AWS，也不需要憑證。

實作註記：凡是需要「模組在不同環境下被重新 import」的情境，一律走 subprocess，
不在本 process 內 reload。原因是實測踩到的問題：`sys.modules.pop("src.llm")` 後重新
import 會產生新的模組物件，而其他測試檔在模組層級 `from src.llm import ...` 綁定的是
舊函式，其 globals 指向舊模組——於是那些測試對 `src.llm.urlopen` 的 patch 失效，
真的發出網路請求。subprocess 沒有這個問題。
"""

from __future__ import annotations

import builtins
import json
import os
import subprocess
import sys
import unittest
from pathlib import Path
from unittest import mock

import lambda_handler

PROJECT_ROOT = Path(__file__).resolve().parent.parent

# 有些測試需要真的 botocore 才能 stub 它的 session。在完全沒有 AWS SDK 的環境
# （這正是 D4 要證明仍可運作的環境）必須 skip 而不是 error，否則「無 boto3 時完整
# 測試套件仍通過」這條驗收就無法成立。
try:  # pragma: no cover - 取決於環境是否安裝 AWS SDK
    import botocore  # noqa: F401
    import botocore.session  # noqa: F401

    HAS_BOTOCORE = True
except ImportError:  # pragma: no cover
    HAS_BOTOCORE = False

# 讓 subprocess 內的 import boto3 失敗。Python 的既有行為：sys.modules[name] = None
# 會使該名稱的 import 拋出 ImportError，因此不需要自訂 meta path finder。
BLOCK_BOTO3 = "import sys; sys.modules['boto3'] = None; sys.modules['botocore'] = None\n"
BLOCK_BOTO3_ONLY = "import sys; sys.modules['boto3'] = None\n"


def run_isolated(script: str, env_extra: dict | None = None) -> subprocess.CompletedProcess:
    """在乾淨的子行程執行 script，回傳結果供斷言。"""
    env = dict(os.environ)
    env["PYTHONPATH"] = str(PROJECT_ROOT)
    env["PYTHONWARNINGS"] = "ignore"
    env.pop("AWS_LAMBDA_FUNCTION_NAME", None)
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, "-c", script],
        cwd=str(PROJECT_ROOT), env=env,
        capture_output=True, text=True, timeout=120,
    )


class SdkCapabilityShapeTests(unittest.TestCase):
    """回傳形狀必須穩定，因為 D5 的驗收與 JSON 回應都依賴這些 key。"""

    def test_returns_the_three_documented_keys(self):
        capability = lambda_handler.sdk_capability()
        self.assertEqual(set(capability), {"botocore_version", "converse_available", "detail"})

    def test_converse_available_is_a_bool(self):
        self.assertIsInstance(lambda_handler.sdk_capability()["converse_available"], bool)

    def test_result_is_json_serialisable(self):
        # 它會被塞進 Function URL 的 JSON 回應，不可序列化就會讓整個回應失敗。
        json.dumps(lambda_handler.sdk_capability(), ensure_ascii=False)

    def test_module_level_constant_matches_a_fresh_call(self):
        self.assertEqual(lambda_handler.SDK_CAPABILITY, lambda_handler.sdk_capability())


class SdkCapabilityDegradedEnvironmentTests(unittest.TestCase):
    """SDK 缺失或過舊時，必須回報原因而不是拋例外。

    這些用 mock.patch 就足夠：它們不重新 import 任何模組，因此不會污染其他測試。
    """

    def test_missing_botocore_is_reported_not_raised(self):
        real_import = builtins.__import__

        def fake_import(name, *args, **kwargs):
            if name == "botocore" or name.startswith("botocore."):
                raise ImportError("No module named 'botocore'")
            return real_import(name, *args, **kwargs)

        with mock.patch.object(builtins, "__import__", side_effect=fake_import):
            capability = lambda_handler.sdk_capability()

        self.assertFalse(capability["converse_available"])
        self.assertIsNone(capability["botocore_version"])
        self.assertIn("botocore unavailable", capability["detail"])

    @unittest.skipUnless(HAS_BOTOCORE, "需要已安裝的 botocore 才能 stub session")
    def test_unknown_service_is_reported_not_raised(self):
        # 模擬真實的失敗模式：SDK 太舊而不認得 bedrock-runtime。
        import botocore.session

        class _StubSession:
            def get_service_model(self, name):
                raise RuntimeError(f"Unknown service: '{name}'")

        with mock.patch.object(botocore.session, "get_session", return_value=_StubSession()):
            capability = lambda_handler.sdk_capability()

        self.assertFalse(capability["converse_available"])
        self.assertIn("Unknown service", capability["detail"])
        # 版本仍應被讀到，因為 botocore 本身 import 成功了。
        self.assertIsNotNone(capability["botocore_version"])

    @unittest.skipUnless(HAS_BOTOCORE, "需要已安裝的 botocore 才能 stub session")
    def test_service_model_without_converse_is_reported(self):
        import botocore.session

        class _StubModel:
            operation_names = ["InvokeModel"]  # 舊 SDK 有 InvokeModel 但沒有 Converse

        class _StubSession:
            def get_service_model(self, name):
                return _StubModel()

        with mock.patch.object(botocore.session, "get_session", return_value=_StubSession()):
            capability = lambda_handler.sdk_capability()

        self.assertFalse(capability["converse_available"])
        self.assertIn("no Converse operation", capability["detail"])


class ColdStartLogTests(unittest.TestCase):
    """`[sdk]` log 只該出現在 Lambda，不該污染本機測試與 CLI 輸出。"""

    def test_no_log_line_outside_lambda(self):
        result = run_isolated("import lambda_handler\nprint('IMPORTED')\n")
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("IMPORTED", result.stdout)
        self.assertNotIn("[sdk]", result.stdout)

    def test_log_line_inside_lambda(self):
        result = run_isolated(
            "import lambda_handler\n",
            {"AWS_LAMBDA_FUNCTION_NAME": "hoyabit-market-research-agent"},
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("[sdk]", result.stdout)
        # log 內容必須是可解析的 JSON，方便在 CloudWatch 上直接篩選。
        payload = json.loads(result.stdout.split("[sdk]", 1)[1].strip().splitlines()[0])
        self.assertIn("converse_available", payload)

    def test_log_line_contains_no_credentials(self):
        result = run_isolated(
            "import lambda_handler\n",
            {"AWS_LAMBDA_FUNCTION_NAME": "hoyabit-market-research-agent"},
        )
        line = result.stdout.split("[sdk]", 1)[1]
        for forbidden in ("aws_access_key", "AKIA", "SessionToken", "SecretAccessKey"):
            self.assertNotIn(forbidden, line)


class LocalPathDoesNotRequireBoto3Tests(unittest.TestCase):
    """證明 D4 的 SDK 打包決策沒有把 boto3 變成專案相依。"""

    def test_src_llm_imports_without_boto3(self):
        result = run_isolated(
            BLOCK_BOTO3_ONLY
            + "import src.llm\n"
            + "assert src.llm.boto3 is None, 'expected optional import to yield None'\n"
            + "print('SRC_LLM_OK')\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("SRC_LLM_OK", result.stdout)

    def test_lambda_handler_imports_without_any_aws_sdk(self):
        # 連 botocore 都不在時，sdk_capability() 仍須回報而非讓 import 失敗。
        result = run_isolated(
            BLOCK_BOTO3
            + "import lambda_handler as h\n"
            + "cap = h.SDK_CAPABILITY\n"
            + "assert cap['converse_available'] is False, cap\n"
            + "assert cap['detail'] and 'botocore' in cap['detail'], cap\n"
            + "print('HANDLER_OK')\n"
        )
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("HANDLER_OK", result.stdout)

    def test_offline_end_to_end_without_boto3(self):
        # D4 的核心驗收：沒有 AWS SDK 時，離線端到端仍產出六項提交物。
        script = (
            BLOCK_BOTO3
            + "import tempfile, json\n"
            + "from pathlib import Path\n"
            + "from src.orchestrator import run\n"
            + "from src.schemas import ARTIFACT_FILENAMES\n"
            + "with tempfile.TemporaryDirectory() as tmp:\n"
            + "    out = Path(tmp)\n"
            + "    run('ETH', 'D4 無 SDK 離線驗收', out, live=False, use_llm=False)\n"
            + "    missing = [n for n in ARTIFACT_FILENAMES.values() if not (out / n).is_file()]\n"
            + "    assert not missing, missing\n"
            + "    print('ARTIFACTS_OK', len(ARTIFACT_FILENAMES))\n"
        )
        result = run_isolated(script)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("ARTIFACTS_OK", result.stdout)

    def test_bedrock_without_boto3_raises_actionable_error(self):
        # 沒有 boto3 時必須是可診斷的錯誤，而不是 AttributeError 之類的意外。
        # 這裡只 patch 屬性、不重新 import，因此對其他測試沒有副作用。
        import src.llm as llm

        with mock.patch.object(llm, "boto3", None), mock.patch.dict(
            "os.environ", {"BEDROCK_MODEL_ID": "amazon.nova-lite-v1:0"}
        ):
            with self.assertRaises(RuntimeError) as caught:
                llm._generate_bedrock("prompt", {"required": []}, "Analysis", 10)
        self.assertIn("boto3", str(caught.exception))


if __name__ == "__main__":
    unittest.main()
