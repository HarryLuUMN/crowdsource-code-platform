from __future__ import annotations

import json
import tempfile
import unittest
from datetime import datetime, timedelta
from pathlib import Path

from trace_admin import MAX_PREVIEW_BYTES, TraceAdminRepository
from trace_store import TraceStore, utc_now


class TraceAdminRepositoryTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.store = TraceStore(Path(self.temp_dir.name))
        self.repository = TraceAdminRepository(self.store)
        self.manifest = self.store.create_session(
            participant_id="participant-42",
            task_id="stockinette-swatch-v1",
            initial_source="",
            recruitment={
                "source": "prolific",
                "prolific_pid": "PROLIFIC-42",
                "study_id": "STUDY-1",
                "prolific_session_id": "PROLIFIC-SESSION-1",
            },
        )
        self.session_id = self.manifest["session_id"]

    def tearDown(self) -> None:
        self.temp_dir.cleanup()

    def test_lists_sessions_with_dashboard_summary(self) -> None:
        self.store.append_event_batch(
            self.session_id,
            "batch-1",
            [
                {
                    "seq": 1,
                    "type": "guide.task_viewed",
                    "client_timestamp": "2026-09-01T00:00:00Z",
                    "elapsed_ms": 2,
                    "payload": {},
                }
            ],
        )

        result = self.repository.list_sessions()

        self.assertEqual(1, result["summary"]["session_count"])
        self.assertEqual(1, result["summary"]["event_count"])
        self.assertEqual("PROLIFIC-42", result["sessions"][0]["prolific_pid"])

    def test_session_detail_includes_steps_compiles_submissions_and_files(self) -> None:
        self.store.append_event_batch(
            self.session_id,
            "batch-1",
            [
                {
                    "seq": 1,
                    "type": "editor.edit",
                    "client_timestamp": "2026-09-01T00:00:00Z",
                    "elapsed_ms": 12,
                    "payload": {
                        "range_start": 0,
                        "range_end": 0,
                        "inserted_text": "Width = 10;",
                        "deleted_text": "",
                        "source_length_after": 11,
                        "operation": "insert",
                    },
                },
                {
                    "seq": 2,
                    "type": "run.requested",
                    "client_timestamp": "2026-09-01T00:00:01Z",
                    "elapsed_ms": 1000,
                    "payload": {},
                },
            ],
        )
        execution = self.store.record_execution(
            self.session_id,
            "Width = 10;",
            {"ok": False, "error": {"type": "SyntaxError", "message": "Expected block"}, "stderr": "bad"},
            utc_now(),
        )
        self.store.record_submission(
            self.session_id,
            "Width = 10;",
            {"passed": False, "tests": []},
            execution["execution_id"],
        )

        detail = self.repository.get_session(self.session_id)

        self.assertEqual(2, len(detail["observations"]))
        self.assertEqual(1, len(detail["executions"]))
        self.assertEqual("compiler_error", detail["executions"][0]["status"])
        self.assertEqual(1, len(detail["submissions"]))
        self.assertIn("editor.edit", detail["event_type_counts"])
        files = self.repository.list_files(self.session_id, limit=10)
        self.assertIn("delta-observations/delta-session1-step0.txt", {item["path"] for item in files["files"]})

    def test_session_detail_recovers_missing_trace_tail_from_execution_checkpoints(self) -> None:
        self.store.append_event_batch(
            self.session_id,
            "batch-prefix",
            [
                {
                    "seq": 1,
                    "type": "editor.edit",
                    "client_timestamp": "2026-09-01T00:00:00Z",
                    "elapsed_ms": 100,
                    "payload": {
                        "range_start": 0,
                        "range_end": 0,
                        "inserted_text": "a",
                        "deleted_text": "",
                        "source_length_after": 1,
                        "operation": "insert",
                    },
                }
            ],
        )
        self.store.append_event_batch(
            self.session_id,
            "batch-after-gap",
            [
                {
                    "seq": 3,
                    "type": "page.hidden",
                    "client_timestamp": "2026-09-01T00:10:00Z",
                    "elapsed_ms": 600_000,
                    "payload": {},
                }
            ],
        )
        started_at = datetime.fromisoformat(self.manifest["started_at"].replace("Z", "+00:00"))
        first_execution = self.store.record_execution(
            self.session_id,
            "ab",
            {"ok": False, "error": {"type": "SyntaxError", "message": "Expected block"}},
            (started_at + timedelta(minutes=5)).isoformat(),
        )
        second_execution = self.store.record_execution(
            self.session_id,
            "abc",
            {"ok": True, "check": {"passed": True}, "knitout": ";!knitout-2\n"},
            (started_at + timedelta(minutes=6)).isoformat(),
        )

        detail = self.repository.get_session(self.session_id)

        self.assertTrue(detail["trace_integrity"]["recovery_needed"])
        self.assertEqual([{"start": 2, "end": 2}], detail["trace_integrity"]["missing_event_ranges"])
        self.assertEqual(2, detail["trace_integrity"]["recovered_checkpoint_count"])
        recovered = [
            step for step in detail["recovered_trajectory"] if step["provenance"] == "execution_checkpoint"
        ]
        self.assertEqual([first_execution["execution_id"], second_execution["execution_id"]], [step["executionId"] for step in recovered])
        self.assertEqual(["ab", "abc"], [
            self.repository.read_file(self.session_id, step["sourcePath"])["content"] for step in recovered
        ])
        self.assertTrue(all(step["primaryLabel"] == "RECOVERED_COMPILE_CHECKPOINT" for step in recovered))

    def test_session_detail_detects_a_missing_tail_without_a_sequence_gap(self) -> None:
        self.store.append_event_batch(
            self.session_id,
            "batch-prefix",
            [
                {
                    "seq": 1,
                    "type": "editor.edit",
                    "client_timestamp": "2026-09-01T00:00:00Z",
                    "elapsed_ms": 100,
                    "payload": {
                        "range_start": 0,
                        "range_end": 0,
                        "inserted_text": "a",
                        "deleted_text": "",
                        "source_length_after": 1,
                        "operation": "insert",
                    },
                }
            ],
        )
        manifest_path = Path(self.temp_dir.name) / self.session_id / "manifest.json"
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        self.store.record_execution(
            self.session_id,
            "finished source",
            {"ok": True, "check": {"passed": True}, "knitout": ";!knitout-2\n"},
            (datetime.fromisoformat(manifest["started_at"].replace("Z", "+00:00")) + timedelta(minutes=5)).isoformat(),
        )

        detail = self.repository.get_session(self.session_id)

        self.assertEqual([], detail["trace_integrity"]["missing_event_ranges"])
        self.assertTrue(detail["trace_integrity"]["tail_gap_detected"])
        self.assertTrue(detail["trace_integrity"]["recovery_needed"])
        self.assertEqual(1, detail["trace_integrity"]["recovered_checkpoint_count"])

    def test_file_listing_is_paginated(self) -> None:
        session_dir = Path(self.temp_dir.name) / self.session_id
        for index in range(4):
            (session_dir / f"extra-{index}.txt").write_text(str(index), encoding="utf-8")

        first_page = self.repository.list_files(self.session_id, limit=2)
        second_page = self.repository.list_files(self.session_id, limit=2, offset=2)

        self.assertEqual(2, len(first_page["files"]))
        self.assertTrue(first_page["has_more"])
        self.assertEqual(2, second_page["offset"])

    def test_read_file_is_bounded_and_rejects_traversal(self) -> None:
        session_dir = Path(self.temp_dir.name) / self.session_id
        large_path = session_dir / "large.txt"
        large_path.write_text("x" * (MAX_PREVIEW_BYTES + 50), encoding="utf-8")

        preview = self.repository.read_file(self.session_id, "large.txt")

        self.assertTrue(preview["truncated"])
        self.assertEqual(MAX_PREVIEW_BYTES, len(preview["content"]))
        with self.assertRaises(ValueError):
            self.repository.read_file(self.session_id, "../outside.txt")


if __name__ == "__main__":
    unittest.main()
