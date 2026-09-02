from __future__ import annotations

import json
import os
import tempfile
import threading
import unittest
from http.cookiejar import CookieJar
from http.server import ThreadingHTTPServer
from pathlib import Path
from urllib.parse import quote
from urllib.error import HTTPError
from urllib.request import HTTPCookieProcessor, Request, build_opener, urlopen
from unittest.mock import patch

import server
from tests.test_answer_checker import CORRECT_SOLUTION
from tests.test_compiler import VALID_PROGRAM
from trace_store import TraceStore


class ServerApiTests(unittest.TestCase):
    ADMIN_TOKEN = "test-admin-token-that-is-long-enough"

    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.env_patcher = patch.dict(os.environ, {"TRACE_ADMIN_TOKEN": self.ADMIN_TOKEN})
        self.env_patcher.start()
        server._trace_store = TraceStore(Path(self.temp_dir.name))
        self.httpd = ThreadingHTTPServer(("127.0.0.1", 0), server.KnitScriptHandler)
        self.thread = threading.Thread(target=self.httpd.serve_forever, daemon=True)
        self.thread.start()
        self.base_url = f"http://127.0.0.1:{self.httpd.server_port}"
        self.admin_opener = build_opener(HTTPCookieProcessor(CookieJar()))

    def tearDown(self) -> None:
        self.httpd.shutdown()
        self.httpd.server_close()
        self.thread.join()
        server._trace_store = None
        self.env_patcher.stop()
        self.temp_dir.cleanup()

    def post_json(self, path: str, payload: dict[str, object]) -> tuple[int, dict[str, object]]:
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8"),
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        try:
            with urlopen(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def get_text(self, path: str) -> tuple[int, str]:
        with urlopen(f"{self.base_url}{path}") as response:
            return response.status, response.read().decode("utf-8")

    def admin_request(self, path: str, payload: dict[str, object] | None = None) -> tuple[int, dict[str, object]]:
        request = Request(
            f"{self.base_url}{path}",
            data=json.dumps(payload).encode("utf-8") if payload is not None else None,
            headers={"Content-Type": "application/json"} if payload is not None else {},
            method="POST" if payload is not None else "GET",
        )
        try:
            with self.admin_opener.open(request) as response:
                return response.status, json.loads(response.read())
        except HTTPError as error:
            return error.code, json.loads(error.read())

    def test_studio_includes_a_tutorial_panel_and_official_documentation_link(self) -> None:
        status, html = self.get_text("/")

        self.assertEqual(200, status)
        self.assertIn('id="tutorialGuide"', html)
        self.assertIn('data-guide-tab="tutorial"', html)
        self.assertIn("https://mhofmann-khoury.github.io/knit_script/", html)
        self.assertIn("Open full documentation", html)

    def test_studio_starts_with_an_empty_editor(self) -> None:
        html_status, html = self.get_text("/")
        script_status, script = self.get_text("/app.js")

        self.assertEqual(200, html_status)
        self.assertEqual(200, script_status)
        self.assertIn("Write your KnitScript program from scratch", html)
        self.assertIn("Clear editor", html)
        self.assertIn('const STARTER_SOURCE = "";', script)
        self.assertNotIn("TODO: cast on", script)

    def test_direct_study_access_requires_a_prolific_participant_id(self) -> None:
        html_status, html = self.get_text("/")
        script_status, script = self.get_text("/app.js")

        self.assertEqual(200, html_status)
        self.assertEqual(200, script_status)
        self.assertIn('id="participantDialog"', html)
        self.assertIn('id="participantIdInput"', html)
        self.assertIn('queryParameters.get("preview") === "1"', script)
        self.assertIn('prolificRecruitment.source = "prolific_manual"', script)
        self.assertIn('participantDialog.showModal()', script)
        self.assertIn('history.replaceState', script)

    def test_admin_page_is_available_but_trace_api_requires_login(self) -> None:
        page_status, html = self.get_text("/admin")
        api_status, payload = self.admin_request("/api/admin/sessions")

        self.assertEqual(200, page_status)
        self.assertIn("Programming Trace Dashboard", html)
        self.assertEqual(401, api_status)
        self.assertFalse(payload["ok"])

    def test_admin_can_login_and_browse_session_events_and_files(self) -> None:
        _status, session_result = self.post_json(
            "/api/sessions",
            {
                "participant_id": "PID-ADMIN-TEST",
                "task_id": "stockinette-swatch-v1",
                "initial_source": "",
                "recruitment": {"source": "prolific", "prolific_pid": "PID-ADMIN-TEST"},
            },
        )
        session_id = session_result["session"]["session_id"]
        self.post_json(
            "/api/events",
            {
                "session_id": session_id,
                "batch_id": "batch-admin",
                "events": [
                    {
                        "seq": 1,
                        "type": "guide.task_viewed",
                        "client_timestamp": "2026-09-01T00:00:00Z",
                        "elapsed_ms": 1,
                        "payload": {},
                    }
                ],
            },
        )

        login_status, _login = self.admin_request("/api/admin/login", {"token": self.ADMIN_TOKEN})
        list_status, sessions = self.admin_request("/api/admin/sessions")
        detail_status, detail = self.admin_request(f"/api/admin/sessions/{session_id}")
        events_status, events = self.admin_request(f"/api/admin/sessions/{session_id}/events?limit=10")
        files_status, files = self.admin_request(f"/api/admin/sessions/{session_id}/files?limit=10")
        file_status, file_payload = self.admin_request(
            f"/api/admin/sessions/{session_id}/file?path={quote('manifest.json')}"
        )

        self.assertEqual(200, login_status)
        self.assertEqual(200, list_status)
        self.assertEqual(1, sessions["summary"]["session_count"])
        self.assertEqual(200, detail_status)
        self.assertEqual(session_id, detail["manifest"]["session_id"])
        self.assertEqual(200, events_status)
        self.assertEqual("guide.task_viewed", events["events"][0]["type"])
        self.assertEqual(200, files_status)
        self.assertGreater(files["total"], 0)
        self.assertEqual(200, file_status)
        self.assertIn(session_id, file_payload["content"])

    def test_admin_file_api_rejects_path_traversal(self) -> None:
        _status, session_result = self.post_json(
            "/api/sessions",
            {"participant_id": "PID-PATH", "task_id": "stockinette-swatch-v1", "initial_source": ""},
        )
        session_id = session_result["session"]["session_id"]
        self.admin_request("/api/admin/login", {"token": self.ADMIN_TOKEN})

        status, payload = self.admin_request(
            f"/api/admin/sessions/{session_id}/file?path={quote('../secret.txt')}"
        )

        self.assertEqual(400, status)
        self.assertFalse(payload["ok"])

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

    def test_submission_rejects_a_missing_source_as_a_bad_request(self) -> None:
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
            {"session_id": session_result["session"]["session_id"]},
        )

        self.assertEqual(400, status)
        self.assertFalse(result["ok"])


if __name__ == "__main__":
    unittest.main()
