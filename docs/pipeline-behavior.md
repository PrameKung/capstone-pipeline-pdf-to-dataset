# Pipeline behavior

This document describes the expected behavior of a local batch run. Adapt module names and commands to the implementation as it is built; do not document commands that the repository does not yet provide as if they already work.

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

Use `.pipeline/` as the default local state directory for manifests, checkpoints, logs, intermediate evidence, and temporary files. All Docker worker replicas that share a job queue must mount the same local `.pipeline/` volume.

## Parallel workers

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
3. **Prepare red-ink evidence and student view** — use OpenCV and NumPy to detect both red hue ranges. Save the untouched render, a red mask, and a grayscale student view derived from the red channel. This reduces red-mark contrast without whitening adjacent writing. Flag likely overlaps with a separate mask. A red mask identifies likely red pixels, not their meaning.
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
