# Pipeline implementation status

## Current state

The repository has a local parallel PDF preprocessing stage. It recursively discovers PDFs under `raw/`, claims each path across workers, excludes page 1, and renders pages 2 onward. Preprocessing version 11 saves untouched grading evidence, conservative student views, red-only teacher views, candidate/removal/uncertainty masks, and image-only `student.pdf`/`teacher.pdf` bundles. For `raw/signal/318/1.pdf`, inspect `.pipeline/jobs/signal/318/1/`. A `.rendered` marker is checked together with the source checksum, preprocessing version, and required artifact existence; a marker alone is insufficient. Python worker count and Docker replicas are configurable.

The Docker worker now continues into Codex OCR and Markdown. It computes source SHA-256, detects changed bytes at the same path, keeps an anonymous attempt ID per path/content, and persists per-run status/stage/timestamps. Successful page/pass JSON checkpoints survive restarts. Student and teacher images are extracted independently through ChatGPT-authenticated Codex CLI. Both passes must validate before publishing `student.md`, `teacher.md`, and combined `ocr.md` under `.pipeline/ocr/<source-stem>/`. This is page transcription, not a complete question-level CSV dataset.

## Implemented

- [x] Local `raw/`, `output/`, and `.pipeline/` layout with Docker mounts.
- [x] Recursive PDF discovery and a local thread pool.
- [x] Atomic per-path worker claims shared through `.pipeline/` across Docker replicas.
- [x] Create a working PDF without page 1, render remaining pages, and mark successful preprocessing.
- [x] FastAPI health endpoint and local Docker startup.
- [x] Documented CSV output schema and intended processing behavior.
- [x] Conservative red removal with centralized parameters, per-page counts, and separate actual-removal evidence.
- [x] Synthetic image/PDF tests and before/after previews, including multi-process job claiming and version-based reruns.
- [x] Run all 17 tests in Docker, including red-only teacher views and PDF bundle isolation. Verify backend health and smoke-test version-10 Docker worker replicas on eight synthetic PDFs with shared state.

## Remaining to reach the goal

- [x] Validate PDFs and compute source checksums; persist anonymous attempt and unique run IDs. Stable question-response IDs remain planned.
- [x] Exclude page 1 before making any derived artifact; render later pages with original page numbers retained.
- [x] Generate red-ink masks and student views without altering source PDFs; save possible overlaps for inspection.
- [ ] Map questions and preserve unmapped answers with a blank question ID and `needs_review` status.
- [x] Extract student work and teacher grading in isolated Codex CLI passes using the local subscription login, explicit schemas, and sanitized failure reporting.
- [x] Validate page records and retain source/image/configuration provenance and uncertainty flags. Review UI and human corrections remain planned.
- [ ] Write one schema-compliant CSV per PDF under the matching `output/` subfolder. Validate each new CSV and replace an older successful export only after the new run succeeds.
- [x] Durable run history, per-page resume, content-aware preprocessing, explicit failed-job retry, shared OCR capacity, and quota/authentication pause. Advisory locks release after process death.
- [x] Extend synthetic coverage to OCR orchestration: 45 unit tests passed in Docker, including diagnostics, targeted retry, blank-answer review, and cache preservation when increasing capacity. Shared-volume smoke checks at capacities 1 and 2 each processed eight PDFs and 16 page/pass calls without duplicates, reaching the requested peak concurrency.
- [ ] Add question mapping, review and CSV tests when those stages exist. Broader real-scan accuracy evaluation remains outstanding.

## Next implementation milestone

The red-removal improvement, student/teacher bundles, and OCR orchestration are integrated. Synthetic tests exercise preprocessing, checksum changes, isolated inputs, validation, literal scores, checkpoint reuse, last-success preservation, and multi-process locks/capacity. Docker CLI preflight confirmed ChatGPT login, image-capable `gpt-6.1-sol`, and `high` effort. Real-file progress/results live in `.pipeline/runs/` and `.pipeline/ocr/`. Neither schema success nor synthetic tests establish real-exam accuracy. See [red-mark removal](red-mark-removal.md) for image limitations.

Next: evaluate real OCR against human transcription, improve red separation/bleed-through handling, map question regions across pages, assign response IDs, and implement the fixed CSV schema. Combined Markdown preserves separate student/teacher sections; it does not associate ambiguous grades with answers. No CSV is generated yet.

See [pipeline overview](pipeline-overview.md), [pipeline behavior](pipeline-behavior.md), and [CSV output format](csv-output-format.md) for the target design and schema.
