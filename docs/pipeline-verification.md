# Markdown-to-CSV verification

Task 5 was checked with inline Python programs and temporary synthetic inputs.
No test files, real exam content, or generated datasets were added to the repository.
These are recorded verification results, not a retained automated regression suite.

## Checks completed

| Area | Verification and result |
| --- | --- |
| CLI | Actual `python -m app.pipeline.jobs.worker --once --export-only` subprocesses succeeded with nested inputs and configurable temporary roots. |
| CSV content | Exact 36-column header; Thai text, commas, quotation marks, multiline LaTeX, and cross-page answer text survived CSV round-trip. |
| Missing/unmapped data | Unknown questions remained separate with blank question IDs; missing scores stayed blank; an empty response set produced a header-only CSV. |
| Internal records | Original pages `[2,3]`, unmatched grading, raw score notation, null verified scores, and legacy provenance warnings remained in the saved JSON run. |
| Cache | Unchanged jobs did not rewrite CSVs. Corrupted CSVs were rebuilt. Markdown and metadata changes triggered exports. |
| Validation failure | Malformed JSON in supplied metadata caused a nonzero CLI exit and a failure record, preserving the previous CSV byte-for-byte. Corrected metadata recovered and cleared the failure record. |
| Page exclusion | Page-1 Markdown was rejected and left the previous CSV unchanged. This checks parser behavior, not PDF preparation. |
| Publication failure | An injected final CSV rename error preserved the previous file. The next run recovered despite the prewritten checksum marker. |
| Concurrent processes | Three independent CLI processes sharing state/output published the same changed source exactly once. |
| Crash recovery | A separate process held the job lock, was killed, and released the lock through the OS; the next export succeeded. |
| Source changes | Changing a previously exported legacy source caused export-only to fail and retain the old CSV. |
| Output layout | Output contained CSV files only, with no leftover temporary CSVs after handled failures. |
| Filename collisions | Inputs with the same stem and differently cased PDF extensions were rejected before export. |
| Docker configuration | `docker compose config --quiet` succeeded. Parsed configuration confirmed worker output/state mounts and the metadata path; no containers were launched. |

Earlier task checks also exercised parser duplicates/unclear content, stable IDs,
changed-source attempt IDs, numeric/rubric validation, and the normal worker flow
with synthetic extraction stubs. This pass did not repeat live OCR or claim new
extraction-accuracy evidence.

## How verification was isolated

Python commands ran from `backend/` using the existing virtual environment. All
fixtures and outputs lived in temporary directories and were removed afterward.
Synthetic `.pdf` paths contained checksum fixtures, not readable exam PDFs:
export-only uses their bytes for source hashing and does not parse them. The
normal extraction-flow checks used stubs and did not invoke Codex.

The programs called the public worker CLI, read CSVs with Python's `csv` module,
inspected internal JSON, compared output bytes and modification times, launched
concurrent subprocesses, and injected a rename failure. No private repository
input was read or changed by these checks.

## Remaining verification and functional limits

- Real PDF preparation/rendering and live Codex OCR were not run in this task.
  A previous local PyMuPDF import failed because `libstdc++.so.6` was unavailable;
  export-only remains independent of that dependency. Resolve the local runtime
  library issue or use a working container runtime before a full extraction run.
- Docker configuration validation does not establish successful image builds,
  authentication mounts, volume permissions, or resource enforcement at runtime.
- Teacher/student separation and transcription accuracy require independent
  evaluation. The current student extraction pass still sees graded images.
- Crash checks establish Linux file-lock release and handled-publication recovery,
  not guarantees against filesystem corruption or machine power loss.
- Legacy Markdown cannot prove its historical source association. Its first
  export records a warning; deleting/corrupting local state loses that evidence.
- Response ID stability assumes unchanged OCR labels/section structure. Changed
  grouping may require reconciliation.
- Human review, verified-score entry, and per-pass extraction checkpoints are not
  implemented. Scores, rubrics, and reference material are supplied metadata.

For usage and field mapping, see the [README](../README.md),
[behavior](pipeline-behavior.md), and [CSV schema](csv-output-format.md).
