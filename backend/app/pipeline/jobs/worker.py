"""Content-aware local PDF workers with isolated, resumable Codex OCR."""
from __future__ import annotations

import argparse
import hashlib
import os
import time
import uuid
from concurrent.futures import Future, ThreadPoolExecutor
from pathlib import Path

from app.pipeline.codex.client import CodexClient, OcrError
from app.pipeline.codex.extract import describe_inputs, digest, extract_document, is_published
from app.pipeline.codex.settings import OcrSettings
from app.pipeline.jobs.state import atomic_json, atomic_text, checksum, claim, now, read_json
from app.pipeline.pdf.preprocess import PREPROCESS_VERSION, cut_first_page_and_render


def find_pdfs(raw_dir: Path) -> list[Path]:
    if not raw_dir.is_dir():
        return []
    return sorted(path for path in raw_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf")


def _claim_key(raw_dir: Path, pdf_path: Path) -> str:
    return hashlib.sha256(pdf_path.relative_to(raw_dir).as_posix().encode("utf-8")).hexdigest()


def _is_current(done_file: Path) -> bool:
    try:
        return done_file.read_text(encoding="utf-8").strip() == str(PREPROCESS_VERSION)
    except OSError:
        return False


def _preparation_current(job: Path, done: Path, source_hash: str) -> bool:
    summary = read_json(job / "preprocessing.json")
    if not _is_current(done) or summary.get("source_sha256") != source_hash or summary.get("preprocess_version") != PREPROCESS_VERSION:
        return False
    pages = summary.get("processed_original_pages", [])
    if not pages or pages != list(range(2, 2 + len(pages))):
        return False
    if any(not (job / name).is_file() for name in ("student.pdf", "teacher.pdf", "pages-without-first.pdf")):
        return False
    return all((job / folder / f"page-{page:03}.png").is_file()
               for page in pages for folder in ("student-view", "teacher-view", "grading-view", "red-masks", "removal-masks", "overlap-candidates"))


def process_pdf(raw_dir: Path, state_dir: Path, pdf_path: Path, *,
                ocr_settings: OcrSettings | None = None, client=None, retry_failed: bool = False) -> bool:
    """Prepare one PDF; optionally OCR. Direct Python callers default to preprocessing."""
    claims = state_dir / "claims"
    key = _claim_key(raw_dir, pdf_path)
    with claim(claims / f"{key}.guard") as acquired:
        if not acquired:
            return False
        lock_file = claims / f"{key}.lock"
        atomic_text(lock_file, "active\n")
        manifest = None
        manifest_path = None
        try:
            source_hash = checksum(pdf_path)
            token = digest({"source": source_hash, "preprocessing": PREPROCESS_VERSION,
                            "ocr": ocr_settings.signature() if ocr_settings else None})
            failure_path = claims / f"{key}.failed.json"
            if not retry_failed and read_json(failure_path).get("token") == token:
                return False
            relative = pdf_path.relative_to(raw_dir)
            job = state_dir / "jobs" / relative.with_suffix("")
            ocr_root = state_dir / "ocr" / relative.with_suffix("")
            done = claims / f"{key}.rendered"
            prepared = _preparation_current(job, done, source_hash)
            description = describe_inputs(job, ocr_settings) if prepared and ocr_settings else None
            published = bool(prepared and ocr_settings and is_published(ocr_root, description))
            runs = state_dir / "runs" / key
            # Repair a crash between publication and the final manifest update.
            for previous_path in runs.glob("*.json"):
                previous = read_json(previous_path)
                if previous.get("status") == "running":
                    latest = read_json(ocr_root / "latest.json")
                    if published and latest.get("run_id") == previous["run_id"]:
                        previous.update(status=latest["status"], stage="complete",
                                        output_location=(ocr_root / "runs" / previous["run_id"]).relative_to(state_dir).as_posix())
                    else:
                        previous.update(status="failed", error_code="interrupted")
                    previous.update(finished_at=now(), updated_at=now())
                    atomic_json(previous_path, previous)
            if prepared and (not ocr_settings or published):
                return False
            if ocr_settings and (state_dir / "ocr-paused.json").exists():
                return False
            attempt_path = state_dir / "attempts" / key / f"{source_hash}.json"
            attempt = read_json(attempt_path)
            if not attempt:
                attempt = {"attempt_id": uuid.uuid4().hex, "source_sha256": source_hash}
                atomic_json(attempt_path, attempt)
            run_id = uuid.uuid4().hex
            manifest_path = runs / f"{run_id}.json"
            manifest = {"run_id": run_id, "attempt_id": attempt["attempt_id"],
                        "source_relative_path": relative.as_posix(), "source_sha256": source_hash,
                        "preprocess_version": PREPROCESS_VERSION, "status": "running",
                        "stage": "preprocessing", "started_at": now(), "updated_at": now(),
                        "preprocessing_path": job.relative_to(state_dir).as_posix()}
            atomic_json(manifest_path, manifest)
            if not prepared:
                images = cut_first_page_and_render(pdf_path, job)
                if checksum(pdf_path) != source_hash:
                    raise OcrError("source_changed", "Source PDF changed during preparation; retry when the copy finishes.")
                atomic_text(done, f"{PREPROCESS_VERSION}\n")
                print(f"Prepared {len(images)} pages for {relative.as_posix()}", flush=True)
            if ocr_settings:
                if client is None:
                    raise OcrError("preflight", "An authenticated, preflighted OCR client is required.")
                description = describe_inputs(job, ocr_settings)
                latest = extract_document(job, ocr_root, state_dir, ocr_settings, client,
                                          manifest, manifest_path, description, source_pdf=pdf_path)
                manifest.update(status=latest["status"], stage="complete",
                                output_location=(ocr_root / "runs" / run_id).relative_to(state_dir).as_posix())
            else:
                manifest.update(status="completed", stage="preprocessing_complete")
            manifest.update(finished_at=now(), updated_at=now())
            atomic_json(manifest_path, manifest)
            failure_path.unlink(missing_ok=True)
            print(f"Run {run_id[:8]}: {manifest['status']}", flush=True)
            return True
        except Exception as exc:
            if manifest is not None:
                code = exc.code if isinstance(exc, OcrError) else "processing_failure"
                message = str(exc) if isinstance(exc, OcrError) else "Local processing failed; check PDF validity, file permissions, and free disk space."
                manifest.update(status="failed", error_code=code, error_message=message, finished_at=now(), updated_at=now())
                atomic_json(manifest_path, manifest)
                atomic_json(failure_path, {"token": token, "run_id": manifest["run_id"], "error_code": code})
            raise
        finally:
            lock_file.unlink(missing_ok=True)


def run_worker(raw_dir: Path, state_dir: Path, workers: int, poll_seconds: float, once: bool,
               *, ocr_settings: OcrSettings | None = None, client=None,
               source: Path | None = None, retry_failed: bool = False) -> int:
    if workers < 1 or poll_seconds <= 0:
        raise ValueError("workers and poll_seconds must be positive")
    raw_dir, state_dir = raw_dir.resolve(), state_dir.resolve()
    selected = None
    if source is not None:
        if source.is_absolute() or not once:
            raise ValueError("--source must be a relative PDF path and requires --once")
        selected = (raw_dir / source).resolve()
        if not selected.is_relative_to(raw_dir) or not selected.is_file() or selected.suffix.lower() != ".pdf":
            raise ValueError("--source must name an existing PDF inside the raw directory")
    running: dict[Future, Path] = {}
    attempted: set[Path] = set()
    last_considered: dict[Path, float] = {}
    failures = 0
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            for future in list(running):
                if future.done():
                    try:
                        future.result()
                    except Exception as exc:
                        failures += 1
                        code = exc.code if isinstance(exc, OcrError) else "processing_failure"
                        detail = f" ({exc.details['reason']})" if isinstance(exc, OcrError) and "reason" in exc.details else ""
                        print(f"Job failed: {code}{detail}. Inspect .pipeline/runs/; use --retry-failed after resolving it.", flush=True)
                    del running[future]
            candidates = [path for path in ([selected] if selected else find_pdfs(raw_dir))
                          if path not in running.values() and (not once or path not in attempted)]
            candidates.sort(key=lambda path: last_considered.get(path, 0))
            for path in candidates[:workers - len(running)]:
                retry_this = retry_failed and path not in attempted
                attempted.add(path)
                last_considered[path] = time.monotonic()
                running[pool.submit(process_pdf, raw_dir, state_dir, path, ocr_settings=ocr_settings,
                                    client=client, retry_failed=retry_this)] = path
            if once and not running:
                return failures or int((state_dir / "ocr-paused.json").exists() or any(
                    (state_dir / "claims" / f"{_claim_key(raw_dir, path)}.failed.json").exists() for path in attempted))
            # Avoid a tight loop in --once while subprocesses work.
            time.sleep(min(poll_seconds, 0.1) if once else poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Prepare separated PDF views and OCR them into Markdown")
    parser.add_argument("--raw-dir", type=Path, default=Path(os.environ.get("RAW_DIR", "../raw")))
    parser.add_argument("--state-dir", type=Path, default=Path(os.environ.get("PIPELINE_DATA_DIR", "../.pipeline")))
    parser.add_argument("--workers", type=int, default=int(os.environ.get("PIPELINE_WORKERS", "1")))
    parser.add_argument("--poll-seconds", type=float, default=float(os.environ.get("PIPELINE_POLL_SECONDS", "5")))
    parser.add_argument("--once", action="store_true", help="Consider current PDFs once, then exit")
    parser.add_argument("--source", type=Path, help="With --once, process only this PDF path relative to raw/")
    parser.add_argument("--preprocess-only", action="store_true", help="Skip Codex preflight and OCR")
    parser.add_argument("--preflight-only", action="store_true", help="Check Codex without submitting exam pages")
    parser.add_argument("--retry-failed", action="store_true", help="Clear the shared pause and retry each selected failed PDF once under its claim; retains checkpoints")
    args = parser.parse_args()
    settings = None
    client = None
    if args.preflight_only or (not args.preprocess_only and os.getenv("PIPELINE_OCR_ENABLED", "0") == "1"):
        settings = OcrSettings.from_env()
        client = CodexClient(settings)
        try:
            client.preflight()
        except OcrError as exc:
            print(f"Codex preflight failed ({exc.code}): {exc}", flush=True)
            raise SystemExit(2)
        print(f"Codex ready: ChatGPT, {settings.model}, {settings.effort}, {client.version}", flush=True)
    if args.preflight_only:
        return
    if args.retry_failed:
        (args.state_dir / "ocr-paused.json").unlink(missing_ok=True)
    failures = run_worker(args.raw_dir, args.state_dir, args.workers, args.poll_seconds, args.once,
                          ocr_settings=settings, client=client, source=args.source, retry_failed=args.retry_failed)
    if failures:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
