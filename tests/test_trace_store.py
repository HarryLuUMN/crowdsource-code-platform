from __future__ import annotations

import json
import tempfile
import unittest
from pathlib import Path

from trace_store import TraceStore


class TraceStoreTests(unittest.TestCase):
    def test_session_preserves_prolific_recruitment_identifiers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TraceStore(Path(temp_dir))

            session = store.create_session(
                participant_id="participant-test",
                task_id="stockinette-swatch-v1",
                recruitment={
                    "source": "prolific",
                    "prolific_pid": "PID123",
                    "study_id": "STUDY123",
                    "prolific_session_id": "SESSION123",
                },
            )

            manifest = json.loads(
                (Path(temp_dir) / session["session_id"] / "manifest.json").read_text(encoding="utf-8")
            )
            self.assertEqual("prolific", manifest["recruitment"]["source"])
            self.assertEqual("PID123", manifest["recruitment"]["prolific_pid"])
            self.assertEqual("SESSION123", manifest["recruitment"]["prolific_session_id"])

    def test_session_event_batch_is_persisted_as_jsonl(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TraceStore(Path(temp_dir))
            session = store.create_session(
                participant_id="participant-test",
                task_id="playground",
                client={"locale": "en-US"},
            )

            result = store.append_event_batch(
                session["session_id"],
                "batch-0001",
                [
                    {
                        "client_event_id": "client:1",
                        "seq": 1,
                        "type": "editor.edit",
                        "client_timestamp": "2026-08-26T20:00:00Z",
                        "elapsed_ms": 123,
                        "payload": {"operation": "insert", "text": "width = 10;"},
                    }
                ],
            )

            session_dir = Path(temp_dir) / session["session_id"]
            manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
            event_lines = (session_dir / result["object_key"]).read_text(encoding="utf-8").splitlines()

            self.assertEqual("active", manifest["status"])
            self.assertEqual(1, manifest["event_count"])
            self.assertEqual(1, manifest["last_seq"])
            self.assertEqual(1, len(event_lines))
            self.assertEqual("editor.edit", json.loads(event_lines[0])["type"])

    def test_compile_execution_bundle_preserves_source_and_artifacts(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TraceStore(Path(temp_dir))
            session = store.create_session("participant-test", "playground")

            execution = store.record_execution(
                session_id=session["session_id"],
                source="width = 10;",
                result={
                    "ok": True,
                    "duration_ms": 84,
                    "knitout": ";!knitout-2\nknit + f1 1\n",
                    "stdout": "compiled\n",
                    "stderr": "",
                    "metrics": {"loops": 1, "stitches": 0, "courses": 1},
                },
                requested_at="2026-08-26T20:00:00Z",
            )

            execution_dir = Path(temp_dir) / session["session_id"] / "executions" / execution["execution_id"]
            stored_result = json.loads((execution_dir / "result.json").read_text(encoding="utf-8"))

            self.assertEqual("succeeded", stored_result["status"])
            self.assertEqual("source.ks", stored_result["artifacts"]["source"])
            self.assertEqual("output.k", stored_result["artifacts"]["knitout"])
            self.assertEqual("width = 10;", (execution_dir / "source.ks").read_text(encoding="utf-8"))
            self.assertEqual(";!knitout-2\nknit + f1 1\n", (execution_dir / "output.k").read_text(encoding="utf-8"))
            self.assertNotIn("knitout", stored_result)

    def test_retried_event_batch_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TraceStore(Path(temp_dir))
            session = store.create_session("participant-test", "playground")
            events = [{"seq": 1, "type": "editor.edit", "payload": {"inserted_text": "x"}}]

            first = store.append_event_batch(session["session_id"], "batch-0001", events)
            retry = store.append_event_batch(session["session_id"], "batch-0001", events)

            manifest_path = Path(temp_dir) / session["session_id"] / "manifest.json"
            manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(1, first["accepted"])
            self.assertEqual(0, retry["accepted"])
            self.assertTrue(retry["duplicate"])
            self.assertEqual(1, manifest["event_count"])

    def test_overlapping_retry_accepts_only_the_unseen_suffix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TraceStore(Path(temp_dir))
            session = store.create_session("participant-test", "playground", initial_source="")
            first_events = [
                {"client_event_id": "client:1", "seq": 1, "type": "session.started", "payload": {}},
                {
                    "client_event_id": "client:2",
                    "seq": 2,
                    "type": "editor.edit",
                    "payload": {
                        "operation": "insert",
                        "range_start": 0,
                        "range_end": 0,
                        "inserted_text": "a",
                        "deleted_text": "",
                        "source_length_after": 1,
                    },
                },
            ]
            store.append_event_batch(session["session_id"], "batch-0001", first_events)

            retry = store.append_event_batch(
                session["session_id"],
                "batch-0002",
                [
                    first_events[1],
                    {"client_event_id": "client:3", "seq": 3, "type": "run.requested", "payload": {}},
                ],
            )

            session_dir = Path(temp_dir) / session["session_id"]
            manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(1, retry["accepted"])
            self.assertEqual(1, retry["duplicate_count"])
            self.assertEqual(3, manifest["event_count"])
            self.assertEqual(3, manifest["last_seq"])

    def test_same_batch_id_can_extend_a_previously_stored_prefix(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TraceStore(Path(temp_dir))
            session = store.create_session("participant-test", "playground")
            first_event = {"client_event_id": "client:1", "seq": 1, "type": "session.started", "payload": {}}
            store.append_event_batch(session["session_id"], "batch-0001", [first_event])

            retry = store.append_event_batch(
                session["session_id"],
                "batch-0001",
                [
                    first_event,
                    {"client_event_id": "client:2", "seq": 2, "type": "run.requested", "payload": {}},
                ],
            )

            session_dir = Path(temp_dir) / session["session_id"]
            batch_lines = (session_dir / "events" / "batch-0001.jsonl").read_text().splitlines()
            manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertEqual(1, retry["accepted"])
            self.assertEqual(1, retry["duplicate_count"])
            self.assertEqual(2, len(batch_lines))
            self.assertEqual(2, manifest["event_count"])
            self.assertEqual(2, manifest["last_seq"])

    def test_conflicting_retry_is_rejected(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TraceStore(Path(temp_dir))
            session = store.create_session("participant-test", "playground")
            store.append_event_batch(
                session["session_id"],
                "batch-0001",
                [{"client_event_id": "client:1", "seq": 1, "type": "page.visible", "payload": {}}],
            )

            with self.assertRaisesRegex(ValueError, "conflicts with stored data"):
                store.append_event_batch(
                    session["session_id"],
                    "batch-0002",
                    [{"client_event_id": "client:other", "seq": 1, "type": "page.hidden", "payload": {}}],
                )

    def test_delta_observations_preserve_full_code_snapshots_and_action_markers(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TraceStore(Path(temp_dir))
            session = store.create_session("participant-test", "playground", initial_source="")
            events = [
                {"client_event_id": "client:1", "seq": 1, "type": "session.started", "payload": {}},
                {
                    "client_event_id": "client:2",
                    "seq": 2,
                    "type": "editor.edit",
                    "client_timestamp": "2026-09-01T13:00:00Z",
                    "elapsed_ms": 100,
                    "payload": {
                        "operation": "insert",
                        "range_start": 0,
                        "range_end": 0,
                        "inserted_text": "a😀",
                        "deleted_text": "",
                        "source_length_after": 3,
                    },
                },
                {
                    "client_event_id": "client:3",
                    "seq": 3,
                    "type": "editor.edit",
                    "payload": {
                        "operation": "insert",
                        "range_start": 3,
                        "range_end": 3,
                        "inserted_text": "b",
                        "deleted_text": "",
                        "source_length_after": 4,
                    },
                },
                {"client_event_id": "client:4", "seq": 4, "type": "guide.tutorial_viewed", "payload": {}},
                {"client_event_id": "client:5", "seq": 5, "type": "run.requested", "payload": {}},
            ]

            store.append_event_batch(session["session_id"], "batch-0001", events)

            session_dir = Path(temp_dir) / session["session_id"]
            observations = session_dir / "delta-observations"
            manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
            labels = [json.loads(line) for line in (observations / "step_labels.jsonl").read_text().splitlines()]
            self.assertEqual("a😀", (observations / "delta-session1-step0.txt").read_text(encoding="utf-8"))
            self.assertEqual("a😀b", (observations / "delta-session1-step1.txt").read_text(encoding="utf-8"))
            self.assertEqual(
                "READ_DOCUMENTATION\n",
                (observations / "delta-session1-step2.txt").read_text(encoding="utf-8"),
            )
            self.assertEqual("BROWSER_TESTING\n", (observations / "delta-session1-step3.txt").read_text())
            self.assertEqual(
                ["INSERT_CODE", "INSERT_CODE", "READ_DOC", "BROWSER_TEST"],
                [row["primaryLabel"] for row in labels],
            )
            self.assertEqual(4, manifest["observation_count"])
            self.assertEqual("delta-observations-v1", manifest["observation_format"])
            code_state_path = session_dir / "code" / f"{manifest['last_code_state_id'][7:]}.ks"
            self.assertTrue(code_state_path.is_file())

    def test_out_of_order_batches_repair_the_contiguous_sequence(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TraceStore(Path(temp_dir))
            session = store.create_session("participant-test", "playground")

            store.append_event_batch(session["session_id"], "batch-0002", [{"seq": 2, "type": "page.hidden"}])
            manifest_path = Path(temp_dir) / session["session_id"] / "manifest.json"
            manifest_with_gap = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(1, manifest_with_gap["event_count"])
            self.assertEqual(0, manifest_with_gap["last_seq"])

            store.append_event_batch(session["session_id"], "batch-0001", [{"seq": 1, "type": "session.started"}])
            repaired_manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            self.assertEqual(2, repaired_manifest["event_count"])
            self.assertEqual(2, repaired_manifest["last_seq"])

    def test_execution_result_preserves_additional_error_context(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TraceStore(Path(temp_dir))
            session = store.create_session("participant-test", "playground")

            execution = store.record_execution(
                session_id=session["session_id"],
                source="bad code",
                result={
                    "ok": False,
                    "error": {"type": "RunnerError", "message": "worker stopped"},
                    "details": "full runner traceback",
                    "stdout": "",
                    "stderr": "fatal output",
                },
                requested_at="2026-08-26T20:00:00Z",
            )

            result_path = Path(temp_dir) / session["session_id"] / "executions" / execution["execution_id"] / "result.json"
            stored_result = json.loads(result_path.read_text(encoding="utf-8"))
            self.assertEqual("full runner traceback", stored_result["compiler_result"]["details"])

    def test_passing_submission_is_persisted_and_marks_the_session(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TraceStore(Path(temp_dir))
            session = store.create_session("participant-test", "stockinette-swatch-v1")

            submission = store.record_submission(
                session_id=session["session_id"],
                source="completed source",
                check={"passed": True, "passed_count": 5, "total_count": 5, "tests": []},
                execution_id="execution-test",
            )

            session_dir = Path(temp_dir) / session["session_id"]
            stored_submission = json.loads(
                (session_dir / "submissions" / f"{submission['submission_id']}.json").read_text(encoding="utf-8")
            )
            manifest = json.loads((session_dir / "manifest.json").read_text(encoding="utf-8"))
            self.assertTrue(stored_submission["passed"])
            self.assertEqual("execution-test", stored_submission["execution_id"])
            self.assertEqual(1, manifest["submission_count"])
            self.assertEqual(submission["submission_id"], manifest["passed_submission_id"])
            self.assertIsNotNone(manifest["passed_at"])

    def test_ending_a_session_finalizes_its_manifest(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            store = TraceStore(Path(temp_dir))
            session = store.create_session("participant-test", "playground")

            manifest = store.end_session(session["session_id"], final_source="final code")

            self.assertEqual("completed", manifest["status"])
            self.assertIsNotNone(manifest["ended_at"])
            self.assertEqual(64 + len("sha256:"), len(manifest["final_code_state_id"]))
            final_path = Path(temp_dir) / session["session_id"] / "code" / "source-final.ks"
            self.assertEqual("final code", final_path.read_text(encoding="utf-8"))


if __name__ == "__main__":
    unittest.main()
