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

The original PDF remains unchanged. The full pipeline is intended to produce `output/signal/318/1.csv` using the fixed schema in [CSV output format](csv-output-format.md). This testing branch stops earlier: each source produces only `.pipeline/jobs/signal/318/1/pages-without-first.pdf`. Docker worker replicas share `.pipeline/` through a local volume.

The first PDF page contains student credentials and must be excluded from pipeline processing and all derived artifacts. Keep the original PDF unchanged in `raw/`; the prepared PDF contains pages 2 onward. If a PDF has no pages after the first, mark it as failed with a clear reason.

The `raw/` and `output/` roots should be configurable. Keep source PDFs, generated artifacts, job state, and credentials out of version control.

## Docker

Copy `.env.example` to `.env` to configure Docker. Its settings are grouped into host folder paths, container UID/GID permissions, and worker concurrency/poll interval. Start the backend API and polling worker with `docker compose up --build`; the API binds to `127.0.0.1:8000`. Scale worker containers with `docker compose up --build -d --scale worker=3`. All workers share the read-only `raw/` mount and writable `.pipeline/` state mount. Stop services with `docker compose down`.

The worker recursively discovers PDFs, atomically claims each source, and saves pages 2 onward as a working PDF under `.pipeline/`. Run `make pipeline-once` to process current PDFs locally and exit. Rendering, answer extraction, and CSV creation are not implemented on this branch.

Use Git and GitHub to version source code, configuration templates, and documentation. Ensure `raw/`, `output/`, local worker state, credentials, and private exam data are ignored by Git.

## Parallel processing

Process independent PDFs concurrently using a configurable number of Python workers. Docker may run multiple worker replicas to increase throughput. Workers must claim jobs atomically, use isolated temporary workspaces, and prevent concurrent runs from overwriting the same CSV. Set worker capacity according to local CPU, memory, and Codex subscription limits.

Treat each PDF as one attempt and keep its anonymous IDs stable across reruns. A rerun may replace the corresponding CSV only after it finishes and validates successfully; if it fails, preserve the last successful CSV. Keep an answer with an uncertain question mapping as a CSV row with a blank question ID and `needs_review` status.

## Processing flow

```text
local input PDFs
  -> validate and identify source
  -> remove page 1
  -> save prepared PDF under .pipeline/jobs/
```

## Data quality rules

- Transcribe what the student wrote, including errors and incomplete work. Do not solve or correct it.
- Do not guess obscured content or reconstruct handwriting hidden beneath red ink.
- Record teacher marks and scores as observations; do not re-grade the answer.
- Preserve raw score notation. Use a null interpreted score when it is unclear.
- Retain page and image evidence, extraction configuration, source checksum, and run identity.
- AI output is an extraction proposal, not verified ground truth. Uncertain results require human review.
- Review only exceptions marked `needs_review` or `failed`; do not require reviewing every row or every CSV before export.

See the [pipeline flow diagram](pipeline-flow.md), [pipeline status](pipeline-status.md) for current implementation progress and next milestone, and [pipeline behavior](pipeline-behavior.md) for detailed stage requirements and expected run contents.
