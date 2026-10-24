"""Prepare, extract, validate and export PDFs with shared local job locks."""

from __future__ import annotations

import argparse
import fcntl
import hashlib
import os
import time
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

from app.pipeline.dataset.export import atomic_json, export_job, read_json
from app.pipeline.dataset.records import _sha256

OCR_VERSION = 1


def find_pdfs(raw_dir: Path) -> list[Path]:
    if not raw_dir.is_dir():
        return []
    return sorted(path for path in raw_dir.rglob("*") if path.is_file() and path.suffix.lower() == ".pdf")


def _claim_key(raw_dir: Path, pdf_path: Path) -> str:
    return hashlib.sha256(pdf_path.relative_to(raw_dir).as_posix().encode("utf-8")).hexdigest()


def process_pdf(raw_dir: Path, state_dir: Path, pdf_path: Path, *,
                output_dir: Path = Path("../output"), export_only: bool = False,
                metadata_dir: Path | None = None) -> bool:
    """Serialize OCR and export for a source; OS releases locks after crashes."""
    raw_dir, state_dir, output_dir = raw_dir.resolve(), state_dir.resolve(), output_dir.resolve()
    if any(a == b or a.is_relative_to(b) or b.is_relative_to(a)
           for a, b in ((raw_dir, output_dir), (state_dir, output_dir), (raw_dir, state_dir))):
        raise ValueError("raw, output and state roots must be separate, non-nested folders")
    pdf_path = pdf_path.resolve()
    relative = pdf_path.relative_to(raw_dir)
    claims = state_dir / "claims"
    claims.mkdir(parents=True, exist_ok=True)
    key = _claim_key(raw_dir, pdf_path)
    # Old worker versions used exclusive-create .lock files. Do not overlap them.
    if (claims / f"{key}.lock").exists():
        raise RuntimeError("Legacy job lock exists; stop old workers and resolve it before upgrading")
    with (claims / f"{key}.joblock").open("a") as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return False
        failure_path = claims / f"{key}.failure.json"
        try:
            job = state_dir / "jobs" / relative.with_suffix("")
            manifest_path = claims / f"{key}.extraction.json"
            manifest = read_json(manifest_path)
            source_sha = _sha256(pdf_path)
            if export_only:
                if manifest and (manifest.get("source_sha256") != source_sha or not manifest.get("complete")):
                    raise ValueError("OCR is incomplete or belongs to another source version; run the normal worker")
            else:
                # Lazy imports keep existing-Markdown export independent of PDF/CLI libraries.
                from app.pipeline.codex.extract import DEFAULT_EFFORT, DEFAULT_MODEL, extract_pdf
                from app.pipeline.pdf.preprocess import PREPROCESS_VERSION, prepare_pdf_without_first_page
                config = {"student_model": os.environ.get("PIPELINE_CODEX_MODEL", DEFAULT_MODEL),
                          "student_effort": os.environ.get("PIPELINE_CODEX_EFFORT", DEFAULT_EFFORT),
                          "grading_model": os.environ.get("PIPELINE_CODEX_MODEL", DEFAULT_MODEL),
                          "grading_effort": os.environ.get("PIPELINE_CODEX_EFFORT", DEFAULT_EFFORT),
                          "render_dpi": int(os.environ.get("PIPELINE_CODEX_RENDER_DPI", "200"))}
                inputs = {"source_sha256": source_sha, "preprocess_version": PREPROCESS_VERSION,
                          "ocr_version": OCR_VERSION, "config": config}
                same_inputs = all(manifest.get(k) == v for k, v in inputs.items())
                complete_files = all((job / name).is_file() for name in
                                     ("pages-without-first.pdf", "student-response.md", "grading-response.md", "ocr.md"))
                if not same_inputs or not manifest.get("complete") or not complete_files:
                    atomic_json(manifest_path, {**inputs, "complete": False})
                    prepared = prepare_pdf_without_first_page(pdf_path, job)
                    extract_pdf(prepared, job / "ocr.md", state_dir)
                    if _sha256(pdf_path) != source_sha:
                        raise ValueError("Source changed during extraction; retry")
                    manifest = {**inputs, "complete": True}
                    atomic_json(manifest_path, manifest)
            metadata_path = metadata_dir / relative.with_suffix(".json") if metadata_dir else None
            if metadata_path is not None and not metadata_path.is_file():
                metadata_path = None
            changed = export_job(pdf_path, raw_dir, state_dir, output_dir,
                                 extraction=manifest, metadata_path=metadata_path)
            failure_path.unlink(missing_ok=True)
            return changed
        except Exception as exc:
            atomic_json(failure_path, {"source_pdf": relative.as_posix(), "status": "failed",
                                       "error_type": type(exc).__name__, "message": str(exc)})
            raise


def run_worker(raw_dir: Path, state_dir: Path, workers: int, poll_seconds: float, once: bool,
               *, output_dir: Path = Path("../output"), export_only: bool = False,
               metadata_dir: Path | None = None) -> int:
    if workers < 1 or poll_seconds <= 0:
        raise ValueError("workers and poll_seconds must be positive")
    raw_dir, state_dir = raw_dir.resolve(), state_dir.resolve()
    failed_inputs: dict[Path, tuple] = {}
    with ThreadPoolExecutor(max_workers=workers) as pool:
        while True:
            paths = find_pdfs(raw_dir)
            destinations = [str(path.relative_to(raw_dir).with_suffix(".csv")) for path in paths]
            if len(set(destinations)) != len(destinations):
                raise ValueError("Multiple PDFs map to the same CSV filename; rename the conflicting inputs")
            def process(path: Path) -> bool:
                try:
                    relative = path.relative_to(raw_dir)
                    job = state_dir / "jobs" / relative.with_suffix("")
                    watched = [path, job / "student-response.md", job / "grading-response.md"]
                    if metadata_dir:
                        watched.append(metadata_dir / relative.with_suffix(".json"))
                    signature = tuple(_sha256(item) if item.is_file() else None for item in watched)
                    if failed_inputs.get(path) == signature:
                        return False
                    process_pdf(raw_dir, state_dir, path, output_dir=output_dir,
                                export_only=export_only, metadata_dir=metadata_dir)
                    failed_inputs.pop(path, None)
                    return True
                except Exception as exc:
                    if "signature" in locals():
                        failed_inputs[path] = signature
                    print(f"Failed {path.relative_to(raw_dir).as_posix()}: {exc}", flush=True)
                    return False
            failures = sum(not success for success in pool.map(process, paths))
            if once:
                return failures
            time.sleep(poll_seconds)


def main() -> None:
    parser = argparse.ArgumentParser(description="Process PDFs and export validated CSV datasets")
    parser.add_argument("--raw-dir", type=Path, default=Path(os.environ.get("RAW_DIR", "../raw")))
    parser.add_argument("--state-dir", type=Path, default=Path(os.environ.get("PIPELINE_DATA_DIR", "../.pipeline")))
    parser.add_argument("--output-dir", type=Path, default=Path(os.environ.get("OUTPUT_DIR", "../output")))
    parser.add_argument("--metadata-dir", type=Path, default=Path(os.environ["PIPELINE_METADATA_DIR"]) if os.environ.get("PIPELINE_METADATA_DIR") else None)
    parser.add_argument("--export-only", action="store_true", help="Export existing Markdown without running OCR")
    parser.add_argument("--workers", type=int, default=int(os.environ.get("PIPELINE_WORKERS", "2")))
    parser.add_argument("--poll-seconds", type=float, default=float(os.environ.get("PIPELINE_POLL_SECONDS", "5")))
    parser.add_argument("--once", action="store_true", help="Process current PDFs, then exit")
    args = parser.parse_args()
    failed = run_worker(args.raw_dir, args.state_dir, args.workers, args.poll_seconds, args.once,
                        output_dir=args.output_dir, export_only=args.export_only, metadata_dir=args.metadata_dir)
    if failed:
        raise SystemExit(f"{failed} PDF job(s) failed; inspect worker logs and .pipeline/claims/*.failure.json.")


if __name__ == "__main__":
    main()
