# Local PDF Pipeline

Local pipeline for graded PDFs under `raw/`: exclude the identity page, separate student/teacher views, run isolated Codex OCR, and publish student, teacher, and combined Markdown. The Docker worker preserves original PDFs, detects content changes with SHA-256, and saves durable run records and per-page checkpoints. Question mapping, verified grading, and CSV export remain planned.

## Run with Docker

```sh
cp .env.example .env
# Set CODEX_AUTH_FILE_HOST to your host ChatGPT auth.json (see below).
docker compose run --build --rm worker python -m app.pipeline.jobs.worker --preflight-only
docker compose up --build -d
```

Run `codex login` on the host using your ChatGPT subscription. On Windows, set an absolute path in the ignored `.env`, for example `CODEX_AUTH_FILE_HOST=C:/Users/you/.codex/auth.json`. Docker mounts that file read-only and copies credentials into its private runtime `/tmp` for refresh. Credentials are never baked into the image or saved in `.pipeline/`. API-key authentication is rejected. OCR uses the Codex service through your subscription; image preprocessing and artifact storage run locally in Docker.

The API health endpoint is at `http://127.0.0.1:8000/api/health`. The worker polls `raw/`. Start with one worker; scale only within available RAM and subscription capacity:

```sh
docker compose up --build -d --scale worker=3
```

Stop containers with `docker compose down`.

### Environment settings

`.env.example` groups the Docker settings into three sections:

- **Host folders:** `PIPELINE_RAW_HOST_DIR`, `PIPELINE_OUTPUT_HOST_DIR`, `PIPELINE_STATE_HOST_DIR`
- **Container permissions:** `PIPELINE_UID`, `PIPELINE_GID` (default `0:0`, which maps to your user with rootless Docker)
- **Worker capacity:** `PIPELINE_WORKER_REPLICAS` (containers, default 1), `PIPELINE_WORKERS` (parallel PDFs per container), and `PIPELINE_POLL_SECONDS` (scan interval)

Defaults use `raw/`, `output/`, and `.pipeline/`, one PDF per container, and five-second polling. `PIPELINE_OCR_ENABLED=1` enables OCR in Compose. `PIPELINE_OCR_CAPACITY` limits simultaneous Codex calls across replicas (default 1); all replicas must use the same value and state mount. Model, effort, timeout, and image-size defaults live only in `backend/app/pipeline/codex/settings.py`: `gpt-6.1-sol`, `high`, 900 seconds/page, and a maximum image side of 3200 pixels. Blank environment overrides retain those defaults. Preflight checks CLI flags, ChatGPT login, and the account's model/vision/effort support; it never substitutes a model/provider.

### Two workers and two concurrent OCR calls

Set these values in the ignored `.env`:

```dotenv
PIPELINE_WORKER_REPLICAS=2
PIPELINE_WORKERS=1
PIPELINE_OCR_CAPACITY=2
```

The replica count controls containers, the worker count controls PDFs per container, and OCR capacity is a shared ceiling across every container using the same state mount. A `--scale worker=N` command overrides the configured replica count. Two workers with OCR capacity 1 still serialize model calls.

Environment changes do not affect running workers. Check `python -m app.pipeline.jobs.status` inside a worker and wait for active runs and one-off retries to finish before applying a capacity change. Recreating a busy container interrupts its current call. Once idle, apply the configuration with `docker compose up --build -d --no-deps worker`. Keep the same OCR capacity on every replica. Start at 2 and observe RAM, timeouts and subscription limits before increasing further.

Capacity now changes scheduling only: it does not invalidate successful OCR checkpoints or published Markdown. Cache signatures retain compatibility with the earlier capacity-1 format used by the existing batch; model, effort, image size, OCR version and other existing signature fields still determine reuse. Existing outputs made with older code at a nondefault capacity are not automatically migrated. Actual run capacity remains recorded in each new run's extraction configuration.

## Run and monitor in Docker

```sh
docker compose run --rm worker python -m app.pipeline.jobs.worker --once
docker compose logs -f worker
docker compose exec worker python -m app.pipeline.jobs.status
```

Use `--preprocess-only` for a batch without Codex requests. The Compose worker still requires the configured auth-file mount; preprocessing itself does not read it. Set `PIPELINE_WORKERS` to bound concurrent PDFs. Docker/Linux workers use shared advisory locks released automatically on process death. Stop old worker versions before an upgrade. Completed content is cached; changing PDF bytes at the same path starts a new attempt. Older preprocessing without a source checksum is regenerated once.

Failures remain recorded and are not retried every poll. After resolving a timeout, authentication issue, or quota limit, stop workers and explicitly retry:

```sh
docker compose stop worker
docker compose run --rm worker python -m app.pipeline.jobs.worker --once --retry-failed
docker compose up -d --scale worker=1 worker
```

Successful page checkpoints are reused. Quota/authentication failures pause OCR across replicas until explicitly retried; no provider fallback occurs. Refresh the host login when needed and recreate the container to refresh its runtime copy. An interrupted call may need to be submitted again if it ended before its validated checkpoint was saved.

To retry only one PDF, add its path relative to `raw/` (requires `--once`):

```sh
docker compose run --rm worker python -m app.pipeline.jobs.worker --once --source signal/318/1.pdf --retry-failed
```

Failed-job retries are claimed per PDF; this command leaves other PDFs' failed markers intact. A polling worker retries each selected failed source at most once per invocation, not every poll. As before, `--retry-failed` also clears the shared quota/authentication pause, so resolve that cause first.

`invalid_output` includes a safe reason and field location: for example `invalid_json` with line/column, `missing_field`, `field_type`, `source_page_mismatch`, or `empty_student_answer` at `$.items[0].answer_markdown`. The run manifest and status command expose `error_details` and `failure_evidence`. Rejected output is retained under `.pipeline/ocr/<source-stem>/runs/<run-id>/failures/<pass>-page-NNN/`: `error.json` contains diagnostics, input checksum and configuration; `schema.json` is the expected schema; `response.bin` contains the exact returned bytes if a response file was readable. Missing/unreadable output is recorded explicitly without inventing response bytes. These are private local artifacts, not published Markdown or usable checkpoints. Raw responses, stdout/stderr, answers and authentication data are never printed into worker logs; CLI stdout/stderr and credentials are not archived with failure evidence. Failed evidence from older runs cannot be recovered retroactively.

Record validation version 2 retains an empty student answer when a nonblank question label exists, and always adds `needs_review` plus a note to inspect the source image. It does not invent answer text, decide whether the page is genuinely blank, or infer a zero score. Empty answers without a usable question label still fail validation. This handles unanswered subquestions without failing an entire PDF. The validation version is recorded in new runs and publications; prompts, JSON schema and model checkpoint keys are unchanged, so existing successful checkpoints and published results remain reusable.

## OCR results

For `raw/signal/318/1.pdf`, open:

```text
.pipeline/jobs/signal/318/1/student.pdf
.pipeline/jobs/signal/318/1/teacher.pdf
.pipeline/ocr/signal/318/1/student.md
.pipeline/ocr/signal/318/1/teacher.md
.pipeline/ocr/signal/318/1/ocr.md
```

Markdown appears after both passes finish. `ocr.md` contains separate Student answers and Teacher grading sections; it does not infer question matching or merge grades into student answers. The authoritative `latest.json` points to a complete immutable set under `runs/<run_id>/`, including `records.json` with source/config/image provenance. Root-level Markdown files are convenient copies; readers requiring a coherent set should follow `latest.json`. Failed OCR keeps the last published set. Checkpoints and submitted resized images remain under `checkpoints/` and `inputs/`. Overall run history lives under `.pipeline/runs/<source-path-hash>/`; anonymous attempt IDs persist for the same path/content.

Student calls receive only the prepared student image. Teacher calls receive only the red-only teacher image. Calls have separate temporary workspaces, schemas and prompts; shell, browser, app, image-reading tools and subagents are disabled. Scores remain literal strings, unknown question associations are flagged, and no hidden handwriting is reconstructed. Page numbering starts at 2. Images can still contain residual grading or color-separation leakage, and resizing can lose fine details. OCR/schema success is not verified transcription accuracy. Review flags and notes are retained in JSON/Markdown; a review UI is not implemented.

## Local data

- Inputs: `raw/` (read-only in Docker)
- Final CSVs: `output/` (not produced by this initial worker)
- Job markers and intermediate state: `.pipeline/`

These data directories and local credentials are excluded from Git. Never commit private exam PDFs or generated student data.

## Red-mark removal

Preprocessing version 11 uses the red-channel grayscale image as a baseline, then replaces confident red pixels with estimated local paper brightness. Faint edges must connect through red-colored pixels to a strong red seed. Nearby writing, suspected crossings, very dark mixed ink, and missing paper evidence veto removal. No hidden handwriting is reconstructed.

Each job includes `grading-view/` (unchanged), `red-masks/` (detected candidates), `removal-masks/` (pixels actually replaced), `student-view/`, `teacher-view/` (detected red pixels in their original colors on white), `overlap-candidates/` (retained red and nearby writing), and `preprocessing.json` (method, parameters, and per-page counts). All PNGs retain source page numbers, starting at `page-002.png`. `output/` remains unused until CSV export is implemented.

Open the per-source `student.pdf` and `teacher.pdf` bundles to inspect the separated views. For `raw/signal/318/1.pdf`, they are `.pipeline/jobs/signal/318/1/student.pdf` and `.pipeline/jobs/signal/318/1/teacher.pdf`. Both omit the identity page, retain source page dimensions, and use page labels/bookmarks starting at source page 2. They embed only the derived images, without original text layers or metadata. The teacher bundle includes all detected red candidates, even when uncertainty prevents their removal from the student view; it is not a complete or verified teacher-grading record. Keep `grading-view/` as the untouched evidence.

The worker regenerates older preprocessing versions, changed PDF content, or missing required artifacts. Parameters are centralized in `backend/app/pipeline/pdf/student_view.py`; Python callers may supply `StudentViewConfig`. The worker uses defaults. After changing them, bump `PREPROCESS_VERSION`. OCR checkpoints include the pass, source page, separated-image checksum, CLI version, and OCR configuration/version. Bump `OCR_VERSION` after prompt or schema changes.

See [red-mark removal](docs/red-mark-removal.md) for the algorithm, artifact meanings, and limitations. Color does not identify who wrote a mark: red student writing or red printed content can also be removed. Uncertain crossings can retain teacher marks, very faint/gray marks may be missed, and fully obscured handwriting cannot be recovered. These images are not certified clean extraction inputs.

## Synthetic verification

Run in Docker from the repository root (no host Python installation needed):

```sh
docker compose --profile test run --build --rm tests
docker compose --profile test run --rm tests python -m tests.preview_student_view --output-dir /data/.pipeline/previews/red-removal
```

The `tests` service uses the same Dockerfile as the worker, mounts test code read-only, and runs only when requested through the test profile. Tests generate synthetic strokes and PDFs in temporary directories inside the container; no private PDFs are needed. The preview command writes `comparison.png`, per-case images, and `metrics.json` to the configured host state directory (default `.pipeline/previews/red-removal/`). Compare the old and new student views alongside known clean synthetic images. Metrics describe only these fixtures, not real-exam accuracy.

Synthetic tests cover pixel-level separation, PDF bundle isolation, source changes at the same path, durable failures, isolated OCR inputs, schema validation, literal math/scores, preservation of published Markdown on failure, checkpoint reuse, global concurrency limits, and crash-released claims. Mock OCR tests do not measure model accuracy. Local real PDFs have been visually inspected as contact sheets; some paper-edge color noise and uncertain student-view marks remain. Private data and verification artifacts stay in ignored local folders.

The OCR integration passed 45 Docker unit tests, including diagnostics, targeted retry, blank-answer review, and capacity changes preserving completed results and resumable checkpoints. Separate smoke checks ran two containers against the actual shared host volume with capacities 1 and 2: each processed eight synthetic PDFs and 16 page/pass calls with zero duplicates, unchanged sources, and page 1 excluded. Measured peak concurrency matched each configured capacity. Reproduce with `tests.verify_docker_ocr`: run `prepare`, two concurrent `worker` commands, then `verify`, each via the `tests` service with the same new `--root /data/.pipeline/verification/<unique-name>` directory. Use `--capacity 2 --workers 1` for the two-slot case. This smoke client is synthetic and makes no model requests.

## Remaining work

Question-region mapping, stable response IDs, cross-page response grouping, interpreted scores, review revisions/UI, and CSV export remain unimplemented. Current OCR is page-based transcription, with unmapped labels and uncertainty preserved. The combined Markdown is an inspectable intermediate, not a human-verified dataset.
