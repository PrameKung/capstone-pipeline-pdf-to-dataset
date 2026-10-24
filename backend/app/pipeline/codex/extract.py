"""Resumable per-page OCR of separated views and validated Markdown publication."""
from dataclasses import asdict
import hashlib
import json
from pathlib import Path
import struct

import cv2

from app.pipeline.codex.client import OcrError
from app.pipeline.codex.records import RECORD_VALIDATION_VERSION, markdown_for, schema_for, validate_record
from app.pipeline.codex.settings import OCR_VERSION, OcrSettings
from app.pipeline.jobs.state import atomic_bytes, atomic_json, atomic_text, checksum, now, ocr_slot, read_json


def digest(value) -> str:
    return hashlib.sha256(json.dumps(value, sort_keys=True).encode()).hexdigest()


def describe_inputs(job: Path, settings: OcrSettings) -> dict:
    preparation = read_json(job / "preprocessing.json")
    pages = preparation.get("processed_original_pages", [])
    if not pages or pages != list(range(2, 2 + len(pages))):
        raise OcrError("inputs", "Preprocessing has invalid source page references.")
    inputs = {}
    for kind in ("student", "teacher"):
        inputs[kind] = {}
        for page in pages:
            path = job / f"{kind}-view" / f"page-{page:03}.png"
            if not path.is_file():
                raise OcrError("inputs", "A required separated page image is missing; rerun preprocessing.")
            inputs[kind][str(page)] = checksum(path)
    return {"preprocessing_sha256": checksum(job / "preprocessing.json"),
            "source_sha256": preparation["source_sha256"], "pages": pages,
            "settings_signature": settings.signature(), "inputs": inputs}


def is_published(root: Path, description: dict) -> bool:
    latest = read_json(root / "latest.json")
    if latest.get("fingerprint") != digest(description):
        return False
    for name, expected in latest.get("outputs", {}).items():
        path = root / "runs" / latest["run_id"] / name
        if not path.is_file() or checksum(path) != expected:
            return False
    return set(latest.get("outputs", {})) == {"student.md", "teacher.md", "ocr.md", "records.json"}


def _prepare_image(source: Path, destination: Path, max_side: int) -> dict:
    # Large scanned PDFs can render to 100+ megapixels. Reduced PNG decoding
    # bounds peak memory before the final resize; the full evidence stays intact.
    with source.open("rb") as handle:
        header = handle.read(24)
    if header[:8] != b"\x89PNG\r\n\x1a\n":
        raise OcrError("inputs", "OCR input is not a PNG image.")
    width, height = struct.unpack(">II", header[16:24])
    reduction = next((factor for factor in (8, 4, 2) if max(width, height) / factor >= max_side), 1)
    flag = {1: cv2.IMREAD_COLOR, 2: cv2.IMREAD_REDUCED_COLOR_2,
            4: cv2.IMREAD_REDUCED_COLOR_4, 8: cv2.IMREAD_REDUCED_COLOR_8}[reduction]
    image = cv2.imread(str(source), flag)
    if image is None:
        raise OcrError("inputs", "A separated page image cannot be decoded.")
    scale = min(1.0, max_side / max(image.shape[:2]))
    if scale < 1:
        image = cv2.resize(image, (max(1, round(image.shape[1] * scale)), max(1, round(image.shape[0] * scale))), interpolation=cv2.INTER_AREA)
    destination.parent.mkdir(parents=True, exist_ok=True)
    temporary = destination.with_name(destination.stem + ".tmp.png")
    if not cv2.imwrite(str(temporary), image):
        raise OcrError("inputs", "Could not prepare an OCR page image.")
    temporary.replace(destination)
    return {"original_size": [width, height], "submitted_size": [image.shape[1], image.shape[0]],
            "submitted_sha256": checksum(destination)}


def extract_document(job: Path, root: Path, state: Path, settings: OcrSettings,
                     client, manifest: dict, manifest_path: Path, description: dict, *, source_pdf: Path | None = None) -> dict:
    fingerprint = digest(description)
    run_id = manifest["run_id"]
    run_dir = root / "runs" / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    manifest.update(ocr_version=OCR_VERSION, ocr_fingerprint=fingerprint,
                    record_validation_version=RECORD_VALIDATION_VERSION,
                    extraction_config=asdict(settings), codex_version=client.version,
                    total_page_passes=len(description["pages"]) * 2, completed_page_passes=0)
    records = {"student": [], "teacher": []}
    provenance = []
    preparation = read_json(job / "preprocessing.json")
    for kind in ("student", "teacher"):
        for page in description["pages"]:
            manifest.update(stage=f"ocr_{kind}", source_page=page, updated_at=now())
            atomic_json(manifest_path, manifest)
            page_key = digest({"kind": kind, "page": page,
                               "image": description["inputs"][kind][str(page)],
                               "settings": settings.signature(), "cli": client.version})
            cache = root / "checkpoints" / f"{page_key}.json"
            image = root / "inputs" / kind / f"{page_key}.png"
            saved = read_json(cache)
            record = saved.get("record")
            try:
                validate_record(record, kind, page)
                if not image.is_file() or checksum(image) != saved["image"]["submitted_sha256"]:
                    record = None
            except (ValueError, TypeError, KeyError):
                record = None
            cached = record is not None
            if record is None:
                source = job / f"{kind}-view" / f"page-{page:03}.png"
                image_info = _prepare_image(source, image, settings.max_image_side)
                with ocr_slot(state, settings.capacity):
                    if (state / "ocr-paused.json").exists():
                        raise OcrError("paused", "OCR is paused after an authentication/quota failure; explicitly retry after resolving it.")
                    try:
                        record = validate_record(client.extract(kind, page, image), kind, page)
                    except OcrError as exc:
                        if exc.code == "invalid_output":
                            evidence = run_dir / "failures" / f"{kind}-page-{page:03}"
                            details = dict(exc.details)
                            details.update(run_id=run_id, kind=kind, source_page=page,
                                           source_sha256=description["source_sha256"],
                                           input=image.relative_to(root).as_posix(),
                                           submitted_sha256=image_info["submitted_sha256"],
                                           checkpoint_key=page_key, model=settings.model,
                                           effort=settings.effort, codex_version=client.version, created_at=now())
                            details["record_validation_version"] = RECORD_VALIDATION_VERSION
                            try:
                                if exc.response is not None:
                                    atomic_bytes(evidence / "response.bin", exc.response)
                                    details["response_sha256"] = checksum(evidence / "response.bin")
                                atomic_json(evidence / "schema.json", schema_for(kind))
                                atomic_json(evidence / "error.json", details)
                                manifest["failure_evidence"] = evidence.relative_to(state).as_posix()
                            except OSError:
                                # Preserve the validation reason even when disk/permissions
                                # prevent evidence retention. Never log subprocess output.
                                manifest["failure_evidence_error"] = "write_failed"
                            manifest["error_details"] = exc.details
                            atomic_json(manifest_path, manifest)
                        if exc.code in ("quota", "authentication"):
                            atomic_json(state / "ocr-paused.json", {"code": exc.code, "at": now()})
                        raise
                saved = {"kind": kind, "source_page": page, "key": page_key,
                         "source_image_sha256": description["inputs"][kind][str(page)],
                         "image": image_info, "record": record, "created_at": now()}
                atomic_json(cache, saved)
            records[kind].append(record)
            provenance.append({"kind": kind, "source_page": page, "checkpoint": str(cache.relative_to(root)),
                               "input": str(image.relative_to(root)), "image": saved["image"], "cached": cached})
            manifest["completed_page_passes"] += 1
            manifest["updated_at"] = now()
            atomic_json(manifest_path, manifest)
            print(f"OCR {run_id[:8]} {kind} page {page}: {'cached' if cached else 'saved'} "
                  f"({manifest['completed_page_passes']}/{manifest['total_page_passes']})", flush=True)

    # Detect changes during extraction before publishing a complete set.
    if describe_inputs(job, settings) != description:
        raise OcrError("inputs_changed", "Preprocessing inputs changed during OCR; previous Markdown was preserved.")
    if source_pdf is not None and checksum(source_pdf) != description["source_sha256"]:
        raise OcrError("source_changed", "Source PDF changed during OCR; previous Markdown was preserved.")
    manifest.update(stage="markdown", updated_at=now())
    atomic_json(manifest_path, manifest)
    banner = (f"Run: {run_id}\n\nSource SHA-256: {description['source_sha256']}\n\n"
              "Automatically transcribed; not human-verified. Original page 1 is excluded.\n\n")
    student = "# Student transcription\n\n" + banner + markdown_for("student", records["student"])
    teacher = "# Teacher transcription\n\n" + banner + markdown_for("teacher", records["teacher"])
    combined = "# Combined OCR\n\n" + banner + "## Student answers\n\n" + markdown_for("student", records["student"], heading_level=3)
    combined += "\n## Teacher grading\n\n" + markdown_for("teacher", records["teacher"], heading_level=3)
    # Combining is presentation only: no inferred cross-pass question matching.
    for name, content in (("student.md", student), ("teacher.md", teacher), ("ocr.md", combined)):
        atomic_text(run_dir / name, content)
    atomic_json(run_dir / "records.json", {"run_id": run_id, "attempt_id": manifest["attempt_id"],
                "record_validation_version": RECORD_VALIDATION_VERSION,
                "source_relative_path": manifest["source_relative_path"], "description": description,
                "provenance": provenance, "records": records,
                "preprocessing_uncertainty": preparation.get("page_statistics", {})})
    needs_review = any(item["needs_review"] for pages in records.values() for record in pages for item in record["items"])
    needs_review |= any(record["page_notes"] for pages in records.values() for record in pages)
    latest = {"run_id": run_id, "fingerprint": fingerprint, "status": "needs_review" if needs_review else "completed",
              "record_validation_version": RECORD_VALIDATION_VERSION,
              "published_at": now(), "outputs": {name: checksum(run_dir / name) for name in ("student.md", "teacher.md", "ocr.md", "records.json")}}
    atomic_json(run_dir / "publication.json", latest)
    # latest.json is the atomic authoritative pointer to an immutable set.
    # Friendly copies are conveniences; readers requiring a coherent set use it.
    for name in ("student.md", "teacher.md", "ocr.md"):
        atomic_text(root / name, (run_dir / name).read_text(encoding="utf-8"))
    atomic_json(root / "latest.json", latest)
    return latest
