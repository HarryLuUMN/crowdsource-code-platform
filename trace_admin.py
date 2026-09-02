"""Read-only projections of stored programming traces for the admin dashboard."""

from __future__ import annotations

import json
from collections import Counter
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any

from trace_store import TraceStore, _require_safe_id

MAX_PREVIEW_BYTES = 256_000


def _read_json(path: Path) -> dict[str, Any]:
    value = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(value, dict):
        raise ValueError(f"Expected an object in {path.name}")
    return value


def _read_jsonl(path: Path) -> list[dict[str, Any]]:
    if not path.is_file():
        return []
    values = []
    for line in path.read_text(encoding="utf-8").splitlines():
        value = json.loads(line)
        if isinstance(value, dict):
            values.append(value)
    return values


def _mtime(path: Path) -> str:
    return datetime.fromtimestamp(path.stat().st_mtime, UTC).isoformat().replace("+00:00", "Z")


def _timestamp(value: Any) -> datetime:
    if not isinstance(value, str) or not value:
        return datetime.min.replace(tzinfo=UTC)
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        return datetime.min.replace(tzinfo=UTC)
    return parsed if parsed.tzinfo else parsed.replace(tzinfo=UTC)


def _missing_ranges(sequences: list[int]) -> list[dict[str, int]]:
    ranges = []
    expected = 1
    for sequence in sorted(set(sequences)):
        if sequence > expected:
            ranges.append({"start": expected, "end": sequence - 1})
        expected = max(expected, sequence + 1)
    return ranges


def _event_timestamp(event: dict[str, Any], session_started_at: Any) -> datetime:
    elapsed_ms = event.get("elapsed_ms")
    if isinstance(elapsed_ms, (int, float)) and elapsed_ms >= 0:
        return _timestamp(session_started_at) + timedelta(milliseconds=elapsed_ms)
    return _timestamp(event.get("client_timestamp") or event.get("server_timestamp"))


class TraceAdminRepository:
    """Expose bounded, dashboard-friendly views over a TraceStore."""

    def __init__(self, store: TraceStore) -> None:
        self.store = store
        self.root = store.root

    def _session_dir(self, session_id: str) -> Path:
        safe_id = _require_safe_id(session_id, "session_id")
        session_dir = (self.root / safe_id).resolve()
        if session_dir.parent != self.root or not (session_dir / "manifest.json").is_file():
            raise KeyError("Unknown session")
        return session_dir

    def list_sessions(self) -> dict[str, Any]:
        sessions = []
        for manifest_path in self.root.glob("*/manifest.json"):
            try:
                manifest = _read_json(manifest_path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            recruitment = manifest.get("recruitment") if isinstance(manifest.get("recruitment"), dict) else {}
            sessions.append(
                {
                    "session_id": manifest.get("session_id", manifest_path.parent.name),
                    "participant_id": manifest.get("participant_id"),
                    "task_id": manifest.get("task_id"),
                    "status": manifest.get("status"),
                    "started_at": manifest.get("started_at"),
                    "ended_at": manifest.get("ended_at"),
                    "updated_at": _mtime(manifest_path),
                    "event_count": int(manifest.get("event_count", 0)),
                    "observation_count": int(manifest.get("observation_count", 0)),
                    "execution_count": int(manifest.get("execution_count", 0)),
                    "submission_count": int(manifest.get("submission_count", 0)),
                    "passed": bool(manifest.get("passed_submission_id")),
                    "prolific_pid": recruitment.get("prolific_pid"),
                    "study_id": recruitment.get("study_id"),
                }
            )
        sessions.sort(key=lambda item: item.get("updated_at") or item.get("started_at") or "", reverse=True)
        return {
            "sessions": sessions,
            "summary": {
                "session_count": len(sessions),
                "active_count": sum(session.get("status") == "active" for session in sessions),
                "passed_count": sum(session.get("passed", False) for session in sessions),
                "event_count": sum(session["event_count"] for session in sessions),
                "execution_count": sum(session["execution_count"] for session in sessions),
            },
        }

    def get_session(self, session_id: str) -> dict[str, Any]:
        session_dir = self._session_dir(session_id)
        manifest = self.store.materialize_observations(session_id)
        events = self.get_events(session_id)
        labels = _read_jsonl(session_dir / "delta-observations" / "step_labels.jsonl")
        executions = []
        for result_path in (session_dir / "executions").glob("*/result.json"):
            try:
                result = _read_json(result_path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            result["result_path"] = result_path.relative_to(session_dir).as_posix()
            executions.append(result)
        executions.sort(key=lambda item: item.get("requested_at") or "", reverse=True)

        submissions = []
        for submission_path in (session_dir / "submissions").glob("*.json"):
            try:
                submission = _read_json(submission_path)
            except (OSError, ValueError, json.JSONDecodeError):
                continue
            submission["path"] = submission_path.relative_to(session_dir).as_posix()
            submissions.append(submission)
        submissions.sort(key=lambda item: item.get("submitted_at") or "", reverse=True)

        recovered_trajectory, trace_integrity = self._recovered_trajectory(
            manifest,
            events,
            labels,
            executions,
            submissions,
        )

        event_counts = Counter(event.get("type", "unknown") for event in events)
        return {
            "manifest": manifest,
            "event_type_counts": dict(sorted(event_counts.items())),
            "observations": labels,
            "recovered_trajectory": recovered_trajectory,
            "trace_integrity": trace_integrity,
            "executions": executions,
            "submissions": submissions,
        }

    @staticmethod
    def _recovered_trajectory(
        manifest: dict[str, Any],
        events: list[dict[str, Any]],
        observations: list[dict[str, Any]],
        executions: list[dict[str, Any]],
        submissions: list[dict[str, Any]],
    ) -> tuple[list[dict[str, Any]], dict[str, Any]]:
        events_by_sequence = {
            event["seq"]: event for event in events if isinstance(event.get("seq"), int)
        }
        sequences = list(events_by_sequence)
        last_contiguous_sequence = int(manifest.get("last_seq", 0))
        last_contiguous_event = events_by_sequence.get(last_contiguous_sequence, {})
        reliable_timestamp = _event_timestamp(last_contiguous_event, manifest.get("started_at"))

        trajectory = []
        for observation in observations:
            source_event = events_by_sequence.get(observation.get("eventSeq"), {})
            item = {
                **observation,
                "provenance": "raw_event",
                "sourcePath": f"delta-observations/{observation['file']}",
                "timestamp": _event_timestamp(source_event, manifest.get("started_at"))
                .isoformat()
                .replace("+00:00", "Z"),
            }
            trajectory.append(item)

        recovered = []
        for execution in sorted(executions, key=lambda item: _timestamp(item.get("requested_at"))):
            requested_at = execution.get("requested_at")
            if _timestamp(requested_at) <= reliable_timestamp:
                continue
            result_path = Path(execution.get("result_path", ""))
            source_name = (execution.get("artifacts") or {}).get("source", "source.ks")
            source_path = (result_path.parent / source_name).as_posix()
            check = (execution.get("compiler_result") or {}).get("check") or {}
            recovered.append(
                {
                    "primaryLabel": "RECOVERED_COMPILE_CHECKPOINT",
                    "labels": ["RECOVERED_COMPILE_CHECKPOINT"],
                    "sourceEventType": "execution.checkpoint",
                    "eventSeq": None,
                    "timestamp": requested_at,
                    "requestedAt": requested_at,
                    "executionId": execution.get("execution_id"),
                    "codeStateId": execution.get("code_state_id"),
                    "sourcePath": source_path,
                    "status": execution.get("status"),
                    "passed": check.get("passed"),
                    "provenance": "execution_checkpoint",
                    "note": "Exact source recovered from a compiler execution; intermediate keystrokes are unavailable.",
                }
            )
        trajectory.extend(recovered)
        trajectory.sort(key=lambda item: (_timestamp(item.get("timestamp")), item.get("eventSeq") or 0))
        for index, item in enumerate(trajectory):
            item["trajectoryStep"] = index

        latest_activity = max(
            [
                *(_event_timestamp(event, manifest.get("started_at")) for event in events),
                *(_timestamp(execution.get("requested_at")) for execution in executions),
                *(_timestamp(submission.get("submitted_at")) for submission in submissions),
            ],
            default=reliable_timestamp,
        )
        missing_ranges = _missing_ranges(sequences)
        tail_gap_detected = latest_activity > reliable_timestamp and bool(recovered)
        recovery_needed = bool(missing_ranges) or tail_gap_detected
        return trajectory, {
            "raw_complete": not recovery_needed,
            "recovery_needed": recovery_needed,
            "raw_event_count": len(events),
            "last_contiguous_sequence": last_contiguous_sequence,
            "max_stored_sequence": max(sequences, default=0),
            "last_reliable_timestamp": (
                reliable_timestamp.isoformat().replace("+00:00", "Z")
            ),
            "latest_activity_timestamp": latest_activity.isoformat().replace("+00:00", "Z"),
            "missing_event_ranges": missing_ranges,
            "tail_gap_detected": tail_gap_detected,
            "recovered_checkpoint_count": len(recovered),
            "recovery_method": "execution_source_checkpoints" if recovered else None,
            "limitation": (
                "Recovered checkpoints contain exact compile-time source, but cannot reconstruct missing keystrokes."
                if recovered
                else None
            ),
        }

    def list_files(self, session_id: str, limit: int = 500, offset: int = 0) -> dict[str, Any]:
        if not isinstance(limit, int) or limit < 1 or limit > 1000:
            raise ValueError("limit must be between 1 and 1000")
        if not isinstance(offset, int) or offset < 0:
            raise ValueError("offset must be zero or greater")
        session_dir = self._session_dir(session_id)
        paths = sorted(path for path in session_dir.rglob("*") if path.is_file() and not path.is_symlink())
        selected = paths[offset : offset + limit]
        return {
            "files": [
                {
                    "path": path.relative_to(session_dir).as_posix(),
                    "size": path.stat().st_size,
                    "updated_at": _mtime(path),
                }
                for path in selected
            ],
            "total": len(paths),
            "offset": offset,
            "has_more": offset + len(selected) < len(paths),
        }

    def get_events(self, session_id: str, limit: int | None = None) -> list[dict[str, Any]]:
        session_dir = self._session_dir(session_id)
        events = list(TraceStore._stored_events(session_dir).values())
        events.sort(key=lambda event: event.get("seq", 0))
        if limit is not None:
            if not isinstance(limit, int) or limit < 1 or limit > 10_000:
                raise ValueError("limit must be between 1 and 10000")
            events = events[-limit:]
        return events

    def read_file(self, session_id: str, relative_path: str) -> dict[str, Any]:
        if not isinstance(relative_path, str) or not relative_path or "\x00" in relative_path:
            raise ValueError("Invalid file path")
        session_dir = self._session_dir(session_id)
        requested_path = (session_dir / relative_path).resolve()
        try:
            requested_path.relative_to(session_dir)
        except ValueError as error:
            raise ValueError("File path leaves the session directory") from error
        if not requested_path.is_file() or requested_path.is_symlink():
            raise KeyError("Unknown file")
        size = requested_path.stat().st_size
        with requested_path.open("rb") as file:
            data = file.read(MAX_PREVIEW_BYTES + 1)
        truncated = len(data) > MAX_PREVIEW_BYTES
        if truncated:
            data = data[:MAX_PREVIEW_BYTES]
        return {
            "path": requested_path.relative_to(session_dir).as_posix(),
            "size": size,
            "truncated": truncated,
            "content": data.decode("utf-8", errors="replace"),
        }
