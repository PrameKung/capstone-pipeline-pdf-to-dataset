# Pipeline implementation status

## Current state

The repository has a local parallel worker scaffold. It recursively finds PDFs under `raw/`, claims each path so multiple workers do not process it at once, waits one second, and prints the filename. It records a `.done` marker under `.pipeline/jobs/` so that unchanged paths are skipped on later scans. Python worker count and Docker worker replicas can be configured.

This is a scaffold only. It does not read PDF pages, extract answers, or create CSV output. A printed filename is not evidence that the exam was processed.

## Implemented

- [x] Local `raw/`, `output/`, and `.pipeline/` layout with Docker mounts.
- [x] Recursive PDF discovery and a local thread pool.
- [x] Atomic per-path worker claims shared through `.pipeline/` across Docker replicas.
- [x] One-time filename print and `.done` marker.
- [x] FastAPI health endpoint and local Docker startup.
- [x] Documented CSV output schema and intended processing behavior.

## Remaining to reach the goal

- [ ] Validate PDFs and compute source checksums; persist stable attempt, response, and run IDs.
- [ ] Exclude page 1 before making any derived artifact; render later pages with original page numbers retained.
- [ ] Generate red-ink evidence and student-only views without altering source PDFs.
- [ ] Map questions and preserve unmapped answers with a blank question ID and `needs_review` status.
- [ ] Extract student work and teacher grading in isolated Codex CLI passes using the local subscription login.
- [ ] Validate records, retain provenance, and surface only ambiguous or failed rows for review.
- [ ] Write one schema-compliant CSV per PDF under the matching `output/` subfolder. Validate each new CSV and replace an older successful export only after the new run succeeds.
- [ ] Add durable job states, resumable checkpoints, safe retries, and failure reporting. Current `.done` markers are not content-aware: changing a PDF at an already completed path does not start a new job.
- [ ] Add safe synthetic fixtures and verify the full flow, including parallel workers and the output folder containing CSV files only.

## Next implementation milestone

Replace the filename-print step with PDF validation and page rendering that skips page 1. This establishes the credential-page boundary and the evidence artifacts needed by later extraction stages.

See [pipeline overview](pipeline-overview.md), [pipeline behavior](pipeline-behavior.md), and [CSV output format](csv-output-format.md) for the target design and schema.
