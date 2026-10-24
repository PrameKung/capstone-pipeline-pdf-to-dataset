"""Prepare and extract discovered PDFs using a parallel worker pool."""

from __future__ import annotations

import argparse
import hashlib
import os
import time
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from app.pipeline.codex.extract import extract_pdf
from app.pipeline.pdf.preprocess import PREPROCESS_VERSION, prepare_pdf_without_first_page

OCR_VERSION = 1


def find_pdfs(raw_dir: Path) -> list[Path]:
    if not raw_dir.is_dir():
        return []
    return sorted(path for path in raw_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf")


def _claim_key(raw_dir: Path, pdf_path: Path) -> str:
    relative_path = pdf_path.relative_to(raw_dir).as_posix()
    return hashlib.sha256(relative_path.encode("utf-8")).hexdigest()


def _is_current(done_file: Path, version: int) -> bool:
    try:
        return done_file.read_text(encoding="utf-8").strip() == str(version)
    except OSError:
        return False


def process_pdf(raw_dir: Path, state_dir: Path, pdf_path: Path) -> bool:
    """Prepare and extract one PDF, coordinating local worker replicas."""
    claims_dir = state_dir / "claims"
    claims_dir.mkdir(parents=True, exist_ok=True)
    key = _claim_key(raw_dir, pdf_path)
    done_file = claims_dir / f"{key}.prepared"
    ocr_done_file = claims_dir / f"{key}.ocr"
    lock_file = claims_dir / f"{key}.lock"

    try:
        lock_fd = os.open(lock_file, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    except FileExistsError:
        return False

    try:
        os.close(lock_fd)
        if _is_current(done_file, PREPROCESS_VERSION) and _is_current(ocr_done_file, OCR_VERSION):
            return False
        relative_path = pdf_path.relative_to(raw_dir)
        job_dir = state_dir / "jobs" / relative_path.with_suffix("")
        if not _is_current(done_file, PREPROCESS_VERSION):
            prepared_pdf = prepare_pdf_without_first_page(pdf_path, job_dir)
            done_file.write_text(f"{PREPROCESS_VERSION}\n", encoding="utf-8")
            print(f"Prepared {relative_path.as_posix()} at {prepared_pdf}", flush=True)
        else:
            prepared_pdf = job_dir / "pages-without-first.pdf"

        ocr_path = job_dir / "ocr.md"
        if not _is_current(ocr_done_file, OCR_VERSION) or not ocr_path.is_file():
            extract_pdf(prepared_pdf, ocr_path, state_dir)
            ocr_done_file.write_text(f"{OCR_VERSION}\n", encoding="utf-8")
            print(f"OCR complete for {relative_path.as_posix()} at {ocr_path}", flush=True)
        return True
    finally:
        lock_file.unlink(missing_ok=True)


def run_worker(raw_dir: Path, state_dir: Path, workers: int, poll_seconds: float, once: bool) -> int:
    if workers < 1:
        raise ValueError("workers must be at least 1")

    raw_dir = raw_dir.resolve()
    state_dir = state_dir.resolve()
    running: dict[Future[bool], Path] = {}
    failed: set[Path] = set()

    with ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            finished = {future for future in running if future.done()}
            for future in finished:
                pdf_path = running.pop(future)
                try:
                    future.result()
                except Exception as exc:
                    failed.add(pdf_path)
                    print(f"Failed {pdf_path.relative_to(raw_dir).as_posix()}: {exc}", flush=True)

            claims_dir = state_dir / "claims"
            candidates = [
                path
                for path in find_pdfs(raw_dir)
                if not (
                    _is_current(claims_dir / f"{_claim_key(raw_dir, path)}.prepared", PREPROCESS_VERSION)
                    and _is_current(claims_dir / f"{_claim_key(raw_dir, path)}.ocr", OCR_VERSION)
                )
                and path not in failed
                and not (claims_dir / f"{_claim_key(raw_dir, path)}.lock").exists()
            ]
            available = workers - len(running)
            for path in candidates[:available]:
                running[pool.submit(process_pdf, raw_dir, state_dir, path)] = path

            if once:
                if not running:
                    return len(failed)
            else:
                time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare PDFs without their first page")
    parser.add_argument("--raw-dir", type=Path, default=Path(os.environ.get("RAW_DIR", "../raw")))
    parser.add_argument("--state-dir", type=Path, default=Path(os.environ.get("PIPELINE_DATA_DIR", "../.pipeline")))
    parser.add_argument("--workers", type=int, default=int(os.environ.get("PIPELINE_WORKERS", "2")))
    parser.add_argument("--poll-seconds", type=float, default=float(os.environ.get("PIPELINE_POLL_SECONDS", "5")))
    parser.add_argument("--once", action="store_true", help="Process current PDFs, then exit")
    args = parser.parse_args()
    failed = run_worker(args.raw_dir, args.state_dir, args.workers, args.poll_seconds, args.once)
    if failed:
        raise SystemExit(f"{failed} PDF job(s) failed; inspect the worker log and rerun.")


if __name__ == "__main__":
    main()
