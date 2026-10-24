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

The initial worker entrypoint is `python -m app.pipeline.jobs.worker`. It recursively discovers PDFs and prints each filename once. It uses a configurable thread pool and atomic state markers under `.pipeline/jobs/` to coordinate multiple containers. This is scaffolding only; it does not yet inspect PDF contents, extract answers, or create CSVs.

Keep source PDFs and generated CSVs out of this package. Inputs live under `raw/`, published CSVs under `output/`, and local state and intermediates under `.pipeline/`.
