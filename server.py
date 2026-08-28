"""Small local web server for the KnitScript Studio MVP."""

from __future__ import annotations

import json
import os
import subprocess
import sys
import tempfile
import threading
import uuid
from http import HTTPStatus
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any

from trace_store import TraceStore, utc_now

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
WORKER = ROOT / "compiler_worker.py"
MAX_REQUEST_BYTES = 1024 * 1024
MAX_SOURCE_CHARS = 100_000
COMPILE_TIMEOUT_SECONDS = 25
MAX_CONCURRENT_COMPILES = 2
TRACE_STORAGE_DIR = Path(os.environ.get("TRACE_STORAGE_DIR", ROOT / "data" / "traces"))
_compile_slots = threading.BoundedSemaphore(MAX_CONCURRENT_COMPILES)
_trace_store: TraceStore | None = None
_trace_store_lock = threading.Lock()


def get_trace_store() -> TraceStore:
    global _trace_store
    with _trace_store_lock:
        if _trace_store is None:
            _trace_store = TraceStore(TRACE_STORAGE_DIR)
        return _trace_store


def compile_source(source: str) -> tuple[int, dict[str, Any]]:
    if not isinstance(source, str):
        return HTTPStatus.BAD_REQUEST, {"ok": False, "error": {"message": "source must be a string"}}
    if not source.strip():
        return HTTPStatus.BAD_REQUEST, {"ok": False, "error": {"message": "Write some KnitScript before running."}}
    if len(source) > MAX_SOURCE_CHARS:
        return HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {
            "ok": False,
            "error": {"message": f"Source is limited to {MAX_SOURCE_CHARS:,} characters."},
        }
    if not _compile_slots.acquire(blocking=False):
        return HTTPStatus.TOO_MANY_REQUESTS, {
            "ok": False,
            "error": {"message": "The compiler is busy. Try again in a moment."},
        }

    try:
        with tempfile.TemporaryDirectory(prefix="knitscript-run-") as work_dir:
            completed = subprocess.run(
                [sys.executable, str(WORKER)],
                input=source,
                text=True,
                cwd=work_dir,
                capture_output=True,
                timeout=COMPILE_TIMEOUT_SECONDS,
                env={
                    "PATH": os.environ.get("PATH", ""),
                    "PYTHONIOENCODING": "utf-8",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
                check=False,
            )
    except subprocess.TimeoutExpired:
        return HTTPStatus.REQUEST_TIMEOUT, {
            "ok": False,
            "error": {"type": "Timeout", "message": f"Execution exceeded {COMPILE_TIMEOUT_SECONDS} seconds."},
        }
    finally:
        _compile_slots.release()

    if completed.returncode != 0:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {
            "ok": False,
            "error": {"type": "RunnerError", "message": "The compiler worker stopped unexpectedly."},
            "details": completed.stderr[-4000:],
        }

    try:
        payload = json.loads(completed.stdout)
    except json.JSONDecodeError:
        return HTTPStatus.INTERNAL_SERVER_ERROR, {
            "ok": False,
            "error": {"type": "RunnerError", "message": "The compiler returned an unreadable response."},
        }
    payload["exit_code"] = completed.returncode
    return HTTPStatus.OK, payload


class KnitScriptHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def _send_json(self, status: int, payload: dict[str, Any]) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(body)

    def _read_json_request(self) -> dict[str, Any] | None:
        try:
            content_length = int(self.headers.get("Content-Length", "0"))
        except ValueError:
            content_length = 0
        if content_length <= 0 or content_length > MAX_REQUEST_BYTES:
            self._send_json(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"ok": False, "error": {"message": "Invalid request size."}})
            return None
        try:
            request = json.loads(self.rfile.read(content_length))
        except (UnicodeDecodeError, json.JSONDecodeError):
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": {"message": "Invalid JSON request."}})
            return None
        if not isinstance(request, dict):
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": {"message": "JSON body must be an object."}})
            return None
        return request

    def do_GET(self) -> None:
        if self.path == "/api/health":
            self._send_json(HTTPStatus.OK, {"ok": True, "compiler": "knit-script", "version": "0.2.1"})
            return
        super().do_GET()

    def do_POST(self) -> None:
        supported_paths = {"/api/run", "/api/sessions", "/api/events", "/api/sessions/end"}
        if self.path not in supported_paths:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": {"message": "Not found"}})
            return
        request = self._read_json_request()
        if request is None:
            return

        try:
            if self.path == "/api/sessions":
                manifest = get_trace_store().create_session(
                    participant_id=request.get("participant_id"),
                    task_id=request.get("task_id", "playground"),
                    client=request.get("client"),
                    initial_source=request.get("initial_source"),
                )
                self._send_json(HTTPStatus.CREATED, {"ok": True, "session": manifest})
                return

            if self.path == "/api/events":
                batch = get_trace_store().append_event_batch(
                    session_id=request.get("session_id"),
                    batch_id=request.get("batch_id"),
                    events=request.get("events"),
                )
                self._send_json(HTTPStatus.OK, {"ok": True, **batch})
                return

            if self.path == "/api/sessions/end":
                event_batches = request.get("event_batches")
                if event_batches is not None:
                    if not isinstance(event_batches, list):
                        raise ValueError("event_batches must be an array")
                    for batch in event_batches:
                        if not isinstance(batch, dict):
                            raise ValueError("Every event batch must be an object")
                        get_trace_store().append_event_batch(
                            session_id=request.get("session_id"),
                            batch_id=batch.get("batch_id"),
                            events=batch.get("events"),
                        )
                else:
                    events = request.get("events")
                    if events:
                        get_trace_store().append_event_batch(
                            session_id=request.get("session_id"),
                            batch_id=request.get("batch_id"),
                            events=events,
                        )
                manifest = get_trace_store().end_session(request.get("session_id"), request.get("final_source", ""))
                self._send_json(HTTPStatus.OK, {"ok": True, "session": manifest})
                return

            source = request.get("source")
            requested_at = utc_now()
            execution_id = str(uuid.uuid4())
            status, result = compile_source(source)
            session_id = request.get("session_id")
            if session_id and isinstance(source, str):
                try:
                    stored = get_trace_store().record_execution(
                        session_id=session_id,
                        source=source,
                        result=result,
                        requested_at=requested_at,
                        execution_id=execution_id,
                    )
                except (KeyError, OSError, ValueError) as storage_error:
                    result["trace_saved"] = False
                    result["trace_error"] = {
                        "type": type(storage_error).__name__,
                        "message": str(storage_error),
                    }
                else:
                    result["trace_saved"] = True
                    result["execution_id"] = stored["execution_id"]
                    result["code_state_id"] = stored["code_state_id"]
            self._send_json(status, result)
        except ValueError as error:
            self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": {"message": str(error)}})
        except KeyError as error:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": {"message": str(error)}})
        except OSError as error:
            self._send_json(
                HTTPStatus.INTERNAL_SERVER_ERROR,
                {"ok": False, "error": {"type": "StorageError", "message": f"Could not save trace data: {error}"}},
            )

    def log_message(self, format: str, *args: Any) -> None:
        print(f"[web] {self.address_string()} — {format % args}")


def main() -> None:
    host = os.environ.get("HOST", "127.0.0.1")
    port = int(os.environ.get("PORT", "8000"))
    server = ThreadingHTTPServer((host, port), KnitScriptHandler)
    print(f"KnitScript Studio is running at http://{host}:{port}")
    print("Press Ctrl+C to stop.")
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        print("\nStopping KnitScript Studio.")
    finally:
        server.server_close()


if __name__ == "__main__":
    main()
