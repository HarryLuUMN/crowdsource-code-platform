from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.request import Request, urlopen
from unittest.mock import patch

import server
from tests.test_answer_checker import CORRECT_SOLUTION
from tests.test_compiler import VALID_PROGRAM
from trace_store import TraceStore


class ServerApiTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        server._trace_store = TraceStore(Path(self.temp_dir.name))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.KnitScriptHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}"

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        server._trace_store = None
        self.temp_dir.cleanup()

    def post_json(self, path: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urlopen(request) as response:
            return response.status, json.loads(response.read())

    def test_correct_submission_is_rechecked_saved_and_given_completion_url(self) -> None:
        _status, session_result = self.post_json(
            "/api/sessions",
            {
                "participant_id": "PID123",
                "task_id": "stockinette-swatch-v1",
                "initial_source": "starter",
                "recruitment": {
                    "source": "prolific",
                    "prolific_pid": "PID123",
                    "study_id": "STUDY123",
                    "prolific_session_id": "SESSION123",
                },
            },
        )
        session_id = session_result["session"]["session_id"]

        with patch.dict(
            os.environ,
            {"PROLIFIC_COMPLETION_URL": "https://app.prolific.com/submissions/complete?cc=ABC123"},
        ):
            status, result = self.post_json(
                "/api/submit",
                {"session_id": session_id, "source": CORRECT_SOLUTION},
            )

        self.assertEqual(200, status)
        self.assertTrue(result["check"]["passed"])
        self.assertTrue(result["submission"]["passed"])
        self.assertEqual(
            "https://app.prolific.com/submissions/complete?cc=ABC123",
            result["completion_url"],
        )
        submission_path = (
            Path(self.temp_dir.name)
            / session_id
            / "submissions"
            / f"{result['submission']['submission_id']}.json"
        )
        self.assertTrue(submission_path.is_file())

    def test_incorrect_submission_is_saved_without_a_completion_url(self) -> None:
        _status, session_result = self.post_json(
            "/api/sessions",
            {
                "participant_id": "participant-test",
                "task_id": "stockinette-swatch-v1",
                "initial_source": "starter",
            },
        )

        status, result = self.post_json(
            "/api/submit",
            {"session_id": session_result["session"]["session_id"], "source": VALID_PROGRAM},
        )

        self.assertEqual(200, status)
        self.assertTrue(result["ok"])
        self.assertFalse(result["check"]["passed"])
        self.assertFalse(result["submission"]["passed"])
        self.assertNotIn("completion_url", result)


if __name__ == "__main__":
    unittest.main()
