"""Bedrock 輸出上限（maxTokens）的截斷偵測與雲端可設定性。

這組測試對應一個具體的、可預期的故障，不是假想情境：

`BEDROCK_MAX_TOKENS` 原本寫死 2048，而 `aws/template.yaml` 的 Lambda 環境變數裡沒有這個
變數，`.env` 也不會進部署包 —— 因此雲端只能吃程式內建預設值，沒有任何調整途徑。

同時 E2 把每筆 Evidence 的語意評估併進**同一次**分析回應。實測的雲端 analyst 輸出本來就
約 1,000 字中文（約 1,100–1,500 tokens，見
`demo-fixtures/competition-ready/live-success/`），再加 13 筆評估後很容易超過 2048。

超過時 Converse **不會報錯**：它回 `stopReason=max_tokens` 並把 JSON 截在一半。於是
`json.loads()` 拋出的訊息與已經修好的 markdown fence bug 一模一樣，而既有的重試會把整份
JSON Schema 附在 prompt 後面 —— 讓輸出要求變得更長，第二次一樣被截。結果是整份分析降級
成離線推理，雲端只留下一個看不出原因的 `fallback:ValueError`。

因此這裡守住四件事：
  1. 截斷必須被辨識出來，錯誤訊息要指名 stopReason 與當時的上限。
  2. 截斷的重試要求「更短」，且不得附上 schema；格式錯誤的重試仍要附上 schema。
  3. 沒有 `stopReason` 的回應行為完全不變（既有測試的假回應都沒有這個鍵）。
  4. template 與 deploy.sh 真的把上限傳到 Lambda，否則第 1、2 點在雲端無從生效。

全程 mock，不呼叫真實 Bedrock。
"""

from __future__ import annotations

import json
import re
import unittest
from pathlib import Path
from unittest import mock

import src.llm as llm
from src.llm import (BEDROCK_STOP_REASON_MAX_TOKENS, DEFAULT_BEDROCK_MAX_TOKENS,
                     _bedrock_stop_reason)

TEMPLATE = Path(__file__).resolve().parent.parent / "aws" / "template.yaml"
DEPLOY_SH = Path(__file__).resolve().parent.parent / "aws" / "deploy.sh"

SIMPLE_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}

VALID = '{"summary": "ok"}'
# 被截斷的 JSON：結構未收尾，`json.loads` 必然失敗。
TRUNCATED = '{"summary": "很長的敘述被截斷在這裡'


def response(text: str, stop_reason: str | None = None) -> dict:
    """組出 Converse 的回應形狀；`stop_reason=None` 代表回應裡沒有這個鍵。"""
    payload = {"output": {"message": {"content": [{"text": text}]}}}
    if stop_reason is not None:
        payload["stopReason"] = stop_reason
    return payload


def run_bedrock(responses, environment=None):
    """以依序回傳 `responses` 的假 client 執行 `_generate_bedrock`，回傳 (結果, 呼叫紀錄)。"""
    calls = []

    class _FakeClient:
        def converse(self, **kwargs):
            calls.append(kwargs)
            return responses[len(calls) - 1]

    fake_boto3 = mock.Mock()
    fake_boto3.client.return_value = _FakeClient()
    values = {"BEDROCK_MODEL_ID": "amazon.nova-lite-v1:0", "BEDROCK_REGION": "us-west-2"}
    values.update(environment or {})
    with mock.patch.object(llm, "boto3", fake_boto3), \
            mock.patch.object(llm, "BotoConfig", None), \
            mock.patch.dict("os.environ", values, clear=True):
        result = llm._generate_bedrock("prompt", SIMPLE_SCHEMA, "market_analysis", 30)
    return result, calls


def sent_prompt(call) -> str:
    return call["messages"][0]["content"][0]["text"]


class StopReasonParsingTests(unittest.TestCase):
    """純函式：缺鍵不得被誤判成截斷，否則所有既有假回應都會壞掉。"""

    def test_missing_stop_reason_is_empty(self):
        self.assertEqual(_bedrock_stop_reason({}), "")

    def test_null_stop_reason_is_empty(self):
        self.assertEqual(_bedrock_stop_reason({"stopReason": None}), "")

    def test_normal_completion_is_not_the_truncation_marker(self):
        self.assertNotEqual(_bedrock_stop_reason({"stopReason": "end_turn"}),
                            BEDROCK_STOP_REASON_MAX_TOKENS)

    def test_case_and_whitespace_are_normalised(self):
        self.assertEqual(_bedrock_stop_reason({"stopReason": " MAX_TOKENS "}),
                         BEDROCK_STOP_REASON_MAX_TOKENS)


class OutputCapDefaultTests(unittest.TestCase):
    def test_default_cap_is_raised_above_the_old_2048(self):
        # 2048 對現在的分析回應已經不夠（E2 的逐筆評估併在同一次回應裡）。
        self.assertGreater(DEFAULT_BEDROCK_MAX_TOKENS, 2048)

    def test_default_cap_fits_inside_the_nova_documented_ceiling(self):
        # amazon.nova-lite-v1:0 的文件輸出上限是 5,000 tokens；超過會被 Converse 拒絕。
        self.assertLessEqual(DEFAULT_BEDROCK_MAX_TOKENS, 5000)

    def test_default_is_used_when_the_environment_does_not_set_one(self):
        _, calls = run_bedrock([response(VALID)])
        self.assertEqual(calls[0]["inferenceConfig"]["maxTokens"], DEFAULT_BEDROCK_MAX_TOKENS)

    def test_environment_override_still_wins(self):
        _, calls = run_bedrock([response(VALID)], {"BEDROCK_MAX_TOKENS": "4321"})
        self.assertEqual(calls[0]["inferenceConfig"]["maxTokens"], 4321)


class TruncationDetectionTests(unittest.TestCase):
    def test_truncated_response_names_the_cap_and_the_stop_reason(self):
        # 修復前這裡只會是 json.loads 的 "Expecting ..."，與 markdown fence bug 無法區分。
        with self.assertRaises(ValueError) as caught:
            run_bedrock([response(TRUNCATED, "max_tokens"), response(TRUNCATED, "max_tokens")],
                        {"BEDROCK_MAX_TOKENS": "2048"})
        message = str(caught.exception)
        self.assertIn("2048", message)
        self.assertIn(BEDROCK_STOP_REASON_MAX_TOKENS, message)
        self.assertIn("BEDROCK_MAX_TOKENS", message)

    def test_truncation_is_detected_even_when_the_partial_json_would_parse(self):
        # 模型可能剛好在一個合法 JSON 之後被截斷。stopReason 才是權威訊號，不是能否解析。
        with self.assertRaises(ValueError) as caught:
            run_bedrock([response(VALID, "max_tokens"), response(VALID, "max_tokens")])
        self.assertIn(BEDROCK_STOP_REASON_MAX_TOKENS, str(caught.exception))

    def test_truncated_then_shorter_valid_response_succeeds(self):
        result, calls = run_bedrock([response(TRUNCATED, "max_tokens"), response(VALID, "end_turn")])
        self.assertEqual(result["summary"], "ok")
        self.assertEqual(len(calls), 2)


class RetryStrategyTests(unittest.TestCase):
    """兩種失敗需要相反的處置：太長要更短，寫錯要更嚴格。"""

    def test_truncation_retry_asks_for_a_shorter_response(self):
        _, calls = run_bedrock([response(TRUNCATED, "max_tokens"), response(VALID, "end_turn")])
        retry = sent_prompt(calls[1])
        self.assertIn("cut off", retry.lower())
        self.assertIn("shorter", retry.lower())

    def test_truncation_retry_does_not_append_the_schema(self):
        # 這是本次修復的重點：附上 schema 會讓輸出要求更長，第二次一樣被截，白花一次呼叫。
        _, calls = run_bedrock([response(TRUNCATED, "max_tokens"), response(VALID, "end_turn")])
        self.assertNotIn("JSON Schema", sent_prompt(calls[1]))
        self.assertNotIn('"properties"', sent_prompt(calls[1]))

    def test_truncation_retry_permits_dropping_the_optional_fields(self):
        # evidence_assessments 與 claims 都不在 ANALYSIS_SCHEMA["required"] 裡，捨棄它們
        # 只會讓那兩層走 deterministic fallback，敘事仍由模型撰寫 —— 遠優於整份降級。
        _, calls = run_bedrock([response(TRUNCATED, "max_tokens"), response(VALID, "end_turn")])
        retry = sent_prompt(calls[1])
        self.assertIn(llm.EVIDENCE_ASSESSMENT_FIELD, retry)
        self.assertIn(llm.CLAIM_PROPOSAL_FIELD, retry)

    def test_format_error_retry_still_appends_the_schema(self):
        # 既有行為不得因為新增截斷分支而改變。
        with self.assertRaises(ValueError):
            run_bedrock([response("not json at all"), response("still not json")])
        _, calls = run_bedrock([response("not json at all"), response(VALID)])
        retry = sent_prompt(calls[1])
        self.assertIn("previous response was invalid", retry)
        self.assertIn("JSON Schema", retry)


class NoStopReasonRegressionTests(unittest.TestCase):
    """既有測試的假回應都沒有 stopReason；那條路必須完全等同修改前。"""

    def test_valid_response_without_stop_reason_parses_on_first_attempt(self):
        result, calls = run_bedrock([response(VALID)])
        self.assertEqual(result["summary"], "ok")
        self.assertEqual(len(calls), 1)

    def test_fenced_response_without_stop_reason_still_parses(self):
        result, calls = run_bedrock([response('```json\n' + VALID + '\n```')])
        self.assertEqual(result["summary"], "ok")
        self.assertEqual(len(calls), 1)

    def test_invalid_response_without_stop_reason_still_raises_after_one_retry(self):
        with self.assertRaises(ValueError) as caught:
            run_bedrock([response("nope"), response("still nope")])
        self.assertIn("after one retry", str(caught.exception))


class DeploymentWiringTests(unittest.TestCase):
    """雲端可設定性。少了這一段，上面的偵測在 Lambda 上沒有調整餘地。"""

    @classmethod
    def setUpClass(cls):
        cls.template = TEMPLATE.read_text(encoding="utf-8")
        cls.deploy = DEPLOY_SH.read_text(encoding="utf-8")

    def test_template_exposes_the_cap_as_a_parameter(self):
        self.assertIn("BedrockMaxTokens:", self.template)

    def test_template_passes_the_cap_into_the_lambda_environment(self):
        # 這一行才是雲端能不能調整的關鍵：沒有它，.env 與 template 參數都影響不到 Lambda。
        self.assertIn("BEDROCK_MAX_TOKENS: !Ref BedrockMaxTokens", self.template)

    def test_template_cap_respects_the_configured_model_ceiling(self):
        match = re.search(r"BedrockMaxTokens:.*?MaxValue:\s*(\d+)", self.template, re.S)
        self.assertIsNotNone(match, "BedrockMaxTokens 應宣告 MaxValue")
        self.assertLessEqual(int(match.group(1)), 5000)

    def test_template_default_is_above_the_old_hardcoded_2048(self):
        match = re.search(r"BedrockMaxTokens:\s*\n\s*Type:\s*Number\s*\n\s*Default:\s*(\d+)",
                          self.template)
        self.assertIsNotNone(match, "BedrockMaxTokens 應宣告 Default")
        self.assertGreater(int(match.group(1)), 2048)

    def test_deploy_script_passes_the_parameter(self):
        self.assertIn("BedrockMaxTokens=", self.deploy)

    def test_deploy_script_validates_the_value(self):
        # 非數字要在打包前就攔下，不要讓 CloudFormation 部署到一半才拒絕。
        self.assertIn("--max-tokens", self.deploy)
        self.assertRegex(self.deploy, r'case "\$MAX_TOKENS" in')

    def test_deploy_script_does_not_read_the_runtime_variable_for_deploy_time(self):
        # 本機 .env 的 BEDROCK_MAX_TOKENS 是執行期設定，不該決定雲端部署什麼。
        self.assertIn("DEPLOY_BEDROCK_MAX_TOKENS", self.deploy)


if __name__ == "__main__":
    unittest.main()
