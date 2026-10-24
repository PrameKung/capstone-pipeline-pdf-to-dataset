"""Minimal parallel PDF worker used to establish the local job flow."""

from __future__ import annotations

import argparse
import hashlib
import json
import logging
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path
from typing import Any

LOGGER = logging.getLogger("pipeline.worker")


def discover_pdfs(raw_dir: Path) -> list[Path]:
    """Find PDFs recursively in deterministic order."""
    if not raw_dir.is_dir():
        return []
    return sorted(path for path in raw_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf")


def _job_key(raw_dir: Path, pdf_path: Path) -> str:
    """Use relative path and content hash so changed PDFs become new jobs."""
    digest = hashlib.sha256()
    with pdf_path.open("rb") as source:
        for chunk in iter(lambda: source.read(1024 * 1024), b""):
            digest.update(chunk)
    identity = f"{pdf_path.relative_to(raw_dir).as_posix()}:{digest.hexdigest()}"
    return hashlib.sha256(identity.encode("utf-8")).hexdigest()


def _unprocessed_pdfs(raw_dir: Path, state_dir: Path) -> list[Path]:
    jobs_dir = state_dir / "jobs"
    candidates = []
    for pdf_path in discover_pdfs(raw_dir):
        key = _job_key(raw_dir, pdf_path)
        if not (jobs_dir / f"{key}.json").exists() and not (jobs_dir / f"{key}.lock").exists():
            candidates.append(pdf_path)
    return candidates


def process_pdf(raw_dir: Path, state_dir: Path, pdf_path: Path) -> bool:
    """Atomically claim a PDF, inspect it, and print the minimal work action."""
    key = _job_key(raw_dir, pdf_path)
    jobs_dir = state_dir / "jobs"
    jobs_dir.mkdir(parents=True, exist_ok=True)
    status_path = jobs_dir / f"{key}.json"
    lock_path = jobs_dir / f"{key}.lock"

    try:
        lock_fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False

    try:
        os.close(lock_fd)
        if status_path.exists():
            return False

        import pymupdf

        with pymupdf.open(pdf_path) as document:
            page_count = document.page_count
            if page_count < 2:
                LOGGER.warning("Skipping %s: no pages remain after excluding credential page 1", pdf_path)
                _write_done_marker(status_path, {"source": pdf_path.relative_to(raw_dir).as_posix(), "status": "failed", "error": "no pages after credential page"})
                return False

        relative_path = pdf_path.relative_to(raw_dir).as_posix()
        print(f"Processing {relative_path} (pages 2-{page_count})", flush=True)
        _write_done_marker(status_path, {"source": relative_path, "page_count": page_count, "status": "completed"})
        return True
    except Exception:
        LOGGER.exception("Failed to inspect PDF: %s", pdf_path)
        _write_done_marker(status_path, {"source": pdf_path.relative_to(raw_dir).as_posix(), "status": "failed", "error": "PDF validation failed"})
        return False
    finally:
        lock_path.unlink(missing_ok=True)


def _write_done_marker(path: Path, value: dict[str, Any]) -> None:
    temporary = path.with_suffix(f".{os.getpid()}.tmp")
    temporary.write_text(json.dumps(value, sort_keys=True), encoding="utf-8")
    os.replace(temporary, path)


def run_worker(
    raw_dir: Path,
    state_dir: Path,
    *,
    concurrency: int,
    poll_seconds: float,
    once: bool,
) -> None:
    """Consume discovered PDFs concurrently, safely across worker replicas."""
    if concurrency < 1:
        raise ValueError("concurrency must be at least 1")

    raw_dir = raw_dir.resolve()
    state_dir = state_dir.resolve()
    state_dir.mkdir(parents=True, exist_ok=True)
    pending: set[Future[bool]] = set()

    with ThreadPoolExecutor(max_workers=concurrency, thread_name_prefix="pdf-worker") as pool:
        while True:
            completed = {future for future in pending if future.done()}
            for future in completed:
                future.result()
            pending.difference_update(completed)
            capacity = concurrency - len(pending)
            for pdf_path in _unprocessed_pdfs(raw_dir, state_dir)[:capacity]:
                pending.add(pool.submit(process_pdf, raw_dir, state_dir, pdf_path))

            if once:
                if not pending:
                    return
                continue

            time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Run the local parallel PDF pipeline worker")
    parser.add_argument("--raw-dir", type=Path, default=Path(os.environ.get("RAW_DIR", "../raw")))
    parser.add_argument(
        "--state-dir",
        type=Path,
        default=Path(os.environ.get("PIPELINE_DATA_DIR", "../.pipeline")),
    )
    parser.add_argument(
        "--workers",
        type=int,
        default=int(os.environ.get("PIPELINE_WORKERS", "2")),
    )
    parser.add_argument(
        "--poll-seconds",
        type=float,
        default=float(os.environ.get("PIPELINE_POLL_SECONDS", "5")),
    )
    parser.add_argument("--once", action="store_true", help="Process current inputs, then exit")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    run_worker(args.raw_dir, args.state_dir, concurrency=args.workers, poll_seconds=args.poll_seconds, once=args.once)


if __name__ == "__main__":
    main()
