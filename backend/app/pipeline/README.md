# Pipeline package

Keep the processing code independent from HTTP routes. API handlers and command-line entry points should call these pipeline services rather than implement processing themselves.

```text
pipeline/
  pdf/       discover PDFs and create prepared copies with the top half of page 1 redacted
  codex/     check CLI availability and run isolated extraction passes
  dataset/   validate response records and write the fixed CSV format
  jobs/      persist job state, claim work safely, and run resumable worker jobs
```

Suggested module responsibilities:

- `pdf/`: input discovery; creating a working PDF with all pages and the top half of page 1 redacted while leaving the source unchanged.
- `codex/`: centralized model/effort configuration, CLI capability checks, schema-constrained invocation, and separate student/grading prompts.
- `dataset/`: response schemas, student/grading merge and validation, and atomic CSV writing that preserves the last successful export on failure.
- `jobs/`: `.pipeline/` state, checkpoints, atomic worker claiming, cancellation, retries, and concurrency limits shared by local Docker worker replicas.

The worker entrypoint is `python -m app.pipeline.jobs.worker`. For `raw/signal/318/1.pdf`, the inspectable job folder is `.pipeline/jobs/signal/318/1/`. It contains `pages-redacted.pdf`, separate `student-response.md` and `grading-response.md` responses, and combined `ocr.md`. Worker completion markers are stored separately under `.pipeline/claims/`. Supplied metadata maps question labels to dataset IDs. The worker validates records, saves internal runs under `.pipeline/dataset/`, and publishes CSVs atomically under `output/`. Use `--export-only --once` for existing Markdown.

Keep source PDFs and generated CSVs out of this package. Inputs live under `raw/`, published CSVs under `output/`, and local state and intermediates under `.pipeline/`.

The worker reserves folders named `question/` at any depth under `raw/` for reference documents. By default,
`raw/question/question.pdf` supplies clean question context to both isolated OCR
passes; `--question-pdf` / `PIPELINE_QUESTION_PDF` overrides the path and is also
excluded from attempt discovery. Reference images are distinguished from attempt
pages and never create responses or teacher scores. Relevant rough calculations
stay; only clearly unrelated scratch work is omitted, and uncertain associations
remain unmapped for review. Reference checksums are included in cache/provenance
configuration. PDF-only mode never renders or extracts references.
