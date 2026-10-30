"""Read shared and per-attempt metadata with identical OCR/export precedence."""

import csv
import io
import json
import math
import re
from pathlib import Path

from app.pipeline.dataset.schema import CSV_COLUMNS, LEGACY_CSV_COLUMNS, TEMPLATE_FIELDS


def csv_question_template(data: bytes) -> dict:
    """Read only question metadata from a blank answer-sheet CSV."""
    reader = csv.DictReader(io.StringIO(data.decode("utf-8-sig")))
    if reader.fieldnames not in (list(CSV_COLUMNS), list(LEGACY_CSV_COLUMNS)):
        raise ValueError("CSV question template must use the 38-column or legacy 36-column header")
    questions = {}
    for row in reader:
        if None in row or any(value is None for value in row.values()):
            raise ValueError("CSV question template has a malformed row")
        label = row["question_id"].strip()
        canonical = re.fullmatch(r"com-eng-math-ii-2026-1-p(\d+(?:\.\d+)*)", label)
        if canonical:
            label = canonical.group(1)
        if not label or label in questions:
            raise ValueError("CSV question template needs unique, nonblank question IDs")
        fields = {key: value for key, value in row.items() if key in TEMPLATE_FIELDS}
        if row["rubric_max_score"]:
            maximum = float(row["rubric_max_score"])
            if not math.isfinite(maximum) or maximum < 0:
                raise ValueError("CSV question maximum must be finite and nonnegative")
            fields["rubric_max_score"] = maximum
        if row["rubric_criteria"]:
            fields["rubric_criteria"] = json.loads(row["rubric_criteria"])
        questions[label] = fields
    if not questions:
        raise ValueError("CSV question template must contain question rows")
    return {"questions": questions}


def load_metadata(metadata_path: Path | None, question_template: Path | None):
    metadata_bytes = metadata_path.read_bytes() if metadata_path else b"{}"
    template_bytes = question_template.read_bytes() if question_template else b"{}"
    metadata = json.loads(metadata_bytes)
    template = (csv_question_template(template_bytes)
                if question_template and question_template.suffix.lower() == ".csv"
                else json.loads(template_bytes))
    if (not isinstance(metadata, dict) or set(metadata) - {"questions", "responses"}
            or any(not isinstance(value, dict) for value in metadata.values())):
        raise ValueError("Metadata must contain questions/responses mappings")
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
    return metadata_bytes, template_bytes, questions, metadata.get("responses", {})
