"""Preprocess discovered PDFs using a small parallel worker pool."""

from __future__ import annotations

import argparse
import hashlib
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from app.pipeline.pdf.preprocess import cut_first_page_and_render


def find_pdfs(raw_dir: Path) -> list[Path]:
    if not raw_dir.is_dir():
        return []
    return sorted(path for path in raw_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf")


def _claim_key(raw_dir: Path, pdf_path: Path) -> str:
    relative_path = pdf_path.relative_to(raw_dir).as_posix()
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()


def process_pdf(raw_dir: Path, state_dir: Path, pdf_path: Path) -> bool:
    """Cut the first page and render remaining pages, coordinating workers."""
    claims_dir = state_dir / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
    key = _claim_key(raw_dir, pdf_path)
    done_file = claims_dir / f"{key}.rendered"
    lock_file = claims_dir / f"{key}.lock"

    try:
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False

    try:
        os.close(lock_fd)
        if done_file.exists():
            return False
        relative_path = pdf_path.relative_to(raw_dir)
        job_dir = state_dir / "jobs" / relative_path.with_suffix("")
        images = cut_first_page_and_render(pdf_path, job_dir)
        print(f"Prepared {len(images)} pages for {relative_path.as_posix()} in {job_dir}", flush=True)
        done_file.touch()
        return True
    finally:
        lock_file.unlink(missing_ok=True)


def run_worker(raw_dir: Path, state_dir: Path, workers: int, poll_seconds: float, once: bool) -> None:
    if workers < 1:
        raise ValueError("workers must be at least 1")

    raw_dir = raw_dir.resolve()
    state_dir = state_dir.resolve()
    running: set[Future[bool]] = set()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            finished = {future for future in running if future.done()}
            for future in finished:
                future.result()
            running.difference_update(finished)

            claims_dir = state_dir / "claims"
            candidates = [
                path
                for path in find_pdfs(raw_dir)
                if not (claims_dir / f"{_claim_key(raw_dir, path)}.rendered").exists()
                and not (claims_dir / f"{_claim_key(raw_dir, path)}.lock").exists()
            ]
            available = workers - len(running)
            for path in candidates[:available]:
                running.add(pool.submit(process_pdf, raw_dir, state_dir, path))

            if once:
                if not running:
                    return
            else:
                time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Remove the first page and render remaining PDF pages")
    parser.add_argument("--raw-dir", type=Path, default=Path(os.environ.get("RAW_DIR", "../raw")))
    parser.add_argument("--state-dir", type=Path, default=Path(os.environ.get("PIPELINE_DATA_DIR", "../.pipeline")))
    parser.add_argument("--workers", type=int, default=int(os.environ.get("PIPELINE_WORKERS", "2")))
    parser.add_argument("--poll-seconds", type=float, default=float(os.environ.get("PIPELINE_POLL_SECONDS", "5")))
    parser.add_argument("--once", action="store_true", help="Process current PDFs, then exit")
    args = parser.parse_args()
    run_worker(args.raw_dir, args.state_dir, args.workers, args.poll_seconds, args.once)


if __name__ == "__main__":
    main()
