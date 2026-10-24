"""Read shared and per-attempt metadata with identical OCR/export precedence."""

import json
from pathlib import Path


def load_metadata(metadata_path: Path | None, question_template: Path | None):
    metadata_bytes = metadata_path.read_bytes() if metadata_path else b"{}"
    template_bytes = question_template.read_bytes() if question_template else b"{}"
    metadata, template = json.loads(metadata_bytes), json.loads(template_bytes)
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
