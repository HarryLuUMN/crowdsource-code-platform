"""Filesystem-backed trace storage using immutable JSONL batches and artifacts."""

from __future__ import annotations

import hashlib
import json
import os
import re
import tempfile
import threading
import uuid
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

SCHEMA_VERSION = 1
_SAFE_ID = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def utc_now() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def _require_safe_id(value: str, name: str) -> str:
    if not isinstance(value, str) or _SAFE_ID.fullmatch(value) is None:
        raise ValueError(f"Invalid {name}")
    return value


def _write_json_atomic(path: Path, value: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temp_file:
        json.dump(value, temp_file, ensure_ascii=False, indent=2, sort_keys=True)
        temp_file.write("\n")
        temp_path = Path(temp_file.name)
    os.replace(temp_path, path)


def _write_text_atomic(path: Path, value: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=path.parent, delete=False) as temp_file:
        temp_file.write(value)
        temp_path = Path(temp_file.name)
    os.replace(temp_path, path)


class TraceStore:
    """Persist replayable session traces without requiring a database."""

    def __init__(self, root: Path) -> None:
        self.root = Path(root).resolve()
        self.root.mkdir(parents=True, exist_ok=True)
        self._locks_guard = threading.Lock()
        self._session_locks: dict[str, threading.Lock] = {}

    def _lock_for(self, session_id: str) -> threading.Lock:
        with self._locks_guard:
            return self._session_locks.setdefault(session_id, threading.Lock())

    def _session_dir(self, session_id: str) -> Path:
        return self.root / _require_safe_id(session_id, "session_id")

    def _read_manifest(self, session_id: str) -> dict[str, Any]:
        path = self._session_dir(session_id) / "manifest.json"
        if not path.is_file():
            raise KeyError("Unknown session")
        return json.loads(path.read_text(encoding="utf-8"))

    @staticmethod
    def _event_state(session_dir: Path) -> tuple[dict[int, str | None], int]:
        """Return stored event sequences and the largest gap-free sequence."""
        sequences: dict[int, str | None] = {}
        for batch_path in (session_dir / "events").glob("*.jsonl"):
            for line in batch_path.read_text(encoding="utf-8").splitlines():
                event = json.loads(line)
                seq = event.get("seq")
                if not isinstance(seq, int) or seq < 1:
                    raise ValueError(f"Invalid event sequence in {batch_path.name}")
                if seq in sequences:
                    raise ValueError(f"Duplicate stored event sequence {seq}")
                sequences[seq] = event.get("client_event_id")

        last_contiguous_seq = 0
        while last_contiguous_seq + 1 in sequences:
            last_contiguous_seq += 1
        return sequences, last_contiguous_seq

    def create_session(
        self,
        participant_id: str,
        task_id: str,
        client: dict[str, Any] | None = None,
        initial_source: str | None = None,
    ) -> dict[str, Any]:
        if client is not None and not isinstance(client, dict):
            raise ValueError("client must be an object")
        if initial_source is not None and not isinstance(initial_source, str):
            raise ValueError("initial_source must be a string")
        session_id = str(uuid.uuid4())
        session_dir = self._session_dir(session_id)
        (session_dir / "events").mkdir(parents=True)
        (session_dir / "code").mkdir()
        (session_dir / "executions").mkdir()

        initial_code_state_id = None
        if initial_source is not None:
            source_hash = hashlib.sha256(initial_source.encode("utf-8")).hexdigest()
            _write_text_atomic(session_dir / "code" / "source-initial.ks", initial_source)
            _write_text_atomic(session_dir / "code" / f"{source_hash}.ks", initial_source)
            initial_code_state_id = f"sha256:{source_hash}"

        manifest = {
            "schema_version": SCHEMA_VERSION,
            "session_id": session_id,
            "participant_id": _require_safe_id(participant_id, "participant_id"),
            "task_id": _require_safe_id(task_id, "task_id"),
            "status": "active",
            "started_at": utc_now(),
            "ended_at": None,
            "event_count": 0,
            "execution_count": 0,
            "last_seq": 0,
            "initial_code_state_id": initial_code_state_id,
            "client": client or {},
        }
        _write_json_atomic(session_dir / "manifest.json", manifest)
        return manifest

    def append_event_batch(self, session_id: str, batch_id: str, events: list[dict[str, Any]]) -> dict[str, Any]:
        batch_id = _require_safe_id(batch_id, "batch_id")
        if not isinstance(events, list) or not events or len(events) > 500:
            raise ValueError("An event batch must contain between 1 and 500 events")

        session_dir = self._session_dir(session_id)
        relative_path = Path("events") / f"{batch_id}.jsonl"
        batch_path = session_dir / relative_path

        with self._lock_for(session_id):
            manifest = self._read_manifest(session_id)
            if batch_path.is_file():
                stored_sequences, last_contiguous_seq = self._event_state(session_dir)
                manifest["event_count"] = len(stored_sequences)
                manifest["last_seq"] = last_contiguous_seq
                _write_json_atomic(session_dir / "manifest.json", manifest)
                return {"accepted": 0, "duplicate": True, "object_key": relative_path.as_posix()}

            normalized_events: list[dict[str, Any]] = []
            first_seq = events[0].get("seq") if isinstance(events[0], dict) else None
            if not isinstance(first_seq, int) or first_seq < 1:
                raise ValueError("Every event requires a positive integer sequence")
            for offset, event in enumerate(events):
                expected_seq = first_seq + offset
                if not isinstance(event, dict) or event.get("seq") != expected_seq:
                    raise ValueError(f"Expected event sequence {expected_seq}")
                event_type = event.get("type")
                if not isinstance(event_type, str) or not event_type:
                    raise ValueError("Every event requires a type")
                normalized_events.append(
                    {
                        **event,
                        "session_id": session_id,
                        "schema_version": SCHEMA_VERSION,
                        "server_timestamp": utc_now(),
                    }
                )

            stored_sequences, _last_contiguous_seq = self._event_state(session_dir)
            overlapping_sequences = stored_sequences.keys() & {event["seq"] for event in normalized_events}
            if overlapping_sequences:
                first_overlap = min(overlapping_sequences)
                raise ValueError(f"Event sequence {first_overlap} is already stored")

            with tempfile.NamedTemporaryFile("w", encoding="utf-8", dir=batch_path.parent, delete=False) as temp_file:
                for event in normalized_events:
                    temp_file.write(json.dumps(event, ensure_ascii=False, separators=(",", ":")))
                    temp_file.write("\n")
                temp_path = Path(temp_file.name)
            os.replace(temp_path, batch_path)

            stored_sequences, last_contiguous_seq = self._event_state(session_dir)
            manifest["last_seq"] = last_contiguous_seq
            manifest["event_count"] = len(stored_sequences)
            _write_json_atomic(session_dir / "manifest.json", manifest)

        return {"accepted": len(normalized_events), "duplicate": False, "object_key": relative_path.as_posix()}

    def record_execution(
        self,
        session_id: str,
        source: str,
        result: dict[str, Any],
        requested_at: str,
        execution_id: str | None = None,
    ) -> dict[str, Any]:
        """Persist one compile attempt and every artifact produced by it."""
        if not isinstance(source, str):
            raise ValueError("source must be a string")
        execution_id = execution_id or str(uuid.uuid4())
        _require_safe_id(execution_id, "execution_id")
        session_dir = self._session_dir(session_id)
        execution_dir = session_dir / "executions" / execution_id

        with self._lock_for(session_id):
            manifest = self._read_manifest(session_id)
            if execution_dir.exists():
                raise FileExistsError("Execution already exists")
            execution_dir.mkdir(parents=True)

            source_hash = hashlib.sha256(source.encode("utf-8")).hexdigest()
            code_state_path = session_dir / "code" / f"{source_hash}.ks"
            if not code_state_path.exists():
                _write_text_atomic(code_state_path, source)
            _write_text_atomic(execution_dir / "source.ks", source)

            artifacts: dict[str, str] = {"source": "source.ks", "code_state": f"../../code/{source_hash}.ks"}
            artifact_fields = {
                "knitout": "output.k",
                "partial_knitout": "error.k",
                "stdout": "stdout.txt",
                "stderr": "stderr.txt",
                "knit_graph": "knit-graph.json",
                "machine_state": "machine-state.json",
            }
            for field, filename in artifact_fields.items():
                value = result.get(field)
                if value is None and field not in {"stdout", "stderr"}:
                    continue
                if isinstance(value, (dict, list)):
                    serialized = json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                else:
                    serialized = str(value or "")
                _write_text_atomic(execution_dir / filename, serialized)
                artifacts[field] = filename

            error = result.get("error") if isinstance(result.get("error"), dict) else None
            if result.get("ok"):
                status = "succeeded"
            elif error and error.get("type") == "Timeout":
                status = "timeout"
            else:
                status = "compiler_error"

            stored_result = {
                "schema_version": SCHEMA_VERSION,
                "execution_id": execution_id,
                "session_id": session_id,
                "code_state_id": f"sha256:{source_hash}",
                "status": status,
                "requested_at": requested_at,
                "finished_at": utc_now(),
                "duration_ms": result.get("duration_ms"),
                "exit_code": result.get("exit_code"),
                "metrics": result.get("metrics", {}),
                "diagnostics": [error] if error else [],
                "artifacts": artifacts,
                "environment": result.get("environment", {}),
                "compiler_result": {
                    field: value for field, value in result.items() if field not in artifact_fields
                },
            }
            _write_json_atomic(execution_dir / "result.json", stored_result)

            manifest["execution_count"] = int(manifest["execution_count"]) + 1
            manifest["last_code_state_id"] = stored_result["code_state_id"]
            _write_json_atomic(session_dir / "manifest.json", manifest)

        return stored_result

    def end_session(self, session_id: str, final_source: str) -> dict[str, Any]:
        """Mark a session complete and preserve its final source snapshot."""
        if not isinstance(final_source, str):
            raise ValueError("final_source must be a string")
        with self._lock_for(session_id):
            session_dir = self._session_dir(session_id)
            manifest = self._read_manifest(session_id)
            source_hash = hashlib.sha256(final_source.encode("utf-8")).hexdigest()
            _write_text_atomic(session_dir / "code" / "source-final.ks", final_source)
            content_addressed_path = session_dir / "code" / f"{source_hash}.ks"
            if not content_addressed_path.exists():
                _write_text_atomic(content_addressed_path, final_source)
            manifest["status"] = "completed"
            manifest["ended_at"] = manifest.get("ended_at") or utc_now()
            manifest["final_code_state_id"] = f"sha256:{source_hash}"
            _write_json_atomic(session_dir / "manifest.json", manifest)
            return manifest
