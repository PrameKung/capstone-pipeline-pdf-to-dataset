# Local PDF Pipeline

Local pipeline for processing PDFs stored under `raw/`. The worker recursively finds PDFs, removes the first page into a local working copy, and renders the remaining pages to images under `.pipeline/`. Answer extraction and CSV generation are not implemented yet.

## Run with Docker

```sh
cp .env.example .env  # optional; only needed for custom paths or worker settings
docker compose up --build
```

The API health endpoint is at `http://127.0.0.1:8000/api/health`. The worker polls `raw/` and writes coordination state under `.pipeline/`. Scale worker containers with:

```sh
docker compose up --build -d --scale worker=3
```

Stop containers with `docker compose down`.

### Environment settings

`.env.example` groups the Docker settings into three sections:

- **Host folders:** `PIPELINE_RAW_HOST_DIR`, `PIPELINE_OUTPUT_HOST_DIR`, `PIPELINE_STATE_HOST_DIR`
- **Container permissions:** `PIPELINE_UID`, `PIPELINE_GID` (default `0:0`, which maps to your user with rootless Docker)
- **Worker capacity:** `PIPELINE_WORKERS` (parallel PDFs per container) and `PIPELINE_POLL_SECONDS` (scan interval)

Defaults use the repository's `raw/`, `output/`, and `.pipeline/` folders, run two PDFs concurrently per worker container, and poll every five seconds.

## Run the local worker once

```sh
make backend-sync
make pipeline-once
```

Set `PIPELINE_WORKERS` to change the number of concurrent PDFs per worker process. Docker replicas share `.pipeline/` and atomically claim each PDF path. A path is skipped after successful rendering; replacing its PDF does not currently trigger a rerun.

## Local data

- Inputs: `raw/` (read-only in Docker)
- Final CSVs: `output/` (not produced by this initial worker)
- Job markers and intermediate state: `.pipeline/`

These data directories and local credentials are excluded from Git. Never commit private exam PDFs or generated student data.
