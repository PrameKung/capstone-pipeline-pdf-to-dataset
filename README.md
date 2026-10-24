# Local PDF Pipeline

This testing branch recursively finds PDFs under `raw/`, removes page 1 from each working copy, and runs Codex OCR. Each job folder under `.pipeline/jobs/` contains `pages-without-first.pdf`, the separate `student-response.md` and `grading-response.md`, and combined `ocr.md`. The worker validates dataset records and exports one CSV per PDF under the matching `output/` subfolder.

## Run with Docker

```sh
codex login            # sign in on the host with your ChatGPT subscription
cp .env.example .env   # set CODEX_AUTH_FILE_HOST if your login file is elsewhere
docker compose up --build
```

The API health endpoint is at `http://127.0.0.1:8000/api/health`. The worker polls `raw/` and writes coordination state under `.pipeline/`. Scale worker containers with:

```sh
docker compose up --build -d --scale worker=3
```

Stop containers with `docker compose down`.

### Container resource limits

Docker Compose limits each backend container to **0.5 CPU / 512 MB RAM** and each
worker replica to **1 CPU / 2 GB RAM** by default. Override these in `.env` with
`PIPELINE_BACKEND_CPUS`, `PIPELINE_BACKEND_MEMORY`, `PIPELINE_WORKER_CPUS`, and
`PIPELINE_WORKER_MEMORY`. These are ceilings, not reserved resources or benchmarked
minimum requirements. Five worker replicas can use up to 10 GB RAM in total.
Large PDFs may need a higher memory limit; exceeding it can cause Docker to kill
the worker. Rootless Docker needs host cgroup support/delegation to enforce limits.

Apply changed limits by recreating containers with `docker compose up -d` after
active jobs finish. A simple container restart does not apply configuration changes.

### Environment settings

`.env.example` groups the Docker settings into three sections:

- **Host folders:** `PIPELINE_RAW_HOST_DIR`, `PIPELINE_OUTPUT_HOST_DIR`, `PIPELINE_STATE_HOST_DIR`
- **Container permissions:** `PIPELINE_UID`, `PIPELINE_GID` (default `0:0`, which maps to your user with rootless Docker)
- **Worker capacity:** `PIPELINE_WORKERS` (parallel PDFs per container) and `PIPELINE_POLL_SECONDS` (scan interval)
- **Codex login:** `CODEX_AUTH_FILE_HOST` (host auth file mounted read-only into the worker)
- **Codex model:** `PIPELINE_CODEX_MODEL` and `PIPELINE_CODEX_EFFORT` (default `gpt-6.1-sol` and `high`)

Defaults use the repository's `raw/`, `output/`, and `.pipeline/` folders, run two PDFs concurrently per worker container, and poll every five seconds.

## Run the local worker once

```sh
make backend-sync
make pipeline-once
```

The worker image includes Codex CLI and uses the host's `codex login` file through a read-only mount. It makes a temporary runtime copy inside the container so CLI state does not write back to host credentials. Set `PIPELINE_WORKERS` to change concurrent PDF jobs. Docker replicas share `.pipeline/` and atomically claim each PDF path. Source checksums and extraction settings control OCR reuse; changed PDFs trigger new extraction. CSV exports also track Markdown and optional metadata changes.

## Local data

- Inputs: `raw/` (read-only in Docker)
- Final CSVs: `output/` (one CSV per source PDF)
- Job markers and intermediate state: `.pipeline/`

These data directories and local credentials are excluded from Git. Never commit private exam PDFs or generated student data.

## Reading OCR Markdown

Student and grading CLI passes are scheduled concurrently in separate working folders.
`PIPELINE_CODEX_MAX_CONCURRENT` (default `5`) caps active extraction requests across all
worker replicas sharing `.pipeline/`, using local Linux file locks. Use the same limit
in every worker and stop all workers before changing it. Slot files must not be deleted
while workers run; locks release automatically when a worker exits. Waiting for a slot
and running a pass each have the configured extraction timeout (default 1800 seconds).
Logs report queue wait and extraction duration. Each successful response is saved as it
finishes; combined OCR is replaced only after both passes succeed. Model, effort, and
image resolution are unchanged. This may reduce per-PDF latency; throughput still depends
on available Codex capacity. Restart/rebuild workers to load these changes.

Open a response file in VS Code and use Markdown Preview (`Ctrl+Shift+V`). Extraction prompts
request `$...$` inline math and separate `$$` display blocks using standard KaTeX/MathJax
commands, with consistent page/question headings. Grading responses use `Raw score:`,
`Marks:`, and `Feedback:` fields; raw scores remain literal source notation.
Unclear content is marked `[unclear]` rather than guessed. These instructions improve
format consistency but do not guarantee identical output or valid LaTeX on every run.

Prompt changes apply to new extractions after restarting the local worker or rebuilding
the Docker worker image. Bump `OCR_VERSION` when changing extraction prompts to invalidate OCR caches.
Legacy jobs can be exported with `--export-only`; normal processing regenerates
OCR when no current extraction manifest exists.

## Parse and match existing OCR Markdown

The dataset parser reads a job's separate OCR responses without rerunning Codex or
writing artifacts. From `backend/`, call:

```python
from pathlib import Path
from app.pipeline.dataset.markdown import read_job_markdown

result = read_job_markdown(Path("../.pipeline/jobs/signal/318/1"))
for response in result.responses:
    print(response.question_label, response.source_pages, response.needs_review)
```

It preserves student Markdown/LaTeX and keeps literal teacher scores, marks, and
feedback in separate grading records. Exact question labels occurring once per
page are grouped across pages; labels are not mapped to dataset question IDs yet.
Unknown labels and duplicate student labels on a page are kept separate for
review. Unmatched grading remains available in `result.unmatched_grading`.
Missing grading, unclear content, repeated grading sections, and distinct raw
scores produce review notes; no scores are calculated or summed.

The parser expects `## Page N` and `### Question LABEL` headings from the current
OCR prompts. It rejects page 1, non-increasing page numbers, and malformed headings
with `MarkdownFormatError`. Unlabeled page content is retained for review. Only
boundary blank lines are trimmed. Cross-page grouping assumes repeated exact
labels refer to the same question within a PDF; it cannot resolve reused question
numbers in different exam sections. It cannot detect teacher content that OCR
mistakenly included in student text.

The worker uses this parsing stage when building records for CSV export.

## Build dataset records

From `backend/`, build the 36-column rows from an existing completed OCR job:

```python
from pathlib import Path
from app.pipeline.dataset.records import build_job_records

result = build_job_records(
    Path("../raw/signal/318/1.pdf"),
    raw_dir=Path("../raw"),
    state_dir=Path("../.pipeline"),
    # Optional trusted metadata keyed by exact OCR question label:
    # questions={"1(a)": {"question_id": "your-question-id", ...}},
)
```

Each `result["records"]` entry contains `row` (exactly the 36 dataset columns)
and `internal` (student sections, original pages, separate teacher grading,
review notes, and separate null extracted/verified scores). Missing cells are
empty strings. Supplied JSON lists are serialized into JSON cell strings. No
reference answers, rubric judgments, numeric scores, or review approvals are
inferred. Raw teacher feedback is not copied into `grader_feedback`.

Supply `questions` as a mapping from exact OCR labels to question-level fields
from the CSV specification, including an explicit `question_id`. Difficulty
fields also belong to this mapping. Unknown or duplicate labels remain unmapped.
After an initial build, optional `response_metadata` can be keyed by the returned
`response_id` to supply response-level fields such as `expected_score` or
`rubric_analysis`. Unknown response IDs and attempts to override generated IDs or
student text are rejected. Supplied `expected_score` is a target value only; it
does not populate extracted or verified scores. Missing origin and sample type
remain blank. Any supplied `student_id` must already be anonymous.

Runs are atomically saved under `.pipeline/dataset/<source-key>/<attempt-id>/` as
JSON, outside job folders that preprocessing can replace. Each run has a unique
ID and records source/Markdown checksums and the supplied metadata. Attempt IDs
are deterministic from relative source path and PDF checksum; changed PDFs get
new attempt IDs. Response IDs use exact labels, or original pages and occurrence
order for unresolved/duplicate labels. They remain stable for unchanged response
structure; relabeling or splitting OCR sections can change IDs and requires
reconciliation. Moving a source to a different relative path changes its identity.

Existing OCR has no saved extraction manifest. Pass `extraction_config` only
when the actual historical model/effort settings are known, and
`extraction_source_sha256` only when recorded for that extraction. Missing values
produce run-level review notes; a supplied checksum mismatch rejects the build.
Use completed, idle jobs: this standalone builder does not coordinate with active
OCR workers or prove historical source linkage. It does not validate PDF structure.
The worker coordinates this builder with OCR and CSV export using a shared job lock.

### Record validation and exception review

`build_job_records` validates rows before saving the internal JSON run. It keeps
every supplied cell unchanged and stores issues in
`record["internal"]["validation"]["issues"]`, with field, message, and severity.
Existing OCR review notes remain separate and are preserved.

- `failed`: invalid column structure, non-string text/IDs, missing or duplicate
  response IDs, mismatched attempt IDs, invalid original pages, malformed JSON
  arrays, or nonnumeric/non-finite score cells.
- `needs_review`: unmapped questions, existing OCR ambiguities, negative or
  out-of-bounds scores, conflicting question metadata, or rubric discrepancies.
  Checks cover criterion IDs, required fields, awards and maxima, score totals,
  criterion status, and covered/missed lists. Fractional totals use decimal
  arithmetic. Missing optional fields remain blank; unavailable rubric metadata
  prevents corresponding checks rather than causing invented values.
- `extracted`: no row-level issues detected. This does not mean human verification.

`result["validation"]` reports status counts, `has_failures`, and only exceptional
`review_response_ids`. It also reports unmatched grading count and run-level
provenance notes, which still need attention even if no row has an issue. Full
unmatched grading records remain in `result["unmatched_grading"]`. Failed records
are retained internally for inspection; they are not a successful export.

To recheck an existing in-memory run, call
`validate_records(result["records"], attempt_id=result["attempt_id"])` from
`app.pipeline.dataset.validation`. This updates internal validation/status and
returns row-level counts; it does not save files or modify CSV cells. Validation
checks supplied data consistency, not transcription accuracy, teacher/student
separation, or whether supplied review approvals are authentic.

## Export CSVs

Export existing Markdown without rerunning OCR:

```sh
make pipeline-export
# Or, from backend/:
uv run --locked python -m app.pipeline.jobs.worker --once --export-only \
  --raw-dir ../raw --state-dir ../.pipeline --output-dir ../output
```

Normal `make pipeline-once` and Docker workers now perform preparation, OCR,
record building, validation, and export. Configure the output root using
`--output-dir` or `OUTPUT_DIR`; Docker workers mount the configured local output
folder. Input, output, and state roots must be separate, non-nested folders.
Stop all old workers before upgrading: the new shared Linux file locks release
on process exit and their files must not be deleted while workers are active.
Legacy `.lock` files cause a clear error; remove a stale legacy lock only after
confirming its old worker has stopped.

Optional `--metadata-dir` / `PIPELINE_METADATA_DIR` points to local JSON files
mirroring the PDF paths: `raw/signal/318/1.pdf` uses
`<metadata-dir>/signal/318/1.json`. Each file accepts `questions` (exact OCR label
to question fields) and `responses` (stable response ID to response fields).
Missing files mean no supplied metadata. Store private metadata under `.pipeline/`
to keep it out of Git. Docker defaults to `/data/.pipeline/metadata`, corresponding
to the host's `.pipeline/metadata/`. Override `PIPELINE_METADATA_DIR` in `.env`
with a container path inside the mounted state folder. Local workers interpret
the environment variable as a local path; use `--metadata-dir ../.pipeline/metadata`
when running from `backend/`.

The output has the exact 36-column header, UTF-8 encoding, and standard CSV
quoting for multiline Markdown, LaTeX, commas, and quotes. Failed validation
blocks publication; `needs_review` rows remain included with their review state
in the internal JSON run. Unknown question IDs stay blank. Empty response sets
produce a header-only CSV. No scores or reference material are generated.

Exports are written to a temporary `.csv` on the output filesystem, read back
for validation, then atomically replace the destination. Prior CSVs survive
extraction, parsing, validation, or pre-publication write failures. Temporary CSVs
are cleaned up on ordinary failures; a hard process kill may leave a hidden
staging `.csv`, which can be removed after workers stop. Export markers under
`.pipeline/claims/` track checksums, destination, and the internal record path.
A missing or modified output CSV is rebuilt. Source, Markdown, or metadata edits
invalidate the export cache. New OCR manifests record source checksum and actual
configured model/effort; source or configuration changes invalidate OCR as well.

Legacy Markdown lacks a source manifest. Its first `--export-only` run retains
that provenance warning rather than claiming a verified source association.
Subsequent source changes are rejected in export-only mode; use normal processing
to regenerate OCR. New jobs with an incomplete extraction manifest cannot export
partially refreshed Markdown. Finish editing inputs before starting a run.

Failures are recorded in `.pipeline/claims/*.failure.json`; `--once` exits nonzero
if a job fails. Polling workers retry a failed job when watched source/Markdown/
metadata content changes or after restarting. Shared locks skip jobs already
owned by another worker, so a one-shot invocation does not wait for those jobs.
All replicas must share the same raw/state/output configuration. Hash checks run
on each scan and can cost noticeable I/O on large input collections.

The worker integration was checked with synthetic extraction stubs, not live
Codex calls. Existing OCR still sends graded page images to the student pass;
this export work does not fix that previously identified isolation limitation.

See [verification results](docs/pipeline-verification.md) for the synthetic
edge cases, concurrent-process checks, recovery checks, and unverified areas.

## Reuse one question template across an exam batch

A shared template avoids copying question/solution/rubric metadata into every
student's per-PDF metadata file. Store it under `templates/`, outside replaceable
worker state. Templates and reference material are ignored by default; the
explicitly approved `com-eng-math-ii-midterm-2026-1/questions.json` is versioned.
Save templates
as JSON with a single `questions` mapping in the same format used by per-PDF
metadata. Keys must match OCR question labels exactly; explicit punctuation
aliases can share the same stable question ID. Include parent and subpart entries
only as alternative mapping levels, not extra exam points.

From `backend/`, select a template for a batch belonging to that exam:

```sh
uv run --locked python -m app.pipeline.jobs.worker --once --export-only \
  --question-template ../templates/<exam>/questions.json
```

Alternatively set `PIPELINE_QUESTION_TEMPLATE` to a local file path. For Docker,
set it in `.env` to `/data/templates/<exam>/questions.json` and recreate
the workers. Docker mounts `templates/` read-only; override its host location with
`PIPELINE_TEMPLATES_HOST_DIR`. The template selection is blank by default so
unrelated exams are not assigned a template.
Use separate runs/configurations for different exams.

Per-PDF `questions` metadata overrides individual template fields for the same
label; per-response data remains in the per-PDF `responses` mapping. Missing
question wording may remain an empty `question_text`. Reference solutions and
rubric policies do not assign student scores, generate rubric analysis, or imply
human review. Unsupported labels remain unmapped. Changes to the template
invalidate the export cache and allow failed polling jobs to retry. Invalid or
missing explicitly selected templates fail without replacing the previous CSV.
