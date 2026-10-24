"""Build dataset rows and persist internal provenance; no CSV export or regrading."""

from __future__ import annotations

import hashlib
import json
import os
import tempfile
import uuid
from collections import Counter
from dataclasses import asdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from app.pipeline.dataset.markdown import match_markdown

CSV_COLUMNS = tuple(
    "source,subject,topic,concept_tags,question_type,question_id,rubric_version,"
    "question_text,answer,solution,rubric_max_score,rubric_criteria,rubric_scoring_guide,"
    "question_review_status,reviewer_feedback,reviewed_answer,reviewed_solution,"
    "student_id,response_id,response_origin,attempt_id,sample_type,answer_text,"
    "expected_score,rubric_analysis,covered_rubric_points,missed_rubric_points,"
    "error_categories,error_location,missed_concepts,grader_feedback,"
    "improvement_suggestion,difficulty,difficulty_reason,answer_image_path,"
    "image_review_status".split(",")
)
QUESTION_FIELDS = frozenset(CSV_COLUMNS[:17]) | {"difficulty", "difficulty_reason"}
RESPONSE_FIELDS = frozenset(CSV_COLUMNS) - QUESTION_FIELDS - {
    "response_id", "attempt_id", "answer_text",
}
JSON_FIELDS = {"rubric_criteria", "rubric_analysis", "error_categories", "missed_concepts"}


def _sha256(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def _metadata(values: Mapping[str, Any], allowed: frozenset[str]) -> dict[str, Any]:
    unexpected = set(values) - allowed
    if unexpected:
        raise ValueError(f"Unsupported metadata fields: {', '.join(sorted(unexpected))}")
    result = {}
    for key, value in values.items():
        if value is None:
            value = ""
        elif key in JSON_FIELDS and not isinstance(value, str):
            value = json.dumps(value, ensure_ascii=False, allow_nan=False)
        elif not isinstance(value, (str, int, float)) or isinstance(value, bool):
            raise ValueError(f"Metadata field {key} must be a scalar or an explicit JSON field")
        result[key] = value
    return result


def build_job_records(
    source_pdf: Path,
    *,
    raw_dir: Path = Path("../raw"),
    state_dir: Path = Path("../.pipeline"),
    questions: Mapping[str, Mapping[str, Any]] | None = None,
    response_metadata: Mapping[str, Mapping[str, Any]] | None = None,
    extraction_config: Mapping[str, Any] | None = None,
    extraction_source_sha256: str | None = None,
) -> dict[str, Any]:
    """Build and save a run from existing Markdown using supplied metadata only.

    ``questions`` maps exact OCR labels to question-level CSV fields, including
    an explicit question_id. ``response_metadata`` maps generated response IDs
    to response-level CSV fields. Scores and rubric evaluations are never inferred.
    Configuration and extraction checksum must describe the actual OCR run;
    current environment defaults are deliberately not used as historical facts.
    """
    raw_dir, state_dir, source_pdf = raw_dir.resolve(), state_dir.resolve(), source_pdf.resolve()
    relative = source_pdf.relative_to(raw_dir)
    if source_pdf.suffix.lower() != ".pdf":
        raise ValueError("Source must be a PDF under raw_dir")
    source_sha = _sha256(source_pdf)
    if extraction_source_sha256 is not None and extraction_source_sha256 != source_sha:
        raise ValueError("Source PDF differs from the supplied extraction checksum; rerun OCR")
    source_key = hashlib.sha256(relative.as_posix().encode("utf-8")).hexdigest()
    attempt_id = str(uuid.uuid5(uuid.NAMESPACE_URL, f"local-pdf-attempt:v1:{source_key}:{source_sha}"))
    job_dir = state_dir / "jobs" / relative.with_suffix("")
    markdown: dict[str, str] = {}
    artifacts = {}
    for name in ("student-response.md", "grading-response.md"):
        path = (job_dir / name).resolve()
        if not path.is_relative_to(state_dir):
            raise ValueError("Markdown artifacts must remain inside state_dir")
        content = path.read_bytes()
        markdown[name] = content.decode("utf-8")
        artifacts[name] = {
            "path": path.relative_to(state_dir).as_posix(),
            "sha256": hashlib.sha256(content).hexdigest(),
        }
    matched = match_markdown(markdown["student-response.md"], markdown["grading-response.md"])
    questions = questions or {}
    response_metadata = response_metadata or {}
    # Check supplied metadata even if an OCR label does not occur in this attempt.
    question_values = {label: _metadata(values, QUESTION_FIELDS) for label, values in questions.items()}
    supplied_responses = {key: _metadata(values, RESPONSE_FIELDS) for key, values in response_metadata.items()}
    label_counts = Counter(response.question_label for response in matched.responses)
    occurrences: Counter[str] = Counter()
    records = []
    used_labels = set()
    for response in matched.responses:
        label = response.question_label
        unique_label = label is not None and label_counts[label] == 1
        identity = json.dumps(
            ["label", label] if unique_label else ["section", label, response.source_pages],
            ensure_ascii=False, separators=(",", ":"),
        )
        occurrences[identity] += 1
        response_id = str(uuid.uuid5(uuid.UUID(attempt_id), f"{identity}:{occurrences[identity]}"))
        row: dict[str, Any] = dict.fromkeys(CSV_COLUMNS, "")
        notes = list(response.review_notes)
        for section in response.sections:
            notes.extend(section.review_notes)
        for grading in response.grading:
            notes.extend(grading.review_notes)
            notes.extend(grading.section.review_notes)
        if unique_label and label in question_values:
            row.update(question_values[label])
            used_labels.add(label)
        if not row["question_id"]:
            notes.append("No supplied question ID mapping; response remains unmapped")
        row.update(supplied_responses.get(response_id, {}))
        row.update(response_id=response_id, attempt_id=attempt_id, answer_text=response.answer_text)
        records.append({
            "row": row,
            "internal": {
                "question_label": label,
                "source_pages": response.source_pages,
                "student_sections": [asdict(section) for section in response.sections],
                "teacher_grading": [asdict(grading) for grading in response.grading],
                "extracted_score": None,
                "verified_score": None,
                "status": "needs_review" if notes else "extracted",
                "review_notes": list(dict.fromkeys(notes)),
            },
        })
    unused_responses = set(supplied_responses) - {record["row"]["response_id"] for record in records}
    if unused_responses:
        raise ValueError("Supplied response metadata contains IDs not found in this attempt")
    run_id = str(uuid.uuid4())
    result = {
        "schema_version": 1,
        "run_id": run_id,
        "created_at": datetime.now(timezone.utc).isoformat(),
        "attempt_id": attempt_id,
        "source_pdf": relative.as_posix(),
        "source_sha256": source_sha,
        "extraction_source_verified": extraction_source_sha256 is not None,
        "extraction_config": dict(extraction_config or {}),
        "artifacts": artifacts,
        "metadata": {"questions": questions, "responses": response_metadata},
        "unused_question_labels": sorted(set(question_values) - used_labels),
        "review_notes": ([] if extraction_source_sha256 is not None else [
            "Existing OCR has no supplied source checksum; linkage to the current PDF is unverified",
        ]) + ([] if extraction_config else ["Historical extraction configuration was not supplied"]),
        "records": records,
        "unmatched_grading": [asdict(grading) for grading in matched.unmatched_grading],
    }
    # Store outside jobs/: PDF preparation may replace an entire job folder.
    destination = state_dir / "dataset" / source_key / attempt_id / f"{run_id}.json"
    result["record_path"] = destination.relative_to(state_dir).as_posix()
    payload = json.dumps(result, ensure_ascii=False, indent=2, allow_nan=False) + "\n"
    if _sha256(source_pdf) != source_sha:
        raise ValueError("Source changed while building records; retry after OCR completes")
    destination.parent.mkdir(parents=True, exist_ok=True)
    fd, staging = tempfile.mkstemp(prefix=".records-", suffix=".tmp", dir=destination.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as stream:
            stream.write(payload)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(staging, destination)
    finally:
        Path(staging).unlink(missing_ok=True)
    return result
