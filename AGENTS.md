# Local PDF Pipeline

## Goal

Recreate the PDF-to-dataset pipeline from `~/Documents/capstone/capstone-storage` in this repository. Inspect that project for behavior and conventions when useful, but make this project self-contained and local-first.

## Local files

- Keep downloaded source files under `raw/`. Never expect PDFs at the repository root.
- Discover input PDFs recursively so nested folders work. For example, an input may be `raw/signal/318/1.pdf`.
- Write one final CSV per input PDF under `output/`, preserving its relative folders and filename. For example, `raw/signal/318/1.pdf` produces `output/signal/318/1.csv`.
- Use the fixed CSV header and field meanings in `docs/csv-output-format.md`; output one row per question response and keep extracted scores separate from human-verified scores.
- Keep originals unchanged. Store manifests, intermediate images, extracted records, review data, and resumable job state in a separate local work area, not in `output/`. The output folder contains CSV files only.
- Exclude the first PDF page, which contains student credentials, from rendering, extraction, review artifacts, and CSV output. Keep the source PDF unchanged and retain original page numbers for all processed pages.
- Use `.pipeline/` as the default worker-state and artifact directory. Docker worker replicas must share it through a local volume.
- On reruns, replace a CSV only after the new run succeeds and validates; a failed run leaves the last successful CSV intact.
- Treat one PDF as one attempt. Keep anonymous attempt and response IDs stable across reruns; include unmapped answers with a blank question ID and `needs_review` status.
- Review is exception-based: surface only rows marked `needs_review` or `failed`. Do not require human review of every row or every CSV before export.
- Make the `raw/` and `output/` roots configurable, and exclude local PDFs, credentials, job state, and generated datasets from Git.
- Process independent PDFs concurrently with configurable Python worker capacity. Docker may run multiple worker replicas; make job claiming and CSV writes safe across workers.

## Pipeline requirements

- Process graded scanned PDFs into question-level student answers and separate teacher grading records.
- Render pages, detect likely red ink, preserve original evidence, and flag overlap or uncertainty. Never reconstruct hidden handwriting or guess unclear content.
- Transcribe student work faithfully, including incorrect or incomplete math. Do not solve, correct, or include teacher scores, feedback, or marks in student-answer inputs.
- Transcribe grading marks without regrading. Preserve raw score notation; use a null score and request review when meaning or question association is unclear.
- Keep provenance from each record to its source file, pages, run, and extraction configuration. Successfully parsed output is not human-verified ground truth.
- Validate structured records and keep grader inputs separate from teacher grading data and verified exports.
- Support resumable local jobs, cached successful stages, useful failure states, and review of ambiguous results.

## Local execution and Codex

- Use a local Python backend and the existing frontend conventions where applicable. Do not require R2, cloud hosting, a database, a distributed queue, or a paid model API.
- Keep the uv project and Python version pin in `backend/`; run backend Python and uv commands from that directory.
- Run AI extraction through the locally authenticated Codex CLI (`codex exec`) using the user's ChatGPT subscription login. Never use API-key billing or silently change providers.
- Keep model and reasoning-effort defaults centralized and configurable. Check installed CLI support, authentication mode, and model availability before processing; fail with a clear message when unavailable.
- Run student and grading extraction as isolated passes. Do not pass teacher marks, grading fields, or original graded images into student extraction.
- Keep credentials local. If Docker workers need Codex access, mount host-side authentication configuration explicitly and read-only; never bake credentials into images or place them in source control, frontend code, or logs. Limit concurrency to local resources and Codex subscription capacity.

## Working in this repository

- Inspect the existing code and this file before changing architecture. Reuse the current stack and keep changes focused.
- Use Git and GitHub for source, configuration, and documentation version control.
- Keep `raw/`, `output/`, local worker state, credentials, and private exam data out of Git; enforce this in `.gitignore`.
- Do not fabricate exam content, answers, grades, or accuracy claims. Use synthetic fixtures where tests are needed.
- Document local setup, input/output locations, worker usage, Codex login, and limitations in the project README.
- At completion, summarize changes, verification performed, and any remaining limitations.
