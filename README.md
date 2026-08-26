# KnitScript Studio

A browser-based environment for writing KnitScript and compiling it through the real KnitScript toolchain. It also records replayable programming traces and preserves every compiler result for later benchmark analysis.

## Run locally

Python 3.12 is recommended.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python server.py
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000), edit the example, and press **Run** or <kbd>Cmd/Ctrl</kbd> + <kbd>Enter</kbd>.

## Trace storage

No database is required for the MVP. Each browser visit creates one directory under `data/traces/`:

```text
data/traces/<session-id>/
├── manifest.json
├── events/<batch-id>.jsonl
├── code/
│   ├── source-initial.ks
│   ├── source-final.ks
│   └── <source-sha256>.ks
└── executions/<execution-id>/
    ├── source.ks
    ├── result.json
    ├── output.k
    ├── error.k
    ├── stdout.txt
    └── stderr.txt
```

The event stream records semantic edits (inserted/deleted text and offsets), paste, undo/redo, reset, run requests, compiler outcomes, output views, visibility changes, and session timing. Compiler bundles include the exact source, full knitout or partial error knitout, diagnostics, logs, metrics, duration, exit code, and toolchain versions.

Set `TRACE_STORAGE_DIR` to place traces on a mounted persistent volume:

```bash
TRACE_STORAGE_DIR=/path/to/persistent/traces .venv/bin/python server.py
```

This filesystem format is intentionally object-storage friendly: each event batch and execution is immutable. On serverless hosting, use a persistent volume or replace the filesystem writes with S3/R2 object writes; a function's temporary disk alone is not durable.

## Test

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Current safety boundary

Compilation runs in a temporary subprocess with time, file-size, and file-descriptor limits. This is appropriate for local development and controlled testing. Before accepting untrusted public submissions, move `compiler_worker.py` into a dedicated sandbox such as Modal Sandbox, gVisor, or Firecracker and disable outbound networking.
