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

The Docker worker entrypoint is `python -m app.pipeline.jobs.worker`. For `raw/signal/318/1.pdf`, preprocessing lives in `.pipeline/jobs/signal/318/1/`: a page-1-free PDF, untouched `grading-view/`, red/removal/uncertainty masks, student/teacher views, and `preprocessing.json` with source checksum, parameters, and counts. Filenames retain original numbering (`page-002.png`). Source claims and global OCR slots use crash-released Linux advisory locks. Durable history is in `.pipeline/runs/`. OCR artifacts live separately in `.pipeline/ocr/signal/318/1/` so preprocessing replacement cannot erase successful transcripts. CSV export remains planned.

`pdf/student_view.py` computes artifacts from a BGR image without mutating it. `StudentViewConfig` centralizes thresholds and kernels. `preprocess.py` handles I/O and stores settings/source checksum. The four-argument `make_student_view()` remains supported with optional removal/teacher paths and configuration. `cut_first_page_and_render()` accepts `student_view_config`. The CLI uses defaults; changing defaults requires bumping `PREPROCESS_VERSION`. A version marker alone no longer causes a cache hit: source checksum and artifact existence are also checked.

`codex/settings.py` owns defaults and `OCR_VERSION`; bump that version when changing prompts/schema. `client.py` validates ChatGPT auth/CLI/model support and invokes isolated, schema-constrained calls. `records.py` defines separate student/teacher contracts, validation and deterministic Markdown. `extract.py` snapshots/resizes permitted images, maintains per-page checkpoints, and publishes an immutable Markdown/JSON set only after both passes succeed. `latest.json` is the atomic publication pointer. `jobs/status.py` prints progress without exam content. See the repository README for Docker preflight, monitor and explicit retry commands.

Validation failures carry safe reason codes and schema-generated field paths through `RecordValidationError` and `OcrError`. `read_page_response()` distinguishes file/encoding/JSON/schema failures and retains exact response bytes across temporary-directory cleanup. The orchestrator saves `response.bin` (when available), `schema.json`, and `error.json` inside the failing run, then links the evidence from its manifest. No raw CLI logs are persisted. `--once --source <relative.pdf> --retry-failed` retries one claimed PDF while preserving other failed markers and all valid checkpoints. `RECORD_VALIDATION_VERSION=2` retains empty answers for identified questions with mandatory source-image review, without inserting answer text or a grade. Empty/unmapped responses remain invalid. New runs/publications record this version; prompts, JSON schema and model cache signatures stay unchanged.

Student-view creation starts from the red channel, then replaces safe, seed-connected red pixels with estimated local paper brightness. Protection masks may join short gaps in writing, but never draw reconstructed writing into the output. Uncertain pixels stay at the baseline. These masks do not guarantee that all grading marks are removed or that every overlap is detected. See the repository's `docs/red-mark-removal.md` and README for tests, preview commands, and limitations.

Keep source PDFs and generated CSVs out of this package. Inputs live under `raw/`, published CSVs under `output/`, and local state and intermediates under `.pipeline/`.

`OcrSettings.signature()` normalizes capacity to the historical value 1 for cache compatibility; runtime slot acquisition still uses the actual configured capacity. This preserves the existing batch's checkpoint/publication identities when moving to two slots. Model, effort, image-size and version changes still invalidate the relevant result identity. Compose replica settings apply only when containers are created; wait until jobs finish before recreating workers if the current calls must not be interrupted.

Version 11 also writes `teacher-view/` (detected red candidates in original colors on white) and `student.pdf`/`teacher.pdf` under each job folder. PDF bundles embed only derived images and label eligible pages with source numbering, starting at 2. They preserve page dimensions without copying original text or metadata. `make_student_view()` optionally accepts `teacher_image_path`. `StudentViewResult` includes both image arrays; uncertain red is included in the teacher view even if it remains in the student baseline. Untouched `grading-view/` remains the grading evidence.
