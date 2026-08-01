"""T7：正式執行生命週期管理（`src/run_manager.py`）的測試。

只驗證純模組行為：狀態機轉換、輸出防覆寫、formal lock 與 authorized rerun、
deadline 決策函式。不呼叫 LLM、不碰網路，檔案系統一律限制在 `tempfile.TemporaryDirectory()`
之內。
"""

from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from src.orchestrator import DEFAULT_TIME_BUDGET_SECONDS
from src.run_manager import (
    DEFAULT_FINALIZATION_DEADLINE_SECONDS,
    DEFAULT_HARD_DEADLINE_SECONDS,
    FINALIZATION_DEADLINE_SECONDS_MAX,
    HARD_DEADLINE_SECONDS_MAX,
    STATUS_COMPLETED,
    STATUS_COMPLETED_DEGRADED,
    STATUS_CREATED,
    STATUS_FAILED,
    STATUS_RUNNING,
    FormalRunAlreadyExistsError,
    InvalidRunTransitionError,
    RunDirectoryExistsError,
    RunManager,
    compute_question_hash,
    evaluate_deadline,
)


class RunCreationTests(unittest.TestCase):
    def test_test_mode_run_is_repeatable(self):
        """test run：同一問題可以重複建立，不受 formal lock 限制。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            first = manager.create_run("近期風險？", ["ETH"], mode="test", run_id="run-test-1")
            second = manager.create_run("近期風險？", ["ETH"], mode="test", run_id="run-test-2")

            self.assertEqual(first.status, STATUS_CREATED)
            self.assertEqual(second.status, STATUS_CREATED)
            self.assertEqual(first.question_hash, second.question_hash)

    def test_unique_run_ids_across_repeated_creation(self):
        """連續建立多個 run（不指定 run_id）不會重複，交由 RunContext 產生唯一 id。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            run_ids = {
                manager.create_run(f"問題 {i}", ["ETH"], mode="test").run_id
                for i in range(5)
            }
            self.assertEqual(len(run_ids), 5)

    def test_formal_run_accidental_rerun_is_rejected(self):
        """formal run：相同 question_hash 意外重跑必須被拒絕。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            manager.create_run("BTC 近期走勢？", ["BTC"], mode="formal", run_id="run-formal-1")

            with self.assertRaises(FormalRunAlreadyExistsError):
                manager.create_run("BTC 近期走勢？", ["BTC"], mode="formal", run_id="run-formal-2")

    def test_authorized_rerun_succeeds_and_records_lineage(self):
        """authorized rerun：明確授權且指向前一個 run_id 時允許重跑，lineage 指回前一個 run。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            first = manager.create_run("ETH 近期走勢？", ["ETH"], mode="formal", run_id="run-formal-a")

            rerun = manager.create_run(
                "ETH 近期走勢？", ["ETH"], mode="formal", run_id="run-formal-b",
                rerun_of=first.run_id, rerun_reason="第一次 Bedrock timeout，需要重跑",
                authorized_rerun=True,
            )

            self.assertEqual(rerun.rerun_of, first.run_id)
            self.assertTrue(rerun.authorized_rerun)
            self.assertEqual(rerun.rerun_reason, "第一次 Bedrock timeout，需要重跑")

            history = manager.formal_lock_history(first.question_hash)
            self.assertEqual([entry["run_id"] for entry in history], [first.run_id, rerun.run_id])

    def test_unauthorized_rerun_pointing_at_prior_run_is_still_rejected(self):
        """即使帶了 rerun_of，若沒有 authorized_rerun=True 仍必須被拒絕。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            first = manager.create_run("SOL 近期走勢？", ["SOL"], mode="formal", run_id="run-formal-x")

            with self.assertRaises(FormalRunAlreadyExistsError):
                manager.create_run(
                    "SOL 近期走勢？", ["SOL"], mode="formal", run_id="run-formal-y",
                    rerun_of=first.run_id, rerun_reason="沒有授權就想重跑",
                    authorized_rerun=False,
                )

    def test_first_failed_lock_entry_is_not_deleted_after_authorized_rerun(self):
        """第一次失敗紀錄不可被刪除：authorized rerun 之後，第一筆紀錄仍保留在歷史裡。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            first = manager.create_run("XRP 近期走勢？", ["XRP"], mode="formal", run_id="run-formal-fail")
            manager.transition(first, STATUS_RUNNING)
            manager.transition(first, STATUS_FAILED)

            manager.create_run(
                "XRP 近期走勢？", ["XRP"], mode="formal", run_id="run-formal-fixed",
                rerun_of=first.run_id, rerun_reason="第一次因 Bedrock 逾時失敗", authorized_rerun=True,
            )

            history = manager.formal_lock_history(first.question_hash)
            self.assertEqual(history[0]["run_id"], first.run_id)
            self.assertEqual(history[0]["status"], STATUS_FAILED)

    def test_existing_run_output_is_not_overwritten(self):
        """既有輸出目錄有內容時，第二次以相同 run_id 建立 run 必須被拒絕。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            record = manager.create_run("ETH 近期走勢？", ["ETH"], mode="test", run_id="run-collide")
            store = manager.artifact_store(record)
            store.write_text("report.md", "已經有報告了")

            with self.assertRaises(RunDirectoryExistsError):
                manager.create_run("ETH 近期走勢？", ["ETH"], mode="test", run_id="run-collide")

    def test_question_hash_normalises_coin_order_and_case(self):
        """question_hash 對幣種大小寫與順序不敏感，避免同一組合被誤判為不同 formal run。"""
        hash_a = compute_question_hash("問題", ["eth", "BTC"])
        hash_b = compute_question_hash("問題", ["btc", "ETH"])
        hash_c = compute_question_hash("問題", ["ETH", "SOL"])

        self.assertEqual(hash_a, hash_b)
        self.assertNotEqual(hash_a, hash_c)


class StatusTransitionTests(unittest.TestCase):
    def test_valid_lifecycle_transitions(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            record = manager.create_run("問題", ["ETH"], mode="test", run_id="run-lifecycle")

            self.assertEqual(record.status, STATUS_CREATED)
            manager.transition(record, STATUS_RUNNING)
            self.assertEqual(record.status, STATUS_RUNNING)
            manager.transition(record, STATUS_COMPLETED)
            self.assertEqual(record.status, STATUS_COMPLETED)

    def test_running_can_end_in_completed_degraded(self):
        """單一 domain 缺失但仍有可用報告時，應能轉為 COMPLETED_DEGRADED 而非 FAILED。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            record = manager.create_run("問題", ["ETH"], mode="test", run_id="run-degraded")
            manager.transition(record, STATUS_RUNNING)
            manager.transition(record, STATUS_COMPLETED_DEGRADED)
            self.assertEqual(record.status, STATUS_COMPLETED_DEGRADED)

    def test_invalid_transition_from_created_directly_to_completed_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            record = manager.create_run("問題", ["ETH"], mode="test", run_id="run-invalid-1")

            with self.assertRaises(InvalidRunTransitionError):
                manager.transition(record, STATUS_COMPLETED)
            self.assertEqual(record.status, STATUS_CREATED)

    def test_terminal_status_cannot_transition_again(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            record = manager.create_run("問題", ["ETH"], mode="test", run_id="run-invalid-2")
            manager.transition(record, STATUS_RUNNING)
            manager.transition(record, STATUS_COMPLETED)

            with self.assertRaises(InvalidRunTransitionError):
                manager.transition(record, STATUS_RUNNING)
            self.assertEqual(record.status, STATUS_COMPLETED)

    def test_unknown_status_name_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            record = manager.create_run("問題", ["ETH"], mode="test", run_id="run-invalid-3")

            with self.assertRaises(ValueError):
                manager.transition(record, "NOT_A_REAL_STATUS")


class DeadlineDecisionTests(unittest.TestCase):
    def test_defaults_do_not_relax_the_existing_720_second_budget(self):
        """預設 hard deadline 不得放寬既有的三階段預算（720 秒）。"""
        self.assertEqual(DEFAULT_HARD_DEADLINE_SECONDS, DEFAULT_TIME_BUDGET_SECONDS)
        self.assertLessEqual(DEFAULT_HARD_DEADLINE_SECONDS, HARD_DEADLINE_SECONDS_MAX)
        self.assertLessEqual(DEFAULT_FINALIZATION_DEADLINE_SECONDS, FINALIZATION_DEADLINE_SECONDS_MAX)
        self.assertLessEqual(DEFAULT_FINALIZATION_DEADLINE_SECONDS, DEFAULT_HARD_DEADLINE_SECONDS)

    def test_hard_deadline_cannot_exceed_competition_ceiling(self):
        with self.assertRaises(ValueError):
            evaluate_deadline(elapsed_seconds=0, hard_deadline_seconds=901)

    def test_finalization_deadline_cannot_exceed_competition_ceiling(self):
        with self.assertRaises(ValueError):
            evaluate_deadline(elapsed_seconds=0, hard_deadline_seconds=900, finalization_deadline_seconds=841)

    def test_finalization_deadline_cannot_exceed_hard_deadline(self):
        with self.assertRaises(ValueError):
            evaluate_deadline(elapsed_seconds=0, hard_deadline_seconds=100, finalization_deadline_seconds=200)

    def test_well_within_budget_does_not_trigger_finalization(self):
        decision = evaluate_deadline(elapsed_seconds=10, hard_deadline_seconds=720)
        self.assertFalse(decision.should_finalize)
        self.assertFalse(decision.should_skip_follow_up_collection)
        self.assertFalse(decision.should_skip_critic)
        self.assertEqual(decision.reason, "within_budget")
        self.assertAlmostEqual(decision.remaining_seconds, 710)

    def test_approaching_finalization_window_triggers_degradation_signals(self):
        """接近 finalization deadline 時，應標示要跳過 follow-up collection 與 Critic。"""
        decision = evaluate_deadline(
            elapsed_seconds=700, hard_deadline_seconds=720, finalization_deadline_seconds=672,
        )
        self.assertTrue(decision.should_finalize)
        self.assertTrue(decision.should_skip_follow_up_collection)
        self.assertTrue(decision.should_skip_critic)
        self.assertEqual(decision.reason, "finalization_window_reached")

    def test_exceeding_hard_deadline_reports_zero_remaining(self):
        decision = evaluate_deadline(elapsed_seconds=999, hard_deadline_seconds=720)
        self.assertEqual(decision.remaining_seconds, 0.0)
        self.assertEqual(decision.reason, "hard_deadline_exceeded")
        self.assertTrue(decision.should_finalize)

    def test_decision_is_deterministic_for_the_same_input(self):
        """相同輸入必須永遠得到相同決策；不得依賴系統時間或亂數。"""
        first = evaluate_deadline(elapsed_seconds=680, hard_deadline_seconds=720)
        second = evaluate_deadline(elapsed_seconds=680, hard_deadline_seconds=720)
        self.assertEqual(first.as_dict(), second.as_dict())

    def test_negative_elapsed_seconds_is_rejected(self):
        with self.assertRaises(ValueError):
            evaluate_deadline(elapsed_seconds=-1)


class CheckpointTests(unittest.TestCase):
    def test_checkpoints_accumulate_without_deleting_earlier_stages(self):
        """後續階段的 checkpoint 不會清除前面已保存的階段。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            record = manager.create_run("問題", ["ETH"], mode="test", run_id="run-checkpoint")

            record.save_checkpoint("plan", {"steps": ["collect", "analyse"]})
            record.save_checkpoint("collected_evidence", {"count": 3})

            self.assertIn("plan", record.checkpoints)
            self.assertIn("collected_evidence", record.checkpoints)

    def test_unknown_checkpoint_stage_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            record = manager.create_run("問題", ["ETH"], mode="test", run_id="run-checkpoint-2")

            with self.assertRaises(ValueError):
                record.save_checkpoint("not_a_real_stage", {})

    def test_write_checkpoints_persists_through_the_artifact_store(self):
        """checkpoint 落地時透過 LocalArtifactStore 寫入 run 自己的輸出前綴之下。"""
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            record = manager.create_run("問題", ["ETH"], mode="test", run_id="run-checkpoint-3")
            record.save_checkpoint("plan", {"steps": []})
            manager.transition(record, STATUS_RUNNING)

            manager.write_checkpoints(record)

            checkpoint_path = Path(directory) / record.context.output_prefix / "_checkpoint.json"
            self.assertTrue(checkpoint_path.exists())
            payload = json.loads(checkpoint_path.read_text(encoding="utf-8"))
            self.assertEqual(payload["run_id"], record.run_id)
            self.assertEqual(payload["status"], STATUS_RUNNING)
            self.assertIn("plan", payload["stages"])


class RunManagerConstructionValidationTests(unittest.TestCase):
    def test_unknown_mode_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            with self.assertRaises(ValueError):
                manager.create_run("問題", ["ETH"], mode="not-a-real-mode")

    def test_authorized_rerun_without_rerun_of_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            with self.assertRaises(ValueError):
                manager.create_run("問題", ["ETH"], mode="test", authorized_rerun=True)

    def test_rerun_without_reason_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            manager = RunManager(directory)
            first = manager.create_run("問題", ["ETH"], mode="test", run_id="run-need-reason")
            with self.assertRaises(ValueError):
                manager.create_run(
                    "問題", ["ETH"], mode="test", run_id="run-need-reason-2",
                    rerun_of=first.run_id, authorized_rerun=True,
                )


if __name__ == "__main__":
    unittest.main()
