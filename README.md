# Local PDF Pipeline

This testing branch recursively finds PDFs under `raw/`, removes page 1 from each working copy, and runs Codex OCR. Each job folder under `.pipeline/jobs/` contains `pages-without-first.pdf`, the separate `student-response.md` and `grading-response.md`, and combined `ocr.md`. CSV generation is not implemented on this branch.

## Run with Docker

```sh
codex login            # sign in on the host with your ChatGPT subscription
cp .env.example .env   # set CODEX_AUTH_FILE_HOST if your login file is elsewhere
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
- **Codex login:** `CODEX_AUTH_FILE_HOST` (host auth file mounted read-only into the worker)
- **Codex model:** `PIPELINE_CODEX_MODEL` and `PIPELINE_CODEX_EFFORT` (default `gpt-6.1-sol` and `high`)

Defaults use the repository's `raw/`, `output/`, and `.pipeline/` folders, run two PDFs concurrently per worker container, and poll every five seconds.

## Run the local worker once

```sh
make backend-sync
make pipeline-once
```

The worker image includes Codex CLI and uses the host's `codex login` file through a read-only mount. It makes a temporary runtime copy inside the container so CLI state does not write back to host credentials. Set `PIPELINE_WORKERS` to change concurrent PDF jobs. Docker replicas share `.pipeline/` and atomically claim each PDF path. A path is skipped after successful OCR; replacing its PDF does not currently trigger a rerun.

## Local data

- Inputs: `raw/` (read-only in Docker)
- Final CSVs: `output/` (not produced by this initial worker)
- Job markers and intermediate state: `.pipeline/`

These data directories and local credentials are excluded from Git. Never commit private exam PDFs or generated student data.

## Reading OCR Markdown

Open a response file in VS Code and use Markdown Preview (`Ctrl+Shift+V`). Extraction prompts
request `$...$` inline math and separate `$$` display blocks using standard KaTeX/MathJax
commands, with consistent page/question headings. Grading responses use `Raw score:`,
`Marks:`, and `Feedback:` fields; raw scores remain literal source notation.
Unclear content is marked `[unclear]` rather than guessed. These instructions improve
format consistency but do not guarantee identical output or valid LaTeX on every run.

Prompt changes apply to new extractions after restarting the local worker or rebuilding
the Docker worker image. Existing Markdown and completed-job markers are not migrated;
successfully cached jobs remain skipped.
