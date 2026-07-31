import json
import os
import unittest
from unittest.mock import patch

from src.llm import analyze_with_llm, configured_provider, llm_is_configured


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


class FakeResponse:
    def __init__(self, payload):
        self.payload = json.dumps(payload).encode()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        return False

    def read(self):
        return self.payload


class GeminiAdapterTests(unittest.TestCase):
    def test_auto_selects_gemini_when_key_exists(self):
        with patch.dict(
            os.environ,
            {"GEMINI_API_KEY": "test-key"},
            clear=True,
        ):
            self.assertEqual(configured_provider(), "gemini")
            self.assertTrue(llm_is_configured())

    @patch("src.llm.urlopen")
    def test_gemini_uses_structured_json_and_parses_result(self, mock_urlopen):
        mock_urlopen.return_value = FakeResponse(
            {
                "candidates": [
                    {
                        "content": {
                            "parts": [{"text": json.dumps(VALID_ANALYSIS)}]
                        }
                    }
                ]
            }
        )
        evidence = [{"evidence_id": "EV-MARKET-001", "data_type": "market"}]
        with patch.dict(
            os.environ,
            {
                "LLM_PROVIDER": "gemini",
                "GEMINI_API_KEY": "test-key",
                "GEMINI_MODEL": "gemini-3.6-flash",
            },
            clear=True,
        ):
            result = analyze_with_llm("ETH", "What is the market outlook?", evidence)

        self.assertEqual(result, VALID_ANALYSIS)
        request = mock_urlopen.call_args.args[0]
        payload = json.loads(request.data.decode())
        self.assertIn("gemini-3.6-flash:generateContent", request.full_url)
        self.assertEqual(request.get_header("X-goog-api-key"), "test-key")
        self.assertEqual(
            payload["generationConfig"]["responseMimeType"],
            "application/json",
        )
        self.assertEqual(
            payload["generationConfig"]["responseJsonSchema"]["required"],
            list(VALID_ANALYSIS),
        )

    @patch("src.llm.urlopen")
    def test_gemini_blocked_response_raises_clear_error(self, mock_urlopen):
        mock_urlopen.return_value = FakeResponse(
            {"promptFeedback": {"blockReason": "SAFETY"}}
        )
        with patch.dict(
            os.environ,
            {"LLM_PROVIDER": "gemini", "GEMINI_API_KEY": "test-key"},
            clear=True,
        ):
            with self.assertRaisesRegex(ValueError, "SAFETY"):
                analyze_with_llm("ETH", "Question", [])

    def test_explicit_provider_requires_matching_key(self):
        with patch.dict(os.environ, {"LLM_PROVIDER": "gemini"}, clear=True):
            self.assertFalse(llm_is_configured())
            with self.assertRaisesRegex(RuntimeError, "GEMINI_API_KEY"):
                analyze_with_llm("ETH", "Question", [])


if __name__ == "__main__":
    unittest.main()
