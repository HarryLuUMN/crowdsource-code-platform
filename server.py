"""Small local web server for the KnitScript Studio MVP."""

from __future__ import annotations

import json
import hashlib
import hmac
import os
import subprocess
import sys
import tempfile
import threading
import time
import uuid
from http import HTTPStatus
from http.cookies import CookieError, SimpleCookie
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
from typing import Any
from urllib.parse import parse_qs, urlparse

from answer_checker import TASK_ID, check_stockinette_answer
from trace_admin import TraceAdminRepository
from trace_store import TraceStore, utc_now

ROOT = Path(__file__).resolve().parent
STATIC_DIR = ROOT / "static"
WORKER = ROOT / "compiler_worker.py"
MAX_REQUEST_BYTES = 1024 * 1024
MAX_SOURCE_CHARS = 100_000
COMPILE_TIMEOUT_SECONDS = 25
MAX_CONCURRENT_COMPILES = 2
ADMIN_COOKIE_NAME = "knitscript_admin"
ADMIN_SESSION_SECONDS = 12 * 60 * 60
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


def get_admin_repository() -> TraceAdminRepository:
    return TraceAdminRepository(get_trace_store())


def _admin_token() -> str | None:
    token = os.environ.get("TRACE_ADMIN_TOKEN", "").strip()
    return token if len(token) >= 20 else None


def _admin_cookie_value(token: str, issued_at: int | None = None) -> str:
    timestamp = issued_at if issued_at is not None else int(time.time())
    message = f"admin:{timestamp}".encode("utf-8")
    signature = hmac.new(token.encode("utf-8"), message, hashlib.sha256).hexdigest()
    return f"{timestamp}.{signature}"


def _valid_admin_cookie(cookie_value: str | None, token: str) -> bool:
    if not cookie_value or "." not in cookie_value:
        return False
    timestamp_text, signature = cookie_value.split(".", 1)
    try:
        timestamp = int(timestamp_text)
    except ValueError:
        return False
    age = int(time.time()) - timestamp
    if age < -60 or age > ADMIN_SESSION_SECONDS:
        return False
    expected = _admin_cookie_value(token, timestamp).split(".", 1)[1]
    return hmac.compare_digest(signature, expected)


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


def evaluate_source(source: str) -> tuple[int, dict[str, Any]]:
    status, result = compile_source(source)
    result["check"] = check_stockinette_answer(result)
    return status, result


def build_study_config(completion_url: str | None = None) -> dict[str, Any]:
    candidate = (completion_url if completion_url is not None else os.environ.get("PROLIFIC_COMPLETION_URL", "")).strip()
    parsed = urlparse(candidate)
    completion_codes = parse_qs(parsed.query).get("cc", [])
    valid_completion_url = (
        candidate
        if parsed.scheme == "https"
        and parsed.hostname == "app.prolific.com"
        and parsed.path.rstrip("/") == "/submissions/complete"
        and any(completion_codes)
        else None
    )
    return {
        "task_id": TASK_ID,
        "prolific": {
            "configured": valid_completion_url is not None,
            "completion_url": valid_completion_url,
        },
    }


class KnitScriptHandler(SimpleHTTPRequestHandler):
    def __init__(self, *args: Any, **kwargs: Any) -> None:
        super().__init__(*args, directory=str(STATIC_DIR), **kwargs)

    def end_headers(self) -> None:
        self.send_header(
            "Content-Security-Policy",
            "default-src 'self'; script-src 'self'; style-src 'self'; img-src 'self' data:; "
            "connect-src 'self'; object-src 'none'; base-uri 'none'; frame-ancestors 'none'; form-action 'self'",
        )
        self.send_header("X-Content-Type-Options", "nosniff")
        self.send_header("X-Frame-Options", "DENY")
        self.send_header("Referrer-Policy", "no-referrer")
        super().end_headers()

    def _send_json(
        self,
        status: int,
        payload: dict[str, Any],
        headers: dict[str, str] | None = None,
    ) -> None:
        body = json.dumps(payload, ensure_ascii=False).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", str(len(body)))
        self.send_header("Cache-Control", "no-store")
        for name, value in (headers or {}).items():
            self.send_header(name, value)
        self.end_headers()
        self.wfile.write(body)

    def _cookie_value(self) -> str | None:
        cookie = SimpleCookie()
        try:
            cookie.load(self.headers.get("Cookie", ""))
        except CookieError:
            return None
        morsel = cookie.get(ADMIN_COOKIE_NAME)
        return morsel.value if morsel else None

    def _require_admin(self) -> bool:
        token = _admin_token()
        if token is None:
            self._send_json(
                HTTPStatus.SERVICE_UNAVAILABLE,
                {"ok": False, "error": {"message": "Admin dashboard is not configured."}},
            )
            return False
        if not _valid_admin_cookie(self._cookie_value(), token):
            self._send_json(
                HTTPStatus.UNAUTHORIZED,
                {"ok": False, "error": {"message": "Admin login required."}},
            )
            return False
        return True

    def _admin_cookie_header(self, value: str, max_age: int) -> str:
        host = self.headers.get("Host", "").split(":", 1)[0].lower()
        forwarded_https = self.headers.get("X-Forwarded-Proto", "").split(",", 1)[0].strip() == "https"
        secure = bool(os.environ.get("RAILWAY_ENVIRONMENT")) or forwarded_https or host not in {
            "127.0.0.1",
            "localhost",
            "::1",
        }
        attributes = [
            f"{ADMIN_COOKIE_NAME}={value}",
            "Path=/",
            f"Max-Age={max_age}",
            "HttpOnly",
            "SameSite=Strict",
        ]
        if secure:
            attributes.append("Secure")
        return "; ".join(attributes)

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
        parsed = urlparse(self.path)
        path = parsed.path
        if path == "/api/health":
            self._send_json(HTTPStatus.OK, {"ok": True, "compiler": "knit-script", "version": "0.5.0"})
            return
        if path == "/api/study-config":
            self._send_json(HTTPStatus.OK, {"ok": True, **build_study_config()})
            return
        if path == "/admin" or path == "/admin/":
            self.path = "/admin.html"
            super().do_GET()
            return
        if path.startswith("/api/admin/"):
            if not self._require_admin():
                return
            try:
                repository = get_admin_repository()
                if path == "/api/admin/sessions":
                    self._send_json(HTTPStatus.OK, {"ok": True, **repository.list_sessions()})
                    return
                prefix = "/api/admin/sessions/"
                if not path.startswith(prefix):
                    raise KeyError("Unknown admin endpoint")
                parts = path[len(prefix) :].split("/")
                session_id = parts[0]
                if len(parts) == 1:
                    self._send_json(HTTPStatus.OK, {"ok": True, **repository.get_session(session_id)})
                    return
                if len(parts) == 2 and parts[1] == "events":
                    query = parse_qs(parsed.query)
                    limit_text = query.get("limit", ["2000"])[0]
                    try:
                        limit = int(limit_text)
                    except ValueError as error:
                        raise ValueError("limit must be an integer") from error
                    events = repository.get_events(session_id, limit)
                    self._send_json(HTTPStatus.OK, {"ok": True, "events": events, "returned": len(events)})
                    return
                if len(parts) == 2 and parts[1] == "files":
                    query = parse_qs(parsed.query)
                    try:
                        limit = int(query.get("limit", ["500"])[0])
                        offset = int(query.get("offset", ["0"])[0])
                    except ValueError as error:
                        raise ValueError("limit and offset must be integers") from error
                    self._send_json(
                        HTTPStatus.OK,
                        {"ok": True, **repository.list_files(session_id, limit=limit, offset=offset)},
                    )
                    return
                if len(parts) == 2 and parts[1] == "file":
                    relative_paths = parse_qs(parsed.query).get("path", [])
                    if len(relative_paths) != 1:
                        raise ValueError("A file path is required")
                    self._send_json(HTTPStatus.OK, {"ok": True, **repository.read_file(session_id, relative_paths[0])})
                    return
                raise KeyError("Unknown admin endpoint")
            except ValueError as error:
                self._send_json(HTTPStatus.BAD_REQUEST, {"ok": False, "error": {"message": str(error)}})
            except KeyError as error:
                self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": {"message": str(error)}})
            except OSError as error:
                self._send_json(
                    HTTPStatus.INTERNAL_SERVER_ERROR,
                    {"ok": False, "error": {"type": "StorageError", "message": str(error)}},
                )
            return
        super().do_GET()

    def do_POST(self) -> None:
        parsed = urlparse(self.path)
        path = parsed.path
        supported_paths = {
            "/api/run",
            "/api/submit",
            "/api/sessions",
            "/api/events",
            "/api/sessions/end",
            "/api/admin/login",
            "/api/admin/logout",
        }
        if path not in supported_paths:
            self._send_json(HTTPStatus.NOT_FOUND, {"ok": False, "error": {"message": "Not found"}})
            return
        if path == "/api/admin/logout":
            self._send_json(
                HTTPStatus.OK,
                {"ok": True},
                {"Set-Cookie": self._admin_cookie_header("", 0)},
            )
            return
        request = self._read_json_request()
        if request is None:
            return

        try:
            if path == "/api/admin/login":
                configured_token = _admin_token()
                supplied_token = request.get("token")
                if configured_token is None:
                    self._send_json(
                        HTTPStatus.SERVICE_UNAVAILABLE,
                        {"ok": False, "error": {"message": "Admin dashboard is not configured."}},
                    )
                    return
                if not isinstance(supplied_token, str) or not hmac.compare_digest(supplied_token, configured_token):
                    self._send_json(
                        HTTPStatus.UNAUTHORIZED,
                        {"ok": False, "error": {"message": "Invalid admin key."}},
                    )
                    return
                cookie_value = _admin_cookie_value(configured_token)
                self._send_json(
                    HTTPStatus.OK,
                    {"ok": True},
                    {"Set-Cookie": self._admin_cookie_header(cookie_value, ADMIN_SESSION_SECONDS)},
                )
                return

            if path == "/api/sessions":
                manifest = get_trace_store().create_session(
                    participant_id=request.get("participant_id"),
                    task_id=request.get("task_id", "playground"),
                    client=request.get("client"),
                    initial_source=request.get("initial_source"),
                    recruitment=request.get("recruitment"),
                )
                self._send_json(HTTPStatus.CREATED, {"ok": True, "session": manifest})
                return

            if path == "/api/events":
                batch = get_trace_store().append_event_batch(
                    session_id=request.get("session_id"),
                    batch_id=request.get("batch_id"),
                    events=request.get("events"),
                )
                self._send_json(HTTPStatus.OK, {"ok": True, **batch})
                return

            if path == "/api/sessions/end":
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
            session_id = request.get("session_id")
            is_submission = path == "/api/submit"
            if is_submission and not isinstance(session_id, str):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "error": {"message": "A study session is required before submitting."}},
                )
                return
            if is_submission and not isinstance(source, str):
                self._send_json(
                    HTTPStatus.BAD_REQUEST,
                    {"ok": False, "error": {"message": "source must be a string"}},
                )
                return
            requested_at = utc_now()
            execution_id = str(uuid.uuid4())
            status, result = evaluate_source(source)
            stored = None
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
            if is_submission:
                if stored is None:
                    self._send_json(
                        HTTPStatus.INTERNAL_SERVER_ERROR,
                        {"ok": False, "error": {"type": "StorageError", "message": "The submission could not be saved."}},
                    )
                    return
                submission = get_trace_store().record_submission(
                    session_id=session_id,
                    source=source,
                    check=result["check"],
                    execution_id=stored["execution_id"],
                )
                result["submission"] = {
                    "submission_id": submission["submission_id"],
                    "passed": submission["passed"],
                    "submitted_at": submission["submitted_at"],
                }
                if submission["passed"]:
                    result["completion_url"] = build_study_config()["prolific"]["completion_url"]
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
