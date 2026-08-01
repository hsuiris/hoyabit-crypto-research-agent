"""T0.5：驗證 LLM 與 Artifact Storage 的邊界介面已凍結且可被注入替換。

這裡不驗證真實 Bedrock 呼叫或 S3 上傳（T0.5 明確排除），只驗證契約本身：
推理角色能吃 MockLLMClient、產物只經由 ArtifactStore 落地、不同 run 不互相覆寫、
manifest 可稽核，且舊的離線執行沒有被破壞。
"""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import unittest
from datetime import datetime, timezone
from pathlib import Path
from unittest.mock import patch

from src import llm
from src.artifact_store import LocalArtifactStore
from src.llm import (
    ANALYSIS_SCHEMA,
    CRITIC_SCHEMA,
    ExistingLLMClient,
    OfflineLLMClient,
    analyze_with_llm,
    critique_with_llm,
)
from src.orchestrator import DEFAULT_TIME_BUDGET_SECONDS, run
from src.ports import ArtifactStore, LLMClient
from src.run_context import (
    DEFAULT_INTERNAL_DEADLINE_SECONDS,
    FROZEN_ENV_NAMES,
    RunContext,
)


VALID_ANALYSIS = {
    "market_judgment": "ETH 訊號互相牽制。",
    "confidence": 0.55,
    "facts": ["EV-MARKET-001 提供期間報酬。"],
    "inferences": ["動能不具決定性。"],
    "conclusion": "維持觀察。",
    "counter_evidence": ["社群情緒可能反轉。"],
    "observation_points": ["觀察成交量。"],
    "cited_evidence_ids": ["EV-MARKET-001"],
}

VALID_CRITIQUE = {
    "verdict": "concerns",
    "summary": "已檢查引用與反證。",
    "confidence_adjustment": -0.05,
    "findings": [{
        "severity": "low", "category": "confidence", "claim": "維持觀察",
        "issue": "信心應更保守。", "evidence_id": "EV-MARKET-001",
    }],
}

PLANNER_SCHEMA = {
    "type": "object",
    "properties": {"steps": {"type": "array", "items": {"type": "string"}}},
    "required": ["steps"],
    "additionalProperties": False,
}


class MockLLMClient:
    """測試用 LLMClient：記錄每次呼叫，回傳預先準備好的回應，不觸碰任何網路。"""

    def __init__(self, *responses: dict) -> None:
        self.responses = list(responses)
        self.calls: list[dict] = []

    def generate_json(self, *, prompt: str, schema: dict, schema_name: str, timeout_seconds: float) -> dict:
        self.calls.append({
            "prompt": prompt, "schema": schema,
            "schema_name": schema_name, "timeout_seconds": timeout_seconds,
        })
        return self.responses.pop(0) if len(self.responses) > 1 else self.responses[0]


def plan_with_client(client, question: str, coins: list[str]) -> dict:
    """代表未來 Planner 角色的最小呼叫端：只依賴 LLMClient，不 import 任何模型 SDK。

    T0.5 不實作 Planner 模組，這個函式只用來證明介面對 Planner 這類角色是夠用的。
    """
    return client.generate_json(
        prompt=json.dumps({"question": question, "coins": coins}, ensure_ascii=False),
        schema=PLANNER_SCHEMA,
        schema_name="research_plan",
        timeout_seconds=30,
    )


class LLMBoundaryTests(unittest.TestCase):
    def test_mock_and_real_clients_satisfy_the_llm_protocol(self):
        for client in (MockLLMClient({"steps": []}), OfflineLLMClient(), ExistingLLMClient()):
            self.assertIsInstance(client, LLMClient)

    @patch.object(llm, "boto3", None)
    @patch("src.llm.urlopen", side_effect=AssertionError("no network call is allowed"))
    def test_planner_role_can_run_on_mock_llm_client(self, _urlopen):
        client = MockLLMClient({"steps": ["collect", "analyse"]})
        plan = plan_with_client(client, "近期風險？", ["ETH"])

        self.assertEqual(plan["steps"], ["collect", "analyse"])
        self.assertEqual(client.calls[0]["schema_name"], "research_plan")
        self.assertEqual(client.calls[0]["timeout_seconds"], 30)

    @patch.object(llm, "boto3", None)
    @patch("src.llm.urlopen", side_effect=AssertionError("no network call is allowed"))
    def test_analyst_role_can_run_on_mock_llm_client(self, _urlopen):
        client = MockLLMClient(VALID_ANALYSIS)
        evidence = [{"evidence_id": "EV-MARKET-001", "data_type": "market"}]

        result = analyze_with_llm("ETH", "市場如何？", evidence, client=client)

        self.assertEqual(result, VALID_ANALYSIS)
        self.assertEqual(client.calls[0]["schema"], ANALYSIS_SCHEMA)
        self.assertEqual(client.calls[0]["schema_name"], "market_analysis")
        self.assertIn("EV-MARKET-001", client.calls[0]["prompt"])

    @patch.object(llm, "boto3", None)
    @patch("src.llm.urlopen", side_effect=AssertionError("no network call is allowed"))
    def test_critic_role_can_run_on_mock_llm_client(self, _urlopen):
        client = MockLLMClient(VALID_CRITIQUE)
        analysis = {"reasoning": VALID_ANALYSIS, "stance": {"stance": "neutral"}, "risk_factors": []}
        evidence = [{"evidence_id": "EV-MARKET-001", "source": "Mock", "data_type": "market",
                     "reliability_score": 0.9, "content": {}}]

        critique = critique_with_llm("ETH", "市場如何？", analysis, evidence, client=client)

        self.assertEqual(critique, VALID_CRITIQUE)
        self.assertEqual(client.calls[0]["schema"], CRITIC_SCHEMA)
        self.assertEqual(client.calls[0]["schema_name"], "research_critique")

    def test_injected_client_output_is_still_validated_deterministically(self):
        incomplete = {key: value for key, value in VALID_ANALYSIS.items() if key != "facts"}
        with self.assertRaisesRegex(ValueError, "missing required fields"):
            analyze_with_llm("ETH", "市場如何？", [], client=MockLLMClient(incomplete))

        over_range = dict(VALID_CRITIQUE, confidence_adjustment=0.4)
        analysis = {"reasoning": VALID_ANALYSIS, "stance": {}, "risk_factors": []}
        with self.assertRaisesRegex(ValueError, "confidence_adjustment"):
            critique_with_llm("ETH", "市場如何？", analysis, [], client=MockLLMClient(over_range))

    def test_offline_client_never_calls_a_model(self):
        with self.assertRaisesRegex(RuntimeError, "does not call any model"):
            analyze_with_llm("ETH", "市場如何？", [], client=OfflineLLMClient())

    def test_reasoning_modules_do_not_import_model_sdks_directly(self):
        """推理層（Orchestrator／資料層／新介面）只能經由 LLMClient 呼叫模型。

        `src/llm.py` 是唯一允許直接持有 provider SDK 與端點的模組；`src/app.py` 的來源摘要
        仍有既有的內嵌呼叫，屬於已記錄的例外（見 docs/COMPETITION_BLOCKERS.md），本任務不改 UI。
        """
        source_root = Path(__file__).parents[1] / "src"
        for name in ("orchestrator.py", "day2_sources.py", "ports.py", "artifact_store.py", "run_context.py"):
            source = (source_root / name).read_text(encoding="utf-8")
            for forbidden in ("import boto3", "from boto3", "import openai", "generativelanguage",
                              "api.openai.com", "bedrock-runtime"):
                self.assertFalse(
                    forbidden in source,
                    f"{name} must call models only through LLMClient (found {forbidden!r})",
                )


class ArtifactStoreBoundaryTests(unittest.TestCase):
    def test_local_store_satisfies_the_artifact_store_protocol(self):
        with tempfile.TemporaryDirectory() as directory:
            self.assertIsInstance(LocalArtifactStore(directory, "runs/x"), ArtifactStore)

    def test_local_store_writes_every_submission_artifact(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalArtifactStore(directory, "runs/run-a")
            report_uri = store.write_text("report.md", "# ETH\n")
            store.write_json("evidence.json", [{"evidence_id": "EV-MARKET-001"}])
            store.write_json("execution_log.json", {"status": "success"})
            store.write_bytes("charts/price.svg", b"<svg/>")

            base = Path(directory) / "runs" / "run-a"
            self.assertTrue(report_uri.startswith("file://"))
            self.assertEqual((base / "report.md").read_text(encoding="utf-8"), "# ETH\n")
            self.assertEqual(json.loads((base / "evidence.json").read_text(encoding="utf-8"))[0]["evidence_id"],
                             "EV-MARKET-001")
            self.assertEqual(json.loads((base / "execution_log.json").read_text(encoding="utf-8"))["status"],
                             "success")
            self.assertEqual((base / "charts" / "price.svg").read_bytes(), b"<svg/>")

    def test_different_run_ids_do_not_overwrite_each_other(self):
        with tempfile.TemporaryDirectory() as directory:
            first = RunContext.create("問題", ["ETH"], run_id="run-1")
            second = RunContext.create("問題", ["ETH"], run_id="run-2")
            store_a = LocalArtifactStore.for_run(first, directory)
            store_b = LocalArtifactStore.for_run(second, directory)

            store_a.write_text("report.md", "first run")
            store_b.write_text("report.md", "second run")

            self.assertNotEqual(store_a.base_path, store_b.base_path)
            self.assertEqual(store_a.resolve("report.md").read_text(encoding="utf-8"), "first run")
            self.assertEqual(store_b.resolve("report.md").read_text(encoding="utf-8"), "second run")

    def test_manifest_lists_files_with_sha256_and_is_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalArtifactStore(directory, "runs/run-a")
            store.write_text("report.md", "# ETH\n")
            store.write_json("evidence.json", [])

            manifest = store.finalize_manifest()
            paths = [entry["path"] for entry in manifest["files"]]

            self.assertEqual(paths, ["evidence.json", "report.md"])
            self.assertEqual(manifest["artifact_count"], 2)
            report_entry = next(entry for entry in manifest["files"] if entry["path"] == "report.md")
            self.assertEqual(report_entry["sha256"], hashlib.sha256("# ETH\n".encode()).hexdigest())
            self.assertEqual(report_entry["bytes"], len("# ETH\n".encode()))

            persisted = json.loads((Path(directory) / "runs" / "run-a" / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual([entry["path"] for entry in persisted["files"]], paths)
            self.assertEqual([entry["path"] for entry in store.finalize_manifest()["files"]], paths)

    def test_store_rejects_absolute_paths_and_parent_escapes(self):
        with tempfile.TemporaryDirectory() as directory:
            store = LocalArtifactStore(directory, "runs/run-a")
            for bad in ("/tmp/outputs/report.md", "../report.md", "a/../../report.md", ""):
                with self.assertRaises(ValueError):
                    store.write_text(bad, "nope")

    def test_core_modules_do_not_hardcode_tmp_output_paths(self):
        source_root = Path(__file__).parents[1] / "src"
        for name in ("orchestrator.py", "day2_sources.py", "artifact_store.py", "run_context.py", "ports.py"):
            source = (source_root / name).read_text(encoding="utf-8")
            for forbidden in ("/tmp/outputs", "s3.put_object", "put_object("):
                self.assertFalse(
                    forbidden in source,
                    f"{name} must write artifacts only through ArtifactStore (found {forbidden!r})",
                )


class RunContextTests(unittest.TestCase):
    def test_environment_variable_names_are_frozen(self):
        self.assertEqual(FROZEN_ENV_NAMES, (
            "LLM_PROVIDER", "BEDROCK_MODEL_ID", "AWS_REGION",
            "ARTIFACT_BUCKET", "ARTIFACT_PREFIX", "RUN_MODE", "INTERNAL_DEADLINE_SECONDS",
        ))

    def test_context_carries_every_frozen_field(self):
        environment = {
            "LLM_PROVIDER": "bedrock", "BEDROCK_MODEL_ID": "amazon.nova-lite-v1:0",
            "AWS_REGION": "ap-northeast-1", "ARTIFACT_BUCKET": "demo-bucket",
            "ARTIFACT_PREFIX": "competition", "RUN_MODE": "live",
            "INTERNAL_DEADLINE_SECONDS": "600",
        }
        as_of = datetime(2026, 8, 1, tzinfo=timezone.utc)
        with patch.dict(os.environ, environment, clear=True):
            context = RunContext.create("近期風險？", ["eth", "btc"], run_id="run-9", as_of=as_of)

        payload = context.as_dict()
        for field in ("run_id", "run_mode", "question", "coins", "started_at", "as_of", "deadline_at",
                      "output_prefix", "code_commit", "config_version", "model_provider", "model_id"):
            self.assertIn(field, payload)
        self.assertEqual(context.coins, ("ETH", "BTC"))
        self.assertEqual(context.run_mode, "live")
        self.assertEqual(context.output_prefix, "competition/run-9")
        self.assertEqual(context.artifact_bucket, "demo-bucket")
        self.assertEqual(context.region, "ap-northeast-1")
        self.assertEqual(context.model_provider, "bedrock")
        self.assertEqual(context.model_id, "amazon.nova-lite-v1:0")
        self.assertEqual(context.deadline_seconds, 600.0)
        self.assertEqual(context.as_of, as_of.isoformat())
        self.assertGreater(context.deadline_at, context.started_at)

    def test_defaults_match_the_orchestrator_time_budget(self):
        with patch.dict(os.environ, {}, clear=True):
            context = RunContext.create("問題", "ETH")
        self.assertEqual(DEFAULT_INTERNAL_DEADLINE_SECONDS, DEFAULT_TIME_BUDGET_SECONDS)
        self.assertEqual(context.deadline_seconds, DEFAULT_TIME_BUDGET_SECONDS)
        self.assertEqual(context.run_mode, "offline")
        self.assertTrue(context.output_prefix.startswith("runs/"))
        self.assertIsNone(context.artifact_bucket)

    def test_invalid_run_mode_and_deadline_are_rejected(self):
        with patch.dict(os.environ, {"RUN_MODE": "turbo"}, clear=True):
            with self.assertRaisesRegex(ValueError, "RUN_MODE"):
                RunContext.create("問題", "ETH")
        with patch.dict(os.environ, {"INTERNAL_DEADLINE_SECONDS": "0"}, clear=True):
            with self.assertRaisesRegex(ValueError, "INTERNAL_DEADLINE_SECONDS"):
                RunContext.create("問題", "ETH")
        with patch.dict(os.environ, {}, clear=True):
            with self.assertRaisesRegex(ValueError, "at least one coin"):
                RunContext.create("問題", [])


class OfflineRegressionTests(unittest.TestCase):
    def test_existing_offline_run_still_produces_all_three_artifacts(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            result = run("ETH", "離線回歸測試", output, live=False, use_llm=False)

            self.assertEqual(result["reasoning"]["cited_evidence_ids"], result["evidence_ids"])
            for name in ("report.md", "evidence.json", "execution_log.json"):
                self.assertTrue((output / name).exists(), name)
            log = json.loads((output / "execution_log.json").read_text(encoding="utf-8"))
            llm_step = next(step for step in log["steps"] if step["name"] == "llm_reasoning")
            self.assertEqual(llm_step["status"], "offline_fallback")

    def test_offline_run_artifacts_can_be_republished_through_the_store(self):
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory) / "legacy"
            run("ETH", "離線回歸測試", output, live=False, use_llm=False)
            context = RunContext.create("離線回歸測試", ["ETH"], run_id="run-offline")
            store = LocalArtifactStore.for_run(context, Path(directory) / "artifacts")
            for name in ("report.md", "evidence.json", "execution_log.json"):
                store.write_bytes(name, (output / name).read_bytes())
            manifest = store.finalize_manifest()

        self.assertEqual(manifest["artifact_count"], 3)
        self.assertTrue(all(entry["sha256"] for entry in manifest["files"]))
        self.assertIn("run-offline", manifest["prefix"])


if __name__ == "__main__":
    unittest.main()
