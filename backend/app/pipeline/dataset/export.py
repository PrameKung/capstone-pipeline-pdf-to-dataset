"""Validated CSV publication and content-aware export caching."""

from __future__ import annotations

import csv
import hashlib
import json
import os
import tempfile
from pathlib import Path

from app.pipeline.dataset.records import _sha256, build_job_records
from app.pipeline.dataset.schema import CSV_COLUMNS

EXPORT_VERSION = 1


def atomic_json(path: Path, value: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, name = tempfile.mkstemp(prefix=".state-", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(name, path)
    finally:
        Path(name).unlink(missing_ok=True)


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (FileNotFoundError, ValueError):
        return {}


def export_job(source: Path, raw_dir: Path, state_dir: Path, output_dir: Path,
               *, extraction: dict, metadata_path: Path | None = None,
               question_template: Path | None = None) -> bool:
    """Export under the caller's shared job lock; return False for a cache hit."""
    relative = source.relative_to(raw_dir)
    destination = (output_dir / relative.with_suffix(".csv")).resolve()
    if not destination.is_relative_to(output_dir) or destination == source:
        raise ValueError("CSV destination must stay inside output_dir")
    key = hashlib.sha256(relative.as_posix().encode()).hexdigest()
    marker = state_dir / "claims" / f"{key}.export.json"
    previous = read_json(marker)
    job = state_dir / "jobs" / relative.with_suffix("")
    paths = {name: (job / name).resolve() for name in ("student-response.md", "grading-response.md")}
    if any(not path.is_relative_to(state_dir) for path in paths.values()):
        raise ValueError("OCR files must stay inside state_dir")
    metadata_bytes = metadata_path.read_bytes() if metadata_path else b"{}"
    metadata = json.loads(metadata_bytes)
    if not isinstance(metadata, dict) or set(metadata) - {"questions", "responses"}:
        raise ValueError("Metadata must be an object with only questions and responses mappings")
    if any(not isinstance(value, dict) for value in metadata.values()):
        raise ValueError("Metadata questions and responses must be mappings")
    template_bytes = question_template.read_bytes() if question_template else b"{}"
    template = json.loads(template_bytes)
    if (not isinstance(template, dict) or set(template) - {"questions"}
            or not isinstance(template.get("questions", {}), dict)):
        raise ValueError("Question template must contain only a questions mapping")
    questions = dict(template.get("questions", {}))
    if any(not isinstance(value, dict) for value in questions.values()):
        raise ValueError("Each template question must be a field mapping")
    for label, fields in metadata.get("questions", {}).items():
        if not isinstance(fields, dict):
            raise ValueError("Each metadata question must be a field mapping")
        questions[label] = {**questions.get(label, {}), **fields}
    fingerprint = {
        "version": EXPORT_VERSION, "source_sha256": _sha256(source),
        "markdown": {name: _sha256(path) for name, path in paths.items()},
        "metadata_sha256": hashlib.sha256(metadata_bytes).hexdigest(),
        "template_sha256": hashlib.sha256(template_bytes).hexdigest(),
        "extraction": extraction,
    }
    if not extraction and previous and previous.get("inputs", {}).get("source_sha256") != fingerprint["source_sha256"]:
        raise ValueError("Source changed since the legacy Markdown export; rerun OCR before exporting")
    if (previous.get("inputs") == fingerprint and previous.get("destination") == str(destination)
            and destination.is_file() and previous.get("csv_sha256") == _sha256(destination)):
        return False
    result = build_job_records(
        source, raw_dir=raw_dir, state_dir=state_dir,
        questions=questions, response_metadata=metadata.get("responses"),
        extraction_config=extraction.get("config"),
        extraction_source_sha256=extraction.get("source_sha256"),
    )
    if result["validation"]["has_failures"]:
        raise ValueError(f"Record validation failed; inspect {result['record_path']}")
    # Confirm the builder used the same snapshot that will be cached.
    if (result["source_sha256"] != fingerprint["source_sha256"]
            or any(result["artifacts"][name]["sha256"] != digest
                   for name, digest in fingerprint["markdown"].items())):
        raise ValueError("Inputs changed while building records; retry")
    destination.parent.mkdir(parents=True, exist_ok=True)
    # A .csv staging file on the destination filesystem allows atomic rename even
    # when output/ and .pipeline/ are different Docker mounts.
    fd, name = tempfile.mkstemp(prefix=f".{destination.stem}-", suffix=".csv", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="") as stream:
            writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
            writer.writeheader()
            writer.writerows(record["row"] for record in result["records"])
            stream.flush()
            os.fsync(stream.fileno())
        with open(name, encoding="utf-8", newline="") as stream:
            reader = csv.reader(stream)
            if next(reader) != list(CSV_COLUMNS):
                raise ValueError("CSV header validation failed")
            for record in result["records"]:
                if next(reader, None) != [str(record["row"][column]) for column in CSV_COLUMNS]:
                    raise ValueError("CSV round-trip validation failed")
            if next(reader, None) is not None:
                raise ValueError("Unexpected CSV rows")
        if (_sha256(source) != fingerprint["source_sha256"]
                or any(_sha256(paths[n]) != digest for n, digest in fingerprint["markdown"].items())
                or (metadata_path and metadata_path.read_bytes() != metadata_bytes)
                or (question_template and question_template.read_bytes() != template_bytes)):
            raise ValueError("Inputs changed during export; previous CSV retained")
        csv_sha = _sha256(Path(name))
        atomic_json(marker, {"inputs": fingerprint, "destination": str(destination),
                             "csv_sha256": csv_sha, "record_path": result["record_path"],
                             "run_id": result["run_id"]})
        # A marker is only a cache hit when the published CSV checksum matches.
        # Persist it first so a state-write failure cannot replace the old CSV.
        os.replace(name, destination)
    finally:
        Path(name).unlink(missing_ok=True)
    print(f"Exported {relative.with_suffix('.csv').as_posix()} "
          f"({len(result['records'])} rows; {result['validation']['counts']['needs_review']} need review)", flush=True)
    return True
