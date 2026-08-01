"""T7：正式 run 生命週期、deadline 收尾與不可覆寫輸出的接線測試。

這一份測的是「run_manager 接上 Orchestrator／Web／Lambda 之後的行為」，`src/run_manager.py`
本身的純模組規則在 `tests/test_run_manager.py`。所有執行一律離線（`live=False`），
需要模型的地方注入 mock client，因此不連外；檔案輸出全部限制在 tempdir 之內。
"""

from __future__ import annotations

import json
import os
import tempfile
import time
import unittest
from pathlib import Path
from unittest.mock import patch

from src import app as web_app
from src import orchestrator
from src.orchestrator import (DEFAULT_FINALIZATION_DEADLINE_SECONDS, DEFAULT_TIME_BUDGET_SECONDS,
                              run, run_comparison)
from src.run_manager import (CHECKPOINT_FILENAME, CHECKPOINT_STAGES,
                             FINALIZATION_DEADLINE_SECONDS_MAX, HARD_DEADLINE_SECONDS_MAX,
                             RUN_MODE_FORMAL, RUN_MODE_TEST, STATUS_COMPLETED,
                             STATUS_COMPLETED_DEGRADED, STATUS_FAILED, STATUS_RUNNING,
                             FormalRunAlreadyExistsError, RunDirectoryExistsError, RunManager,
                             compute_question_hash)
from src.schemas import ARTIFACT_FILENAMES

QUESTION = "ETH 近期市場、新聞與鏈上訊號呈現什麼風險？"


def _offline_run(manager: RunManager, question: str = QUESTION, coin: str = "ETH",
                 mode: str = RUN_MODE_TEST, **kwargs):
    """建立 run record 並以離線模式跑完一次分析，回傳 (record, run_dir, result)。"""
    record = manager.create_run(question, [coin], mode=mode, **kwargs)
    run_dir = manager.run_directory(record)
    result = run(coin, question, run_dir, live=False, use_llm=False, run_record=record)
    return record, run_dir, result


class RunIdentityTests(unittest.TestCase):
    def test_run_id_follows_the_competition_format(self):
        """run_id 為 `RUN-<時間戳>-<幣種>-<尾碼>`，光看目錄名稱就知道是哪一次、哪個標的。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            record = manager.create_run(QUESTION, ["ETH"], mode=RUN_MODE_TEST)

            parts = record.run_id.split("-")
            self.assertEqual(parts[0], "RUN")
            self.assertRegex(parts[1], r"^\d{8}T\d{6}Z$")
            self.assertEqual(parts[2], "ETH")
            self.assertTrue(parts[3])

    def test_comparison_run_id_carries_both_coins(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            record = manager.create_run("比較題", ["btc", "eth"], mode=RUN_MODE_TEST)
            self.assertIn("-BTC-ETH-", record.run_id)

    def test_each_run_gets_its_own_directory_and_artifacts(self):
        """同一題目連跑兩次（test 模式）不會互相覆寫，兩份提交物各自完整。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            first_record, first_dir, _ = _offline_run(manager)
            second_record, second_dir, _ = _offline_run(manager)

            self.assertNotEqual(first_record.run_id, second_record.run_id)
            self.assertNotEqual(first_dir, second_dir)
            for run_dir in (first_dir, second_dir):
                for filename in ARTIFACT_FILENAMES.values():
                    self.assertTrue((run_dir / filename).is_file(), f"{run_dir}/{filename}")
            first_manifest = json.loads((first_dir / "manifest.json").read_text(encoding="utf-8"))
            second_manifest = json.loads((second_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertNotEqual(first_manifest["run_id"], second_manifest["run_id"])
            # 同一題目 → 同一個 question_hash；test 模式不會因此被擋下。
            self.assertEqual(first_manifest["question_hash"], second_manifest["question_hash"])

    def test_completed_run_output_cannot_be_reused_by_a_new_run(self):
        """已寫出提交物的 run 目錄不可被第二次執行接手。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            record, _, _ = _offline_run(manager)

            with self.assertRaises(RunDirectoryExistsError):
                manager.create_run(QUESTION, ["ETH"], mode=RUN_MODE_TEST, run_id=record.run_id)

    def test_manifest_and_execution_log_share_the_run_identity(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            record, run_dir, result = _offline_run(manager, mode=RUN_MODE_FORMAL)

            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            log = json.loads((run_dir / "execution_log.json").read_text(encoding="utf-8"))

            self.assertEqual(manifest["run_id"], record.run_id)
            self.assertEqual(log["run_id"], record.run_id)
            self.assertEqual(result["run_id"], record.run_id)
            self.assertEqual(manifest["run_mode"], RUN_MODE_FORMAL)
            self.assertEqual(log["run_mode"], RUN_MODE_FORMAL)
            self.assertEqual(manifest["question_hash"],
                             compute_question_hash(QUESTION, ["ETH"]))
            self.assertEqual(manifest["question_hash"], log["question_hash"])
            # 正式執行資料：模式、時間、程式版本、provider／model、狀態。
            lifecycle = manifest["run_lifecycle"]
            for field in ("run_mode", "question_hash", "status", "started_at", "completed_at",
                          "code_commit", "provider", "model"):
                self.assertIn(field, lifecycle)
            self.assertTrue(lifecycle["code_commit"])
            self.assertEqual(lifecycle["completed_at"], manifest["completed_at"])


class FormalLockWiringTests(unittest.TestCase):
    def test_test_mode_runs_are_repeatable(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            for _ in range(3):
                _, run_dir, _ = _offline_run(manager)
                self.assertTrue((run_dir / "report.md").is_file())

    def test_formal_run_accidental_duplicate_is_rejected_before_any_work(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            _offline_run(manager, mode=RUN_MODE_FORMAL)

            with self.assertRaises(FormalRunAlreadyExistsError):
                manager.create_run(QUESTION, ["ETH"], mode=RUN_MODE_FORMAL)

    def test_test_run_does_not_consume_the_formal_lock(self):
        """test run 不影響 formal lock：先跑測試，正式執行仍是「第一次」。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            _offline_run(manager, mode=RUN_MODE_TEST)
            question_hash = compute_question_hash(QUESTION, ["ETH"])
            self.assertEqual(manager.formal_lock_history(question_hash), [])

            record, _, _ = _offline_run(manager, mode=RUN_MODE_FORMAL)
            history = manager.formal_lock_history(question_hash)
            self.assertEqual([entry["run_id"] for entry in history], [record.run_id])

    def test_authorized_rerun_records_full_lineage_in_the_manifest(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            first, first_dir, _ = _offline_run(manager, mode=RUN_MODE_FORMAL)

            rerun, rerun_dir, _ = _offline_run(
                manager, mode=RUN_MODE_FORMAL, rerun_of=first.run_id,
                rerun_reason="第一次模型逾時，經授權重跑", authorized_rerun=True,
            )

            manifest = json.loads((rerun_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["rerun_of"], first.run_id)
            self.assertEqual(manifest["rerun_reason"], "第一次模型逾時，經授權重跑")
            self.assertTrue(manifest["authorized_rerun"])
            # 第一次的紀錄與產物都必須還在。
            self.assertTrue((first_dir / "manifest.json").is_file())
            first_manifest = json.loads((first_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertIsNone(first_manifest["rerun_of"])
            self.assertFalse(first_manifest["authorized_rerun"])
            history = manager.formal_lock_history(first.question_hash)
            self.assertEqual([entry["run_id"] for entry in history], [first.run_id, rerun.run_id])

    def test_report_prints_run_mode_status_and_rerun_lineage(self):
        """報告本身要能回答「這是正式還是測試、有沒有降級、是不是重跑」。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            first, first_dir, _ = _offline_run(manager, mode=RUN_MODE_FORMAL)
            _, rerun_dir, _ = _offline_run(
                manager, mode=RUN_MODE_FORMAL, rerun_of=first.run_id,
                rerun_reason="示範授權重跑", authorized_rerun=True,
            )

            first_report = (first_dir / "report.md").read_text(encoding="utf-8")
            rerun_report = (rerun_dir / "report.md").read_text(encoding="utf-8")

            self.assertIn(f"執行性質：{RUN_MODE_FORMAL}／狀態：{STATUS_COMPLETED_DEGRADED}",
                          first_report)
            self.assertIn("本次降級項目：", first_report)
            self.assertNotIn("重跑血緣", first_report)
            self.assertIn(f"重跑血緣：本 run 重跑自 {first.run_id}", rerun_report)
            self.assertIn("示範授權重跑", rerun_report)

    def test_failed_formal_run_keeps_its_record_and_allows_authorized_rerun(self):
        """階段失敗時：狀態轉 FAILED、checkpoint 落地、第一次紀錄不刪除。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            record = manager.create_run(QUESTION, ["ETH"], mode=RUN_MODE_FORMAL)
            run_dir = manager.run_directory(record)

            with patch.object(orchestrator, "enrich_and_score_evidence",
                              side_effect=RuntimeError("scoring exploded")):
                with self.assertRaises(RuntimeError):
                    run("ETH", QUESTION, run_dir, live=False, use_llm=False, run_record=record)

            self.assertEqual(record.status, STATUS_FAILED)
            checkpoint = json.loads((run_dir / CHECKPOINT_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(checkpoint["status"], STATUS_FAILED)
            # 失敗發生在計分階段，因此 plan 與 collected_evidence 已經保存下來。
            self.assertIn("plan", checkpoint["stages"])
            self.assertIn("collected_evidence", checkpoint["stages"])
            self.assertNotIn("final_artifacts", checkpoint["stages"])

            history = manager.formal_lock_history(record.question_hash)
            self.assertEqual(history[0]["status"], STATUS_FAILED)

            rerun, rerun_dir, _ = _offline_run(
                manager, mode=RUN_MODE_FORMAL, rerun_of=record.run_id,
                rerun_reason="第一次計分階段失敗", authorized_rerun=True,
            )
            self.assertTrue((rerun_dir / "report.md").is_file())
            history = manager.formal_lock_history(record.question_hash)
            self.assertEqual([entry["run_id"] for entry in history], [record.run_id, rerun.run_id])
            self.assertEqual(history[0]["status"], STATUS_FAILED)


class RunStatusTests(unittest.TestCase):
    def test_offline_run_is_reported_as_completed_degraded_with_reasons(self):
        """離線執行的證據全是 fixture，狀態必須誠實標為 COMPLETED_DEGRADED。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            record, run_dir, result = _offline_run(manager)

            self.assertEqual(record.status, STATUS_COMPLETED_DEGRADED)
            self.assertEqual(result["run_status"], STATUS_COMPLETED_DEGRADED)
            self.assertIn("all_evidence_is_fallback_fixture", result["degradation_reasons"])
            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(manifest["status"], STATUS_COMPLETED_DEGRADED)
            self.assertEqual(manifest["degradation_reasons"], result["degradation_reasons"])

    def test_clean_run_with_substantive_evidence_is_completed(self):
        """有實證來源（本地 CSV）且沒有任何降級時，狀態為 COMPLETED。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            record = manager.create_run("BTC 長期價格位置？", ["BTC"], mode=RUN_MODE_TEST)
            result = run("BTC", "BTC 長期價格位置？", manager.run_directory(record),
                         live=False, use_llm=False, run_record=record,
                         history_path=Path("data") / "BTC.csv")

            self.assertEqual(result["degradation_reasons"], [])
            self.assertEqual(result["run_status"], STATUS_COMPLETED)
            self.assertEqual(record.status, STATUS_COMPLETED)

    def test_collector_failure_degrades_instead_of_failing_the_run(self):
        """Collector 全數逾時仍要產出完整報告，狀態是 COMPLETED_DEGRADED，不是 FAILED。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            record = manager.create_run(QUESTION, ["ETH"], mode=RUN_MODE_TEST)
            run_dir = manager.run_directory(record)
            result = run("ETH", QUESTION, run_dir, live=True, use_llm=False,
                         deadline=time.monotonic() - 1, run_record=record)

            self.assertEqual(record.status, STATUS_COMPLETED_DEGRADED)
            self.assertTrue(any(reason.startswith("collector_skipped:")
                                for reason in result["degradation_reasons"]))
            for filename in ARTIFACT_FILENAMES.values():
                self.assertTrue((run_dir / filename).is_file(), filename)

    def test_run_status_is_recorded_even_without_a_run_record(self):
        """沒有 run record 的既有呼叫端仍會取得狀態欄位，行為不變。"""
        with tempfile.TemporaryDirectory() as directory:
            result = run("ETH", QUESTION, Path(directory), live=False, use_llm=False)
            log = json.loads((Path(directory) / "execution_log.json").read_text(encoding="utf-8"))

            self.assertEqual(log["status"], "success")
            self.assertEqual(log["run_status"], result["run_status"])
            self.assertIsNone(log["run_mode"])
            self.assertIsNone(log["run_lifecycle"])

    def test_comparison_pair_status_reflects_both_legs(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            record = manager.create_run("BTC 與 ETH 哪個風險較低？", ["BTC", "ETH"],
                                        mode=RUN_MODE_FORMAL)
            payload = run_comparison("BTC", "ETH", "BTC 與 ETH 哪個風險較低？",
                                     manager.run_directory(record), live=False, use_llm=False,
                                     run_record=record)

            manifest = payload["manifest"]
            self.assertEqual(manifest["run_id"], record.run_id)
            self.assertEqual(manifest["run_mode"], RUN_MODE_FORMAL)
            self.assertEqual(manifest["status"], record.status)
            self.assertEqual(set(manifest["validation"]["run_status_by_coin"]), {"BTC", "ETH"})
            self.assertIn(record.status, (STATUS_COMPLETED, STATUS_COMPLETED_DEGRADED))


class DeadlineWiringTests(unittest.TestCase):
    """收尾窗口：`hard <= 900`、`finalization <= 840`，且不放寬既有 720 秒預算。"""

    # 已耗用 860 秒（>= 840 收尾門檻）但仍有 40 秒可用，正好落在「該收尾但還沒逾時」的區間。
    HARD = HARD_DEADLINE_SECONDS_MAX
    FINALIZATION = FINALIZATION_DEADLINE_SECONDS_MAX
    REMAINING = 40.0

    def _finalizing_run(self, run_dir: Path, **kwargs):
        return run("ETH", QUESTION, run_dir, live=False, use_llm=True,
                   time_budget_seconds=self.HARD,
                   finalization_deadline_seconds=self.FINALIZATION,
                   deadline=time.monotonic() + self.REMAINING, **kwargs)

    def test_defaults_stay_below_the_competition_ceilings(self):
        self.assertLessEqual(DEFAULT_TIME_BUDGET_SECONDS, HARD_DEADLINE_SECONDS_MAX)
        self.assertLessEqual(DEFAULT_FINALIZATION_DEADLINE_SECONDS,
                             FINALIZATION_DEADLINE_SECONDS_MAX)
        self.assertLess(DEFAULT_FINALIZATION_DEADLINE_SECONDS, DEFAULT_TIME_BUDGET_SECONDS)
        self.assertEqual(DEFAULT_TIME_BUDGET_SECONDS, 720.0)

    def test_time_budget_is_clamped_to_the_competition_hard_ceiling(self):
        """呼叫端要求超過 900 秒時只會被夾回 900，不會真的放寬。"""
        with tempfile.TemporaryDirectory() as directory:
            run("ETH", QUESTION, Path(directory), live=False, use_llm=False,
                time_budget_seconds=1800)
            log = json.loads((Path(directory) / "execution_log.json").read_text(encoding="utf-8"))
            deadline = log["time_budget"]["deadline"]

            self.assertEqual(deadline["hard_deadline_seconds"], HARD_DEADLINE_SECONDS_MAX)
            self.assertLessEqual(deadline["finalization_deadline_seconds"],
                                 FINALIZATION_DEADLINE_SECONDS_MAX)

    @patch("src.orchestrator.critique_with_llm")
    @patch("src.orchestrator.analyze_with_llm")
    def test_finalization_window_skips_models_but_still_writes_every_artifact(
            self, mock_analyze, mock_critique):
        """進入收尾窗口：不再呼叫模型與 Critic，但六項提交物照樣完整產出。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            record = manager.create_run(QUESTION, ["ETH"], mode=RUN_MODE_FORMAL)
            run_dir = manager.run_directory(record)

            result = self._finalizing_run(run_dir, run_record=record)

            mock_analyze.assert_not_called()
            mock_critique.assert_not_called()
            log = json.loads((run_dir / "execution_log.json").read_text(encoding="utf-8"))
            deadline = log["time_budget"]["deadline"]
            self.assertEqual(deadline["reason"], "finalization_window_reached")
            self.assertTrue(deadline["llm_skipped_for_deadline"])
            self.assertTrue(deadline["critic_skipped_for_deadline"])
            self.assertEqual(result["critic_status"], "skipped:finalization_window_reached")
            for filename in ARTIFACT_FILENAMES.values():
                self.assertTrue((run_dir / filename).is_file(), filename)
            # 報告仍然完整：立場、Claim 與 Citation Gate 都在。
            report = (run_dir / "report.md").read_text(encoding="utf-8")
            self.assertIn("## 主張（Claim）", report)
            self.assertTrue(result["claims"])
            self.assertIn(record.status, (STATUS_COMPLETED, STATUS_COMPLETED_DEGRADED))

    def test_finalization_window_skips_follow_up_collection(self):
        """收尾窗口內不再做補充蒐集（本專案唯一的補充蒐集是新聞全文爬取）。"""
        with tempfile.TemporaryDirectory() as directory:
            result = run("ETH", QUESTION, Path(directory), live=False, use_llm=False,
                         fulltext=True, time_budget_seconds=self.HARD,
                         finalization_deadline_seconds=self.FINALIZATION,
                         deadline=time.monotonic() + self.REMAINING)

            self.assertTrue(result["deadline"]["follow_up_collection_skipped"])
            self.assertIn("fulltext:skipped:finalization_window_reached",
                          result["collection_log"])

    @patch("src.orchestrator.analyze_with_llm")
    def test_existing_watchdog_labels_are_unchanged(self, mock_analyze):
        """原本的 watchdog（預算已耗盡）行為與字面狀態不變。"""
        with tempfile.TemporaryDirectory() as directory:
            run("ETH", QUESTION, Path(directory), live=False, use_llm=True,
                deadline=time.monotonic() - 1)
            log = json.loads((Path(directory) / "execution_log.json").read_text(encoding="utf-8"))
            step = next(entry for entry in log["steps"] if entry["name"] == "llm_reasoning")

            self.assertEqual(step["status"], "fallback:TimeBudgetExceeded")
            mock_analyze.assert_not_called()


class CheckpointWiringTests(unittest.TestCase):
    def test_every_pipeline_stage_is_checkpointed(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            _, run_dir, _ = _offline_run(manager)

            checkpoint = json.loads((run_dir / CHECKPOINT_FILENAME).read_text(encoding="utf-8"))
            self.assertEqual(set(checkpoint["stages"]), set(CHECKPOINT_STAGES))
            self.assertEqual(checkpoint["run_mode"], RUN_MODE_TEST)

    def test_checkpoint_file_is_not_one_of_the_submission_artifacts(self):
        """checkpoint 是內部恢復資料，不得混進六項提交物或 manifest 的檔案清單。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            _, run_dir, _ = _offline_run(manager)

            manifest = json.loads((run_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertNotIn(CHECKPOINT_FILENAME, ARTIFACT_FILENAMES.values())
            self.assertNotIn(CHECKPOINT_FILENAME, [entry["path"] for entry in manifest["files"]])


class WebDemoRunLifecycleTests(unittest.TestCase):
    def test_web_run_uses_a_unique_run_directory_under_the_artifact_root(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"ARTIFACT_ROOT": directory}, clear=False):
                first_record, first_dir = web_app._prepare_run(QUESTION, ["ETH"], RUN_MODE_TEST)
                second_record, second_dir = web_app._prepare_run(QUESTION, ["ETH"], RUN_MODE_TEST)

            self.assertNotEqual(first_record.run_id, second_record.run_id)
            self.assertEqual(first_dir, Path(directory) / "runs" / first_record.run_id)
            self.assertEqual(second_dir, Path(directory) / "runs" / second_record.run_id)

    def test_web_formal_duplicate_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"ARTIFACT_ROOT": directory}, clear=False):
                web_app._prepare_run(QUESTION, ["ETH"], RUN_MODE_FORMAL)
                with self.assertRaises(FormalRunAlreadyExistsError):
                    web_app._prepare_run(QUESTION, ["ETH"], RUN_MODE_FORMAL)

    def test_unknown_web_mode_falls_back_to_test(self):
        self.assertEqual(web_app._normalise_run_mode("FORMAL"), RUN_MODE_FORMAL)
        self.assertEqual(web_app._normalise_run_mode(""), RUN_MODE_TEST)
        self.assertEqual(web_app._normalise_run_mode("turbo"), RUN_MODE_TEST)

    def test_home_page_exposes_the_run_mode_selector(self):
        page = web_app._home_page()
        self.assertIn('name="mode"', page)
        self.assertIn(f'value="{RUN_MODE_FORMAL}"', page)

    def test_result_page_shows_run_id_and_status(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            _, run_dir, result = _offline_run(manager)
            report = (run_dir / "report.md").read_text(encoding="utf-8")
            evidence = json.loads((run_dir / "evidence.json").read_text(encoding="utf-8"))
            log = json.loads((run_dir / "execution_log.json").read_text(encoding="utf-8"))

            page = web_app._result_page(result, report, evidence, log)

            self.assertIn(result["run_id"], page)
            self.assertIn(result["run_status"], page)


class LambdaRunLifecycleTests(unittest.TestCase):
    def test_lambda_uses_a_unique_run_directory_in_the_scratch_root(self):
        import lambda_handler

        with tempfile.TemporaryDirectory() as directory:
            with patch.dict(os.environ, {"ARTIFACT_ROOT": directory}, clear=False):
                first_record, first_dir = lambda_handler._prepare_run(QUESTION, ["ETH"],
                                                                      RUN_MODE_TEST)
                second_record, second_dir = lambda_handler._prepare_run(QUESTION, ["ETH"],
                                                                        RUN_MODE_TEST)

            self.assertNotEqual(first_dir, second_dir)
            self.assertEqual(first_dir, Path(directory) / "runs" / first_record.run_id)
            self.assertTrue(second_record.run_id.startswith("RUN-"))

    def test_lambda_default_root_is_the_container_scratch_directory(self):
        import lambda_handler

        self.assertEqual(lambda_handler.DEFAULT_ARTIFACT_ROOT, "/tmp")
        self.assertEqual(lambda_handler._run_mode("formal"), RUN_MODE_FORMAL)
        self.assertEqual(lambda_handler._run_mode(None), RUN_MODE_TEST)

    def test_lambda_no_longer_writes_to_a_shared_output_directory(self):
        source = (Path(__file__).parents[1] / "lambda_handler.py").read_text(encoding="utf-8")
        self.assertNotIn("/tmp/outputs", source)


class StatusTransitionWiringTests(unittest.TestCase):
    def test_record_is_running_while_the_pipeline_executes(self):
        observed = {}

        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            record = manager.create_run(QUESTION, ["ETH"], mode=RUN_MODE_FORMAL)

            original = orchestrator.enrich_and_score_evidence

            def spy(*args, **kwargs):
                observed["status"] = record.status
                return original(*args, **kwargs)

            with patch.object(orchestrator, "enrich_and_score_evidence", side_effect=spy):
                run("ETH", QUESTION, manager.run_directory(record), live=False, use_llm=False,
                    run_record=record)

        self.assertEqual(observed["status"], STATUS_RUNNING)
        self.assertIn(record.status, (STATUS_COMPLETED, STATUS_COMPLETED_DEGRADED))


if __name__ == "__main__":
    unittest.main()
