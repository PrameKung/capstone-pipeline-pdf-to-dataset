# Pipeline package

Keep the processing code independent from HTTP routes. API handlers and command-line entry points should call these pipeline services rather than implement processing themselves.

```text
pipeline/
  pdf/       discover and validate PDFs, render eligible pages, detect red ink, map questions
  codex/     check CLI availability and run isolated extraction passes
  dataset/   validate response records and write the fixed CSV format
  jobs/      persist job state, claim work safely, and run resumable worker jobs
```

Suggested module responsibilities:

- `pdf/`: input discovery and checksums; rendering pages 2 onward while retaining original page numbers; red masks and student views; question-region mapping.
- `codex/`: centralized model/effort configuration, CLI capability checks, schema-constrained invocation, and separate student/grading prompts.
- `dataset/`: response schemas, student/grading merge and validation, and atomic CSV writing that preserves the last successful export on failure.
- `jobs/`: `.pipeline/` state, checkpoints, atomic worker claiming, cancellation, retries, and concurrency limits shared by local Docker worker replicas.

The worker entrypoint is `python -m app.pipeline.jobs.worker`. For `raw/signal/318/1.pdf`, the inspectable job folder is `.pipeline/jobs/signal/318/1/`. It contains `pages-without-first.pdf`, untouched renders under `grading-view/`, likely-red masks under `red-masks/`, red-reduced grayscale images under `student-view/` (derived from the red channel), possible overlap masks under `overlap-candidates/`, and `preprocessing.json`. Render filenames retain original page numbers (for example, `page-002.png`). Opaque claim markers are stored separately under `.pipeline/claims/` so the job folders stay easy to inspect. Answer extraction and CSV export are not implemented yet.

Keep source PDFs and generated CSVs out of this package. Inputs live under `raw/`, published CSVs under `output/`, and local state and intermediates under `.pipeline/`.
