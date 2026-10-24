# Pipeline behavior

This document describes the expected behavior of a local batch run. Adapt module names and commands to the implementation as it is built; do not document commands that the repository does not yet provide as if they already work.

Current implementation reaches page-level OCR and Markdown publication. Question-region mapping, cross-page response grouping, human-review revisions, interpreted scores, and CSV export below remain target behavior. Current student OCR uses whole student-view pages; teacher OCR uses whole red-only teacher-view pages. Untouched grading images are retained as local evidence and are not attached to either OCR pass. See the README for executable Docker commands.

Use Git and GitHub for version control of the project. Do not commit PDFs, generated CSVs, temporary workspaces, job state, credentials, or private exam data.

## Run lifecycle

Each run should have a unique ID and a persistent manifest containing at least the source-relative path, content checksum, status, current stage, timestamps, pipeline version, and output location. Use these states:

```text
pending -> running -> completed
                   -> needs_review
                   -> failed
                   -> cancelled
```

Persist stage checkpoints so an interrupted run can resume and successful work can be reused. Do not mark a run completed when any required stage failed. Keep failure details actionable and avoid logging student names, answers, or credentials.

Implemented run records are `.pipeline/runs/<source-path-hash>/<run-id>.json` with `running`, `completed`, `needs_review`, or `failed` status. A crash leaves a running record until the next claimant records it as interrupted (or reconciles an already published result). Explicit pending/cancelled job management is not implemented. SHA-256 covers the entire original PDF without rendering/extracting page 1. A source change creates a new anonymous attempt identity. Old checksum-free preprocessing is regenerated once. Page checkpoints are keyed by image content, pass, source page, CLI version and centralized OCR configuration/version.

The authoritative OCR publication is `.pipeline/ocr/<source-stem>/latest.json`, atomically pointing to an immutable, validated set under `runs/<run-id>/`. Convenient root Markdown copies are replaced individually; consumers needing a consistent set follow the pointer. Failed extraction preserves the last published set. Failed-job markers prevent polling retry storms; quota/authentication failures also create a shared OCR pause. After resolving the issue, `--retry-failed` clears the shared pause and retries each selected failed PDF once under its path claim. `--once --source <relative.pdf> --retry-failed` targets one PDF without clearing other failed-job markers. Shared local Linux advisory locks coordinate source claims and OCR slots and release on process death; native Windows worker execution is not supported, so use Docker.

Rejected OCR responses retain exact bytes (when readable) and the expected schema under the run's `failures/<pass>-page-NNN/` directory, with safe diagnostics in `error.json`. Reasons distinguish missing/unreadable/empty responses, UTF-8/JSON errors, schema field errors, wrong source pages, and unsupported empty answers. Run manifests link the evidence and expose safe field paths or JSON offsets. Raw response values, unknown field names, CLI stdout/stderr, and credentials never enter worker logs. Failure evidence stays private in `.pipeline/`, is never used as a successful checkpoint, and does not replace published Markdown. Older discarded responses cannot be recovered.

Record validation version 2 preserves empty answer text for an identified question and adds a mandatory review flag/note. It does not conclude that the student left the answer blank, insert invented text, or assign zero points. An empty answer with no usable question label remains invalid, as does an empty page without an explanation. New runs/publications record the validation version. Prompts, the JSON schema and model cache signatures are unchanged; successful older results passed stricter checks and remain valid/reusable.

Use `.pipeline/` as the default local state directory for manifests, checkpoints, logs, intermediate evidence, and temporary files. All Docker worker replicas that share a job queue must mount the same local `.pipeline/` volume.

## Parallel workers

Compose exposes `PIPELINE_WORKER_REPLICAS` (containers), `PIPELINE_WORKERS` (PDFs per container), and `PIPELINE_OCR_CAPACITY` (shared simultaneous model-call limit). For two PDFs to extract concurrently, use 2 replicas, 1 worker per container, and capacity 2. Running processes do not reload environment settings; apply changes only after active jobs finish when interruption is unacceptable. Increasing replicas alone cannot accelerate two already-claimed PDFs waiting on capacity-1 slots. Capacity is excluded from the result identity while preserving legacy capacity-1 signatures, so scaling does not repeat the existing batch's successful OCR.

- Process different PDFs concurrently with a configurable Python worker count. Docker Compose or equivalent local orchestration may run multiple worker replicas.
- Claim jobs atomically across processes and replicas so one run is never handled by two workers at once.
- Give each job attempt a separate temporary workspace. Keep checkpoints and job state in a location shared by replicas that need to resume one another's work.
- Write the CSV to a temporary file and atomically replace the destination only after validation. Prevent concurrent runs for the same source from silently overwriting one another with a lock or explicit run/version policy.
- A successful rerun may replace the existing CSV for that source. A failed or cancelled rerun must leave the previous successful CSV unchanged.
- Bound parallelism by CPU, memory, PDF rendering cost, and Codex subscription quotas. Handle quota exhaustion as a clear resumable state; avoid retry storms.
- Keep student and grading extraction isolated even when workers process different PDFs concurrently.

## Processing stages

1. **Discover and validate** — recursively find PDFs under `raw/` (for example, `raw/signal/318/1.pdf`); validate that each file can be opened; calculate a checksum; assign anonymous IDs. Treat each PDF as one attempt. Persist its attempt ID so reruns keep it stable; create a new attempt ID if the source file changes. Never use a real student identifier in public output filenames.
2. **Exclude identity page and render** — never process the first page, which contains student credentials. Keep the source PDF unchanged and render only pages 2 onward with PyMuPDF at configurable 200–300 DPI. Keep original 1-based page numbers in evidence and records. Record source page count and render settings without extracting first-page content. If no pages remain, fail the job and produce no CSV. Handle scanned PDFs without a text layer.
3. **Prepare red-ink evidence and student view** — use OpenCV and NumPy to detect both red hue ranges and red-channel dominance. Keep the untouched render and a candidate red mask. Starting from red-channel grayscale, replace confident red pixels with local paper brightness only when their color-connected component contains a strong red seed and reliable paper evidence exists. Protect nearby non-red writing, short suspected crossings, and very dark mixed ink; never reconstruct hidden handwriting. Save an actual-removal mask separately from candidate evidence. The overlap/uncertainty mask includes retained red candidates and nearby writing. A red mask identifies likely red pixels, not their author or meaning; uncertain or undetected marks can remain. See [red-mark removal](red-mark-removal.md) for the implemented method and limitations.
4. **Map questions** — use known exam metadata and layout rules first. Record question IDs, page spans, and bounding boxes. Mark uncertain mappings for review instead of guessing. Preserve an answer whose question cannot be mapped as a response row with a blank question ID and `needs_review` status.
5. **Extract student answers** — run an isolated Codex CLI pass using student-view crops only. Transcribe handwriting, symbols, and math faithfully; preserve order, errors, and incomplete steps. Reference diagrams through retained image evidence. Mark unreadable spans as uncertain.
6. **Extract teacher grading** — run a separate Codex CLI pass using original crops and red masks. Record literal marks, comments, raw scores, and possible maximum scores. Do not infer a score from checkmarks or evaluate the student's work. Use a null interpreted score when meaning or question association is ambiguous.
7. **Validate and review** — validate structured outputs, page references, mappings, score bounds when known, and separation of student and grading fields. Put only uncertain or failed rows in the review queue; do not require review of every extracted row or CSV. A parsed result is not human-verified. Preserve human corrections as revisions.
8. **Export** — write one CSV per source PDF under `output/`, preserving its relative parent folders and filename. Keep all non-CSV working artifacts outside `output/`. Include only the approved dataset fields in the CSV, and do not treat unverified scores as verified ground truth.

## Codex CLI requirements

- Use the locally installed `codex exec` authenticated through the user's ChatGPT subscription login.
- Do not require `OPENAI_API_KEY`, `CODEX_API_KEY`, or paid API billing, and never silently switch to another provider.
- Centralize model and reasoning-effort settings. Treat them as requested defaults, check installed CLI support and model availability before a batch, and report a clear error when unavailable.
- Keep student and grading passes in separate invocations and workspaces. Never feed grading data or original graded crops to student extraction.
- Use explicit output schemas, validate responses, capture process failures, and bound retries. Quota or authentication failures must not be reported as successful extraction.
- Keep authentication local. If container workers require Codex login, mount the host authentication configuration explicitly and read-only; never bake credentials into images or put them in frontend code, source control, or logs.

## Output layout

The published output tree contains CSV files only. Each CSV corresponds to one source PDF and contains one row per student response to a question. Preserve all folders beneath `raw/` and replace the source extension with `.csv`:

```text
raw/signal/318/1.pdf
  -> output/signal/318/1.csv
```

Keep manifests, original copies if needed, page renders for pages 2 onward, masks, extracted records, logs, and resumable job state under a separate local work directory (for example, `.pipeline/`). Do not place these files in `output/`. Never store a rendered or extracted copy of page 1. Use anonymous, stable identifiers in CSV records. Include source-relative paths and original page references so records can be traced without relying on absolute local paths.

For example, `raw/signal/318/1.pdf` maps to `output/signal/318/1.csv`.

## CSV export

Use the fixed header, column meanings, and encoding rules in [CSV output format](csv-output-format.md). Write one row per student response to a question. Generate anonymous response IDs from the persisted attempt ID and question/subquestion identity; persist IDs for unmapped responses so they remain stable across reruns. Never copy teacher grading into `answer_text`. A CSV may contain many rows; only rows with `needs_review` or `failed` require human attention. Successful extraction does not require human verification before export, and `verified_score` remains blank unless a person verifies it.

## Verification and acceptance

Before claiming the pipeline works, verify with synthetic fixtures that are safe to commit. If a real sample PDF is available locally, use it only for manual quality review and do not publish its personal data.

Check PDF validation/rendering, recursive discovery under `raw/`, mirrored CSV paths (including `raw/signal/318/1.pdf` mapping to `output/signal/318/1.csv`), red-mask generation, question mapping, ambiguous score handling, student/grading isolation, resume behavior, schema validation, and CSV export. Verify that `output/` contains CSV files only. Report which checks ran and whether real Codex authentication or sample-PDF accuracy was not verified.
