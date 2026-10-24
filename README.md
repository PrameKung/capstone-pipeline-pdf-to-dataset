# Local PDF Pipeline

Local worker scaffold for processing PDFs stored under `raw/`. The worker recursively discovers PDFs, excludes credential page 1, validates the remaining page count, and prints each claimed file. Extraction and CSV generation are not implemented yet.

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

## Run the local worker once

```sh
make backend-sync
make pipeline-once
```

Set `PIPELINE_WORKERS` to change the number of concurrent PDFs per worker process. Docker replicas share `.pipeline/` and atomically claim PDFs so the same unchanged source is printed once. A changed PDF is treated as a new job.

## Local data

- Inputs: `raw/` (read-only in Docker)
- Final CSVs: `output/` (not produced by this initial worker)
- Job markers and intermediate state: `.pipeline/`

These data directories and local credentials are excluded from Git. Never commit private exam PDFs or generated student data.
