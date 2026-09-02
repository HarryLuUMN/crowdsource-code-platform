# KnitScript Studio

A browser-based environment for writing KnitScript and compiling it through the real KnitScript toolchain. It also records replayable programming traces and preserves every compiler result for later benchmark analysis.

## Run locally

Python 3.12 is recommended.

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python server.py
```

Open [http://127.0.0.1:8000/?preview=1](http://127.0.0.1:8000/?preview=1), write a program from scratch, and press **Run** or <kbd>Cmd/Ctrl</kbd> + <kbd>Enter</kbd>.

## Trace storage

No database is required for the MVP. Each browser visit creates one directory under `data/traces/`:

```text
data/traces/<session-id>/
├── manifest.json
├── events/<batch-id>.jsonl
├── delta-observations/
│   ├── delta-session1-step0.txt
│   ├── delta-session1-step1.txt
│   └── step_labels.jsonl
├── code/
│   ├── source-initial.ks
│   ├── source-final.ks
│   └── <source-sha256>.ks
├── executions/<execution-id>/
│   ├── source.ks
│   ├── result.json
│   ├── output.k
│   ├── error.k
│   ├── stdout.txt
│   └── stderr.txt
└── submissions/<submission-id>.json
```

The event stream records semantic edits (inserted/deleted text and offsets), paste, undo/redo, reset, run requests, compiler outcomes, output views, visibility changes, and session timing. Compiler bundles include the exact source, full knitout or partial error knitout, diagnostics, logs, metrics, duration, exit code, and toolchain versions.

`delta-observations/` is an analysis-ready materialized view compatible with the benchmark trajectory layout used by `dsl-bench-alpha-delta-obs`. Every accepted code edit produces a complete source snapshot. Tutorial/documentation views produce `READ_DOCUMENTATION`, and Run or Submit requests produce `BROWSER_TESTING`. `step_labels.jsonl` links each observation to its raw event sequence, timestamp, elapsed time, generic action label, and immutable code state. Fine-grained intent labels and behavior/challenge summaries remain offline annotations rather than guessed collection-time data.

Event uploads are idempotent by event identity. If a browser retries a batch after losing the response, the server accepts any unseen suffix and ignores matching events already stored without creating sequence gaps.

Final Submit attempts are recompiled and checked on the server. Each attempt preserves its semantic test results and links back to the immutable compiler execution. Passing submissions also mark the session manifest with `passed_submission_id` and `passed_at`.

Set `TRACE_STORAGE_DIR` to place traces on a mounted persistent volume:

```bash
TRACE_STORAGE_DIR=/path/to/persistent/traces .venv/bin/python server.py
```

This filesystem format is intentionally object-storage friendly: each event batch and execution is immutable. On serverless hosting, use a persistent volume or replace the filesystem writes with S3/R2 object writes; a function's temporary disk alone is not durable.

## Private trace dashboard

Set a strong administrator key and open `/admin` to browse stored sessions without downloading the volume:

```bash
TRACE_ADMIN_TOKEN='a-long-random-secret' .venv/bin/python server.py
```

The dashboard shows Prolific identifiers, session status, event totals, the ordered browser-event timeline, replayable source snapshots, every compile result and artifact, submissions, and raw stored files. Data APIs require a signed, HTTP-only administrator session cookie. File previews are restricted to the selected session directory and capped at 256 KB. The key must be at least 20 characters and should be configured as a private Railway service variable.

## Test

```bash
.venv/bin/python -m unittest discover -s tests -v
```

## Deploy on Railway

The repository includes a Docker image and Railway health-check configuration. Deploy the repository as one Railway service, attach a volume at `/data`, and expose the generated Railway domain. Runtime traces are written to `/data/traces` through `TRACE_STORAGE_DIR`.

Required service settings:

```text
Volume mount path: /data
Health check: /api/health
TRACE_ADMIN_TOKEN: a strong random secret with at least 20 characters
```

Railway supplies the public `PORT` automatically. The container binds to `0.0.0.0` so the editor, compiler API, and trace store are available from the same origin.

## Launch through Prolific

The app accepts Prolific's `PROLIFIC_PID`, `STUDY_ID`, and `SESSION_ID` URL parameters and saves them with the trace. When a participant follows a static link from an external survey, the editor requires their Prolific ID before it starts the trace or enables the study controls. Set the study-specific Railway variable below so a passing submission can return the participant to Prolific:

```text
PROLIFIC_COMPLETION_URL=https://app.prolific.com/submissions/complete?cc=YOUR_CODE
```

See [PROLIFIC_SETUP.md](PROLIFIC_SETUP.md) for the external study URL, completion-path setup, task definition, and pilot checklist.

## Current safety boundary

Compilation runs in a temporary subprocess with time, file-size, and file-descriptor limits. This is appropriate for local development and controlled testing. Before accepting untrusted public submissions, move `compiler_worker.py` into a dedicated sandbox such as Modal Sandbox, gVisor, or Firecracker and disable outbound networking.
