# Local pipeline overview

## Purpose

Turn locally stored, already-graded exam PDFs into traceable datasets. Each PDF contains student handwriting and teacher markings. The pipeline preserves the source, extracts student work and teacher grading in separate branches, and marks uncertain results for human review.

This project runs locally. It does not require Cloudflare R2, a hosted service, a database, or a paid model API.

## Input and output folders

Put downloaded PDFs under the configured raw input directory, defaulting to `raw/`. PDFs may be nested by course, exam, or batch. The pipeline searches recursively; do not place inputs at the repository root. For example, `raw/signal/318/1.pdf` is a valid input.

For each source file, write one CSV under the configured output directory, defaulting to `output/`. Preserve the source's relative parent folders and use the same filename with a `.csv` extension. For example:

```text
  raw/
    signal/
      318/
        1.pdf
        2.pdf
  output/
    signal/
      318/
        1.csv
        2.csv
```

The original PDF remains unchanged. The example input `raw/signal/318/1.pdf` produces `output/signal/318/1.csv`. The `output/` tree contains CSV files only, using the fixed schema in [CSV output format](csv-output-format.md). Keep manifests, rendered page images, masks, extracted records, review state, and resumable job data in `.pipeline/` by default. Docker worker replicas share this directory through a local volume. Use stable relative paths in records where possible; avoid exposing machine-specific absolute paths.

The first PDF page contains student credentials and must be excluded from pipeline processing and all derived artifacts. Do not render, extract, review, or export its contents. Keep the original PDF unchanged in `raw/`; process pages 2 onward and preserve their original 1-based page numbers. If a PDF has no pages after the first, mark it as failed with a clear reason and produce no CSV.

The `raw/` and `output/` roots should be configurable. Keep source PDFs, generated artifacts, job state, and credentials out of version control.

## Docker

Copy `.env.example` to `.env` to configure Docker. Its settings are grouped into host folder paths, container UID/GID permissions, and worker concurrency/poll interval. Start the backend API and polling worker with `docker compose up --build`; the API binds to `127.0.0.1:8000`. Scale worker containers with `docker compose up --build -d --scale worker=3`. All workers share the read-only `raw/` mount and writable `.pipeline/` state mount. Stop services with `docker compose down`.

The initial worker recursively discovers PDFs, excludes page 1, atomically claims each source, and prints a processing line. Run `make pipeline-once` to process current PDFs locally and exit. This first parallel pass only validates and prints; extraction and CSV generation are not implemented yet.

Use Git and GitHub to version source code, configuration templates, and documentation. Ensure `raw/`, `output/`, local worker state, credentials, and private exam data are ignored by Git.

## Parallel processing

Process independent PDFs concurrently using a configurable number of Python workers. Docker may run multiple worker replicas to increase throughput. Workers must claim jobs atomically, use isolated temporary workspaces, and prevent concurrent runs from overwriting the same CSV. Set worker capacity according to local CPU, memory, and Codex subscription limits.

Treat each PDF as one attempt and keep its anonymous IDs stable across reruns. A rerun may replace the corresponding CSV only after it finishes and validates successfully; if it fails, preserve the last successful CSV. Keep an answer with an uncertain question mapping as a CSV row with a blank question ID and `needs_review` status.

## Processing flow

```text
local input PDFs
  -> validate and identify source
  -> render PDF pages
  -> detect likely red ink and create a student view
  -> map question regions
  -> extract student answers and teacher grading separately
  -> validate, preserve evidence, and flag uncertainty
  -> human review
  -> export datasets
```

Student extraction sees only the student view and permitted question metadata. Grading extraction sees the original graded crop and red-ink evidence. Keep those passes isolated so teacher scores and comments cannot enter student-answer text or grader inputs.

## Data quality rules

- Transcribe what the student wrote, including errors and incomplete work. Do not solve or correct it.
- Do not guess obscured content or reconstruct handwriting hidden beneath red ink.
- Record teacher marks and scores as observations; do not re-grade the answer.
- Preserve raw score notation. Use a null interpreted score when it is unclear.
- Retain page and image evidence, extraction configuration, source checksum, and run identity.
- AI output is an extraction proposal, not verified ground truth. Uncertain results require human review.
- Review only exceptions marked `needs_review` or `failed`; do not require reviewing every row or every CSV before export.

See [pipeline behavior](pipeline-behavior.md) for detailed stage requirements and expected run contents.
