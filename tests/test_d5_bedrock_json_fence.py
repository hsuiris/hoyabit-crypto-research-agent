"""D5：Bedrock 回應的 markdown code fence 容錯。

這組測試對應一個在雲端實際發生的故障，不是假想情境：

`amazon.nova-lite-v1:0`（us-west-2）在 Converse 下會把 JSON 包成 ```json ... ```。
`json.loads()` 因此拋 `Expecting value: line 1 column 1 (char 0)`，且既有的單次重試無效
——模型行為一致，重試只會拿到同一個 fence。結果是 planner、analyst 與 critic 三個階段
全部降級成 deterministic fallback，而報告照樣產出，只有 stage status 記著
`fallback:ValueError`。

因此這裡守住兩件事：帶 fence 的回應要能被解析，沒有 fence 的既有行為不能改變。

全程 mock，不呼叫真實 Bedrock。
"""

from __future__ import annotations

import json
import unittest
from unittest import mock

from src.llm import strip_json_fence


def bedrock_response(text: str) -> dict:
    """組出 Converse 的回應形狀。"""
    return {"output": {"message": {"content": [{"text": text}]}}}


SIMPLE_SCHEMA = {
    "type": "object",
    "properties": {"summary": {"type": "string"}},
    "required": ["summary"],
}


class StripJsonFenceTests(unittest.TestCase):
    """純函式行為。沒有 fence 時必須是不改變內容的 no-op（除了兩端空白）。"""

    def test_strips_json_labelled_fence(self):
        # 這是雲端實際觀測到的形狀。
        raw = '```json\n{\n  "summary": "ok"\n}\n```'
        self.assertEqual(json.loads(strip_json_fence(raw)), {"summary": "ok"})

    def test_strips_bare_fence(self):
        raw = '```\n{"summary": "ok"}\n```'
        self.assertEqual(json.loads(strip_json_fence(raw)), {"summary": "ok"})

    def test_plain_json_is_unchanged_apart_from_whitespace(self):
        raw = '  {"summary": "ok"}  '
        self.assertEqual(strip_json_fence(raw), '{"summary": "ok"}')

    def test_does_not_touch_fences_inside_string_values(self):
        # 只剝最外層。值裡面的 ``` 必須原樣保留，否則會改寫模型的內容。
        payload = {"summary": "see ```code``` here"}
        raw = "```json\n" + json.dumps(payload) + "\n```"
        self.assertEqual(json.loads(strip_json_fence(raw)), payload)

    def test_single_line_fence_without_content_is_left_for_json_to_reject(self):
        # 沒有內容可取時不要假裝成功，讓呼叫端的 JSON 解析報出可診斷的錯誤。
        self.assertEqual(strip_json_fence("```"), "```")
        with self.assertRaises(json.JSONDecodeError):
            json.loads(strip_json_fence("```"))

    def test_handles_trailing_newline_after_closing_fence(self):
        raw = '```json\n{"summary": "ok"}\n```\n\n'
        self.assertEqual(json.loads(strip_json_fence(raw)), {"summary": "ok"})

    def test_empty_input_returns_empty(self):
        self.assertEqual(strip_json_fence("   "), "")


class BedrockGenerateWithFenceTests(unittest.TestCase):
    """整合到 _generate_bedrock：帶 fence 的回應要能一次成功，不該進重試。"""

    def _run(self, texts):
        """以依序回傳 texts 的假 client 執行 _generate_bedrock。"""
        import src.llm as llm

        calls = []

        class _FakeClient:
            def converse(self, **kwargs):
                calls.append(kwargs)
                return bedrock_response(texts[len(calls) - 1])

        fake_boto3 = mock.Mock()
        fake_boto3.client.return_value = _FakeClient()
        with mock.patch.object(llm, "boto3", fake_boto3), mock.patch.object(
            llm, "BotoConfig", None
        ), mock.patch.dict(
            "os.environ",
            {"BEDROCK_MODEL_ID": "amazon.nova-lite-v1:0", "BEDROCK_REGION": "us-west-2"},
        ):
            result = llm._generate_bedrock("prompt", SIMPLE_SCHEMA, "Analysis", 30)
        return result, calls

    def test_fenced_response_parses_on_first_attempt(self):
        result, calls = self._run(['```json\n{"summary": "ok"}\n```'])
        self.assertEqual(result["summary"], "ok")
        # 關鍵：不該需要重試。修復前這裡會是 2 次且最終拋 ValueError。
        self.assertEqual(len(calls), 1)

    def test_unfenced_response_still_parses_on_first_attempt(self):
        result, calls = self._run(['{"summary": "ok"}'])
        self.assertEqual(result["summary"], "ok")
        self.assertEqual(len(calls), 1)

    def test_prompt_tells_the_model_not_to_use_code_fences(self):
        # prompt 是請求不是保證，但仍應明說，以降低發生率。
        _, calls = self._run(['{"summary": "ok"}'])
        sent = calls[0]["messages"][0]["content"][0]["text"]
        self.assertIn("do not wrap it in markdown", sent.lower())

    def test_genuinely_invalid_response_still_raises_after_one_retry(self):
        # fence 容錯不得掩蓋真正的格式錯誤。
        with self.assertRaises(ValueError) as caught:
            self._run(["not json at all", "still not json"])
        self.assertIn("after one retry", str(caught.exception))

    def test_missing_required_field_still_raises(self):
        with self.assertRaises(ValueError):
            self._run(['```json\n{"other": 1}\n```', '```json\n{"other": 1}\n```'])


if __name__ == "__main__":
    unittest.main()


class RunSummaryLogTests(unittest.TestCase):
    """D5：CloudWatch 必須能從 log 回溯到 run_id，且不得寫入題目原文或憑證。

    第一次雲端驗收時 CloudWatch 只有 Lambda 自己的 START／END／REPORT 與冷啟動的
    `[sdk]`，完全找不到 run_id——降級的報告與正常的報告在 log 上無法區分。
    """

    def _summary(self):
        import lambda_handler as h

        class _Record:
            run_id = "RUN-20260801T091424Z-ETH-505a53ea"
            mode = "test"
            status = "COMPLETED_DEGRADED"

        manifest = {
            "question_hash": "abc123",
            "duration_ms": 17695.1,
            "validation": {"citation_gate_status": "PASS_WITH_WARNINGS"},
            "stage_providers": {
                "planner": {"provider": "bedrock", "model": "amazon.nova-lite-v1:0"},
                "analyst": {"provider": "bedrock", "status": "success"},
                "critic": {"provider": "bedrock", "status": "success"},
            },
        }
        log = {"degradation_reasons": ["collector_fallback:derivatives"]}
        return h._run_summary(_Record(), manifest, log)

    def test_summary_contains_run_id_and_gate_and_stages(self):
        summary = self._summary()
        self.assertEqual(summary["run_id"], "RUN-20260801T091424Z-ETH-505a53ea")
        self.assertEqual(summary["citation_gate"], "PASS_WITH_WARNINGS")
        self.assertEqual(summary["stages"]["analyst"], "success")
        # planner 沒有 status 欄位時退回 provider，才能看出它走的是模型還是 deterministic。
        self.assertEqual(summary["stages"]["planner"], "bedrock")
        self.assertEqual(summary["degradation_reasons"], ["collector_fallback:derivatives"])

    def test_summary_records_question_hash_not_the_question_text(self):
        # log 的保留期比單次請求長得多，不該把使用者輸入寫進去。
        summary = self._summary()
        self.assertEqual(summary["question_hash"], "abc123")
        self.assertNotIn("question", summary)

    def test_summary_is_json_serialisable_and_single_line(self):
        import lambda_handler as h

        with mock.patch("builtins.print") as printer:
            h.log_run_summary("run", self._summary())
        printed = printer.call_args.args[0]
        self.assertTrue(printed.startswith("[run] "))
        self.assertNotIn("\n", printed)
        json.loads(printed[len("[run] "):])
