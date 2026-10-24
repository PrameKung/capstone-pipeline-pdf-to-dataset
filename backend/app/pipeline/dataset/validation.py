"""Validate supplied dataset values without correcting answers or regrading."""

from __future__ import annotations

import json
from collections import defaultdict
from decimal import Decimal, InvalidOperation
from typing import Any

from app.pipeline.dataset.schema import CSV_COLUMNS, JSON_FIELDS, QUESTION_FIELDS


def _number(value: Any) -> Decimal | None:
    if isinstance(value, bool) or not isinstance(value, (str, int, float)):
        return None
    try:
        number = Decimal(str(value))
        return number if number.is_finite() else None
    except InvalidOperation:
        return None


def _reject_constant(value: str) -> None:
    raise ValueError(f"Non-finite JSON value: {value}")


def validate_records(records: list[dict[str, Any]], *, attempt_id: str) -> dict[str, Any]:
    """Attach validation issues and internal status; leave every CSV cell unchanged.

    Structural issues are ``failed`` and block export readiness. Ambiguous or
    inconsistent supplied data is ``needs_review``. Missing optional values are
    allowed. Existing extraction notes/status are preserved. Run-level provenance
    warnings and unmatched grading remain in the enclosing run, not CSV columns.
    """
    issues: list[list[dict[str, str]]] = [[] for _ in records]

    def flag(index: int, field: str, message: str, severity: str = "needs_review") -> None:
        issue = {"field": field, "message": message, "severity": severity}
        if issue not in issues[index]:
            issues[index].append(issue)

    ids: dict[str, list[int]] = defaultdict(list)
    questions: dict[tuple[str, str], list[int]] = defaultdict(list)
    parsed_rows = []
    for index, record in enumerate(records):
        row = record["row"]
        parsed: dict[str, Any] = {}
        parsed_rows.append(parsed)
        if tuple(row) != CSV_COLUMNS:
            flag(index, "columns", "Columns must match the exact 36-column order", "failed")
        for field, value in row.items():
            if field in {"expected_score", "rubric_max_score"}:
                continue
            if not isinstance(value, str):
                flag(index, field, "Cell must be a string (missing values use an empty string)", "failed")
        for name in ("response_id", "attempt_id"):
            if not isinstance(row.get(name), str) or not row[name].strip():
                flag(index, name, "A nonempty string ID is required", "failed")
        if isinstance(row.get("response_id"), str) and row["response_id"].strip():
            ids[row["response_id"]].append(index)
        if row.get("attempt_id") != attempt_id:
            flag(index, "attempt_id", "Row attempt ID differs from its run", "failed")
        question_id, version = row.get("question_id"), row.get("rubric_version")
        if isinstance(question_id, str) and question_id.strip() and isinstance(version, str):
            questions[question_id, version].append(index)
        else:
            flag(index, "question_id", "Question mapping is missing or invalid")
        pages = record["internal"].get("source_pages")
        if (not isinstance(pages, list) or not pages
                or any(type(page) is not int or page < 2 for page in pages)):
            flag(index, "source_pages", "Original source pages must be integers starting at 2", "failed")
        for field in JSON_FIELDS:
            cell = row.get(field, "")
            if cell == "":
                continue
            try:
                value = json.loads(cell, parse_constant=_reject_constant)
                if not isinstance(value, list):
                    raise ValueError("Expected array")
                element_type = dict if field in {"rubric_criteria", "rubric_analysis"} else str
                if any(not isinstance(item, element_type) for item in value):
                    raise ValueError("Wrong array element type")
                parsed[field] = value
            except (TypeError, ValueError):
                flag(index, field, "Must be a valid JSON array with the documented element type", "failed")
        numbers = {}
        for field in ("expected_score", "rubric_max_score"):
            cell = row.get(field, "")
            if cell == "":
                continue
            number = _number(cell)
            if number is None:
                flag(index, field, "Must be a finite numeric value", "failed")
            elif number < 0:
                flag(index, field, "Score cannot be negative")
            numbers[field] = number
        maximum, expected = numbers.get("rubric_max_score"), numbers.get("expected_score")
        if maximum is not None and expected is not None and expected > maximum:
            flag(index, "expected_score", "Score exceeds rubric maximum")
        if expected is not None and maximum is None:
            flag(index, "expected_score", "Score bounds cannot be checked without a valid rubric maximum")

        criteria: dict[str, Decimal | None] = {}
        analysis: dict[str, tuple[Decimal | None, Decimal | None, Any]] = {}
        for field in ("rubric_criteria", "rubric_analysis"):
            entries = parsed.get(field)
            if entries is None:
                continue
            seen = set()
            for entry in entries:
                criterion_id = entry.get("criterion_id")
                if not isinstance(criterion_id, str) or not criterion_id.strip():
                    flag(index, field, "Each criterion requires a nonempty string criterion_id")
                    continue
                if criterion_id in seen:
                    flag(index, field, "Duplicate criterion IDs")
                seen.add(criterion_id)
                required = ({"description", "points", "partial_credit"} if field == "rubric_criteria"
                            else {"max_score", "awarded_score", "status", "reasoning"})
                if not required <= entry.keys():
                    flag(index, field, "Criterion is missing required fields")
                description = "description" if field == "rubric_criteria" else "reasoning"
                if description in entry and not isinstance(entry[description], str):
                    flag(index, field, f"{description} must be text")
                max_field = "points" if field == "rubric_criteria" else "max_score"
                points = _number(entry.get(max_field))
                if points is None or points < 0:
                    flag(index, field, f"{max_field} must be a finite nonnegative number")
                if field == "rubric_criteria":
                    criteria[criterion_id] = points
                else:
                    award = _number(entry.get("awarded_score"))
                    status = entry.get("status")
                    if award is None or award < 0 or (points is not None and award > points):
                        flag(index, field, "Criterion award must be between zero and its maximum")
                    if status not in ("covered", "partial", "missed"):
                        flag(index, field, "Criterion status is not covered, partial, or missed")
                    elif award is not None and points is not None:
                        consistent = {"covered": award == points, "missed": award == 0,
                                      "partial": Decimal(0) < award < points}
                        if not consistent[status]:
                            flag(index, field, "Criterion status conflicts with its supplied award")
                    analysis[criterion_id] = points, award, status
        if "rubric_criteria" in parsed and maximum is not None:
            points = list(criteria.values())
            if len(criteria) == len(parsed["rubric_criteria"]) and all(p is not None for p in points):
                if sum(points, Decimal(0)) != maximum:
                    flag(index, "rubric_criteria", "Criterion points do not sum to rubric maximum")
        if "rubric_analysis" in parsed:
            if "rubric_criteria" not in parsed:
                flag(index, "rubric_analysis", "Cannot check criterion associations without a valid rubric")
            else:
                if set(analysis) != set(criteria):
                    flag(index, "rubric_analysis", "Analysis criterion IDs do not match the rubric")
                for criterion_id, (points, _, _) in analysis.items():
                    if criterion_id in criteria and points != criteria[criterion_id]:
                        flag(index, "rubric_analysis", "Criterion maximum differs from rubric points")
            awards = [entry[1] for entry in analysis.values()]
            if expected is not None and len(analysis) == len(parsed["rubric_analysis"]) and all(a is not None for a in awards):
                if sum(awards, Decimal(0)) != expected:
                    flag(index, "expected_score", "Criterion awards do not sum to expected score")
        point_sets = {}
        for field, status in (("covered_rubric_points", "covered"), ("missed_rubric_points", "missed")):
            cell = row.get(field, "")
            if not isinstance(cell, str) or not cell:
                continue
            labels = [label.strip() for label in cell.split(";")]
            point_sets[field] = set(labels)
            if not all(labels) or len(set(labels)) != len(labels):
                flag(index, field, "Criterion list has empty or duplicate IDs")
            if "rubric_criteria" not in parsed:
                flag(index, field, "Cannot check criterion IDs without a valid rubric")
            elif not set(labels) <= set(criteria):
                flag(index, field, "Criterion list contains IDs absent from the rubric")
            if "rubric_analysis" in parsed:
                expected_labels = {key for key, value in analysis.items() if value[2] == status}
                if set(labels) != expected_labels:
                    flag(index, field, "Criterion list conflicts with rubric analysis")
        if point_sets.get("covered_rubric_points", set()) & point_sets.get("missed_rubric_points", set()):
            flag(index, "covered_rubric_points", "A criterion cannot be both covered and missed")

    for indices in ids.values():
        if len(indices) > 1:
            for index in indices:
                flag(index, "response_id", "Duplicate response ID", "failed")
    for indices in questions.values():
        for field in sorted(QUESTION_FIELDS):
            values = [parsed_rows[i].get(field, records[i]["row"].get(field, "")) for i in indices]
            if any(value != values[0] for value in values[1:]):
                for index in indices:
                    flag(index, field, "Conflicting metadata for the same question and rubric version")

    counts = {"extracted": 0, "needs_review": 0, "failed": 0}
    for record, row_issues in zip(records, issues):
        internal = record["internal"]
        # Keep validation results separate so repeated checks do not duplicate notes.
        previous = internal.get("validation", {})
        base_status = previous.get("input_status", internal.get("status", "extracted"))
        status = base_status
        if any(issue["severity"] == "failed" for issue in row_issues):
            status = "failed"
        elif row_issues or internal.get("review_notes"):
            status = "failed" if base_status == "failed" else "needs_review"
        internal["validation"] = {"input_status": base_status, "issues": row_issues}
        internal["status"] = status
        counts[status] += 1
    return {"counts": counts, "has_failures": counts["failed"] > 0,
            "review_response_ids": [record["row"].get("response_id", "") for record in records
                                    if record["internal"]["status"] in {"needs_review", "failed"}]}
