from __future__ import annotations

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import lambda_handler
from src import llm
from src.llm import (
    ANALYSIS_SCHEMA,
    CRITIC_SCHEMA,
    analyze_with_llm,
    configured_provider,
    critique_with_llm,
    generate_json_with_llm,
    llm_is_configured,
    llm_runtime_info,
)
from src.orchestrator import run


VALID_ANALYSIS = {
    "market_judgment": "ETH signals are mixed.",
    "confidence": 0.61,
    "facts": ["EV-MARKET-001 shows the latest market observation."],
    "inferences": ["Momentum is not decisive."],
    "conclusion": "Continue monitoring multiple signals.",
    "counter_evidence": ["News sentiment may reverse."],
    "observation_points": ["Watch volatility and volume."],
    "cited_evidence_ids": ["EV-MARKET-001"],
}

VALID_CRITIQUE = {
    "verdict": "concerns",
    "summary": "引用與反證已檢查。",
    "confidence_adjustment": -0.1,
    "findings": [{
        "severity": "low",
        "category": "confidence",
        "claim": "監測多方訊號",
        "issue": "信心應保守。",
        "evidence_id": "EV-MARKET-001",
    }],
}


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


def bedrock_response(payload: dict | str) -> dict:
    text = payload if isinstance(payload, str) else json.dumps(payload, ensure_ascii=False)
    return {"output": {"message": {"content": [{"text": text}]}}}


class BedrockAdapterTests(unittest.TestCase):
    def bedrock_environment(self, **overrides):
        values = {
            "LLM_PROVIDER": "bedrock",
            "BEDROCK_MODEL_ID": "amazon.nova-lite-v1:0",
            "AWS_REGION": "ap-northeast-1",
            "BEDROCK_MAX_TOKENS": "321",
            "BEDROCK_TEMPERATURE": "0.15",
        }
        values.update(overrides)
        return patch.dict(os.environ, values, clear=True)

    def test_configured_provider_selects_bedrock_and_reports_runtime_info(self):
        with self.bedrock_environment():
            self.assertEqual(configured_provider(), "bedrock")
            self.assertTrue(llm_is_configured())
            self.assertEqual(
                llm_runtime_info(),
                {
                    "provider": "bedrock",
                    "model": "amazon.nova-lite-v1:0",
                    "region": "ap-northeast-1",
                },
            )

    def test_bedrock_without_model_id_is_not_configured(self):
        with self.bedrock_environment(BEDROCK_MODEL_ID=""):
            self.assertFalse(llm_is_configured())
            with self.assertRaisesRegex(RuntimeError, "BEDROCK_MODEL_ID"):
                analyze_with_llm("ETH", "問題", [])

    @patch.object(llm, "boto3")
    def test_bedrock_generates_and_validates_json(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.converse.return_value = bedrock_response(VALID_ANALYSIS)
        with self.bedrock_environment():
            result = generate_json_with_llm("請產生 JSON", ANALYSIS_SCHEMA, "market_analysis", 17)

        self.assertEqual(result, VALID_ANALYSIS)
        client_args, client_kwargs = mock_boto3.client.call_args
        self.assertEqual(client_args, ("bedrock-runtime",))
        self.assertEqual(client_kwargs["region_name"], "ap-northeast-1")
        request = client.converse.call_args.kwargs
        self.assertEqual(request["modelId"], "amazon.nova-lite-v1:0")
        self.assertEqual(request["inferenceConfig"], {"maxTokens": 321, "temperature": 0.15})
        self.assertIn("JSON Schema", request["messages"][0]["content"][0]["text"])

    @patch.object(llm, "boto3")
    def test_bedrock_invalid_json_retries_once_then_raises_clear_error(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.converse.return_value = bedrock_response("這不是 JSON")
        with self.bedrock_environment():
            with self.assertRaisesRegex(ValueError, "invalid after one retry"):
                generate_json_with_llm("請產生 JSON", ANALYSIS_SCHEMA, "market_analysis", 17)

        self.assertEqual(client.converse.call_count, 2)
        retry_prompt = client.converse.call_args_list[1].kwargs["messages"][0]["content"][0]["text"]
        self.assertIn("previous response was invalid", retry_prompt)

    @patch.object(llm, "boto3")
    def test_bedrock_invalid_json_is_repaired_on_its_one_retry(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.converse.side_effect = [
            bedrock_response("不是 JSON"),
            bedrock_response(VALID_ANALYSIS),
        ]
        with self.bedrock_environment():
            result = generate_json_with_llm("請產生 JSON", ANALYSIS_SCHEMA, "market_analysis", 17)

        self.assertEqual(result, VALID_ANALYSIS)
        self.assertEqual(client.converse.call_count, 2)

    @patch.object(llm, "boto3")
    def test_bedrock_timeout_is_propagated_without_json_retry(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.converse.side_effect = TimeoutError("Bedrock timed out")
        with self.bedrock_environment():
            with self.assertRaisesRegex(TimeoutError, "timed out"):
                generate_json_with_llm("請產生 JSON", ANALYSIS_SCHEMA, "market_analysis", 5)

        self.assertEqual(client.converse.call_count, 1)

    @patch.object(llm, "boto3")
    def test_analyze_with_llm_uses_bedrock_converse(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.converse.return_value = bedrock_response(VALID_ANALYSIS)
        evidence = [{"evidence_id": "EV-MARKET-001", "data_type": "market"}]
        with self.bedrock_environment():
            result = analyze_with_llm("ETH", "市場如何？", evidence)

        self.assertEqual(result, VALID_ANALYSIS)
        self.assertEqual(client.converse.call_count, 1)
        self.assertIn("EV-MARKET-001", client.converse.call_args.kwargs["messages"][0]["content"][0]["text"])

    @patch.object(llm, "boto3")
    def test_critique_with_llm_uses_bedrock_converse(self, mock_boto3):
        client = mock_boto3.client.return_value
        client.converse.return_value = bedrock_response(VALID_CRITIQUE)
        result = {
            "reasoning": VALID_ANALYSIS,
            "stance": {"stance": "neutral", "label": "中性"},
            "risk_factors": ["資料有限"],
        }
        evidence = [{
            "evidence_id": "EV-MARKET-001",
            "source": "Mock", "data_type": "market",
            "reliability_score": 0.9, "content": {},
        }]
        with self.bedrock_environment():
            critique = critique_with_llm("ETH", "市場如何？", result, evidence)

        self.assertEqual(critique, VALID_CRITIQUE)
        request_prompt = client.converse.call_args.kwargs["messages"][0]["content"][0]["text"]
        self.assertIn("挑戰", request_prompt)
        self.assertIn("EV-MARKET-001", request_prompt)

    @patch.object(llm, "boto3")
    def test_bedrock_failure_falls_back_to_deterministic_offline_report(self, mock_boto3):
        mock_boto3.client.return_value.converse.side_effect = TimeoutError("Bedrock timed out")
        with self.bedrock_environment(), tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run("ETH", "近期風險？", output, live=False, use_llm=True)
            log = json.loads((output / "execution_log.json").read_text(encoding="utf-8"))
            report_exists = (output / "report.md").exists()

        self.assertEqual(result["reasoning"]["cited_evidence_ids"], result["evidence_ids"])
        llm_step = next(step for step in log["steps"] if step["name"] == "llm_reasoning")
        self.assertEqual(llm_step["status"], "fallback:TimeoutError")
        self.assertEqual(llm_step["provider"], "bedrock")
        self.assertTrue(report_exists)

    @patch("src.llm.urlopen")
    def test_openai_analysis_and_critic_remain_compatible(self, mock_urlopen):
        mock_urlopen.side_effect = [
            FakeResponse({"output_text": json.dumps(VALID_ANALYSIS)}),
            FakeResponse({"output_text": json.dumps(VALID_CRITIQUE, ensure_ascii=False)}),
        ]
        result = {
            "reasoning": VALID_ANALYSIS,
            "stance": {"stance": "neutral", "label": "中性"},
            "risk_factors": [],
        }
        evidence = [{
            "evidence_id": "EV-MARKET-001", "source": "Mock", "data_type": "market",
            "reliability_score": 0.9, "content": {},
        }]
        with patch.dict(os.environ, {"LLM_PROVIDER": "openai", "OPENAI_API_KEY": "test-key"}, clear=True):
            self.assertEqual(analyze_with_llm("ETH", "市場如何？", evidence), VALID_ANALYSIS)
            self.assertEqual(critique_with_llm("ETH", "市場如何？", result, evidence), VALID_CRITIQUE)

        self.assertEqual(mock_urlopen.call_count, 2)
        request = mock_urlopen.call_args_list[0].args[0]
        self.assertEqual(request.get_header("Authorization"), "Bearer test-key")

    def test_none_provider_still_falls_back_in_orchestrator(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "none"}, clear=True), tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run("ETH", "近期風險？", output, live=False, use_llm=True)
            log = json.loads((output / "execution_log.json").read_text(encoding="utf-8"))
            is_configured = llm_is_configured()

        self.assertFalse(is_configured)
        self.assertEqual(result["reasoning"]["cited_evidence_ids"], result["evidence_ids"])
        llm_step = next(step for step in log["steps"] if step["name"] == "llm_reasoning")
        self.assertEqual(llm_step["status"], "fallback:RuntimeError")

    def test_lambda_bedrock_configuration_skips_secrets_manager(self):
        with self.bedrock_environment():
            self.assertTrue(lambda_handler._load_llm_credentials())

    def test_template_scopes_bedrock_permission_to_the_selected_model(self):
        template = (Path(__file__).parents[1] / "aws" / "template.yaml").read_text(encoding="utf-8")
        self.assertIn("AllowedValues: [bedrock, gemini, openai, none]", template)
        self.assertIn("Default: amazon.nova-lite-v1:0", template)
        self.assertIn("HasNonBedrockSecret", template)
        self.assertIn("Action: bedrock:InvokeModel", template)
        self.assertIn("foundation-model/${BedrockModelId}", template)
        self.assertIn("BEDROCK_MODEL_ID: !Ref BedrockModelId", template)
        self.assertIn("BEDROCK_REGION: !Ref AWS::Region", template)
        self.assertNotIn("AdministratorAccess", template)


if __name__ == "__main__":
    unittest.main()
