# Pipeline package

Keep the processing code independent from HTTP routes. API handlers and command-line entry points should call these pipeline services rather than implement processing themselves.

```text
pipeline/
  pdf/       discover PDFs and create prepared copies without page 1
  codex/     check CLI availability and run isolated extraction passes
  dataset/   validate response records and write the fixed CSV format
  jobs/      persist job state, claim work safely, and run resumable worker jobs
```

Suggested module responsibilities:

- `pdf/`: input discovery; creating a working PDF from pages 2 onward while leaving the source unchanged.
- `codex/`: centralized model/effort configuration, CLI capability checks, schema-constrained invocation, and separate student/grading prompts.
- `dataset/`: response schemas, student/grading merge and validation, and atomic CSV writing that preserves the last successful export on failure.
- `jobs/`: `.pipeline/` state, checkpoints, atomic worker claiming, cancellation, retries, and concurrency limits shared by local Docker worker replicas.

The worker entrypoint is `python -m app.pipeline.jobs.worker`. For `raw/signal/318/1.pdf`, the inspectable job folder is `.pipeline/jobs/signal/318/1/`. It contains only `pages-without-first.pdf`. Worker completion markers are stored separately under `.pipeline/claims/`. Page rendering, answer extraction, and CSV export are not implemented on this branch.

Keep source PDFs and generated CSVs out of this package. Inputs live under `raw/`, published CSVs under `output/`, and local state and intermediates under `.pipeline/`.
