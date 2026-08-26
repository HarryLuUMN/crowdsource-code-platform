"""Run one KnitScript program and emit a single JSON result."""

from __future__ import annotations

import contextlib
import io
import json
import platform
import sys
import time
from importlib.metadata import version
from pathlib import Path

from knit_script.interpret_knit_script import knit_script_to_knitout


def _apply_resource_limits() -> None:
    """Constrain this one-shot compiler process on Unix platforms."""
    try:
        import resource

        resource.setrlimit(resource.RLIMIT_CPU, (8, 8))
        resource.setrlimit(resource.RLIMIT_FSIZE, (2 * 1024 * 1024, 2 * 1024 * 1024))
        resource.setrlimit(resource.RLIMIT_NOFILE, (64, 64))
    except (ImportError, OSError, ValueError):
        # The parent process still enforces a wall-clock timeout everywhere.
        return


def _graph_metrics(graph: object) -> dict[str, int]:
    """Extract stable, user-facing metrics without serializing library internals."""
    loop_count = getattr(graph, "loop_count", 0)
    edge_count = getattr(graph, "edge_count", 0)
    return {
        "loops": int(loop_count() if callable(loop_count) else loop_count),
        "stitches": int(edge_count() if callable(edge_count) else edge_count),
        "courses": len(graph.get_courses()),
    }


def main() -> int:
    _apply_resource_limits()
    source = sys.stdin.read()
    output_path = Path("output.k")
    captured_stdout = io.StringIO()
    captured_stderr = io.StringIO()
    started = time.perf_counter()

    try:
        with contextlib.redirect_stdout(captured_stdout), contextlib.redirect_stderr(captured_stderr):
            graph, _machine = knit_script_to_knitout(
                source,
                str(output_path),
                pattern_is_filename=False,
            )

        result = {
            "ok": True,
            "knitout": output_path.read_text(encoding="utf-8"),
            "stdout": captured_stdout.getvalue(),
            "stderr": captured_stderr.getvalue(),
            "metrics": _graph_metrics(graph),
        }
    except Exception as exc:  # The compiler exposes several independent error families.
        result = {
            "ok": False,
            "error": {
                "type": type(exc).__name__,
                "message": str(exc).replace("\\n", "\n"),
            },
            "stdout": captured_stdout.getvalue(),
            "stderr": captured_stderr.getvalue(),
        }
        partial_output_path = Path("error.k")
        if partial_output_path.is_file():
            result["partial_knitout"] = partial_output_path.read_text(encoding="utf-8")

    result["duration_ms"] = round((time.perf_counter() - started) * 1000)
    result["environment"] = {
        "python": platform.python_version(),
        "knit_script": version("knit-script"),
        "knitout_interpreter": version("knitout-interpreter"),
        "virtual_knitting_machine": version("virtual-knitting-machine"),
    }
    print(json.dumps(result, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
