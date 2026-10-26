"""Accept evidence-backed teacher score decisions, never grade student work."""

import hashlib
import json
from decimal import Decimal, InvalidOperation


def number(value):
    if isinstance(value, bool) or not isinstance(value, (int, float, str)):
        return None
    try:
        result = Decimal(str(value))
        return result if result.is_finite() else None
    except InvalidOperation:
        return None


def rubric_context(question: dict) -> dict:
    fields = ("question_id", "rubric_version", "rubric_max_score", "rubric_criteria", "rubric_scoring_guide")
    context = {key: question.get(key, "") for key in fields}
    if isinstance(context["rubric_criteria"], str) and context["rubric_criteria"]:
        context["rubric_criteria"] = json.loads(context["rubric_criteria"])
    encoded = json.dumps(context, sort_keys=True, ensure_ascii=False, allow_nan=False)
    return {**context, "rubric_sha256": hashlib.sha256(encoded.encode()).hexdigest()}


def _first_page_comparison(response, decision: dict, score: Decimal) -> list[str]:
    """Compare independently extracted question awards with the page-1 table."""
    table = decision.get("first_page_score")
    if not isinstance(table, dict) or table.get("status") != "extracted":
        return ["First-page table score is missing or unclear; score requires review"]
    label = response.question_label
    if not isinstance(label, str) or label not in {str(n) for n in range(1, 14)}:
        return ["First-page table totals require a mapped parent question 1 through 13; do not allocate to subparts"]
    if (type(table.get("page")) is not int or table["page"] != 1
            or type(table.get("row")) is not int or table["row"] != int(label)):
        return ["First-page table row does not match the question; score requires review"]
    for key in ("raw_score", "evidence"):
        if not isinstance(table.get(key), str) or not table[key].strip():
            return ["First-page table score lacks raw notation or evidence; score requires review"]
    table_score = number(table.get("score"))
    if (type(table.get("score")) not in (int, float) or table_score is None or table_score < 0):
        return ["First-page table score is invalid; score requires review"]
    if table_score != score:
        return [f"Question award {score} conflicts with first-page table award {table_score}; score withheld"]
    return []


def teacher_score(response, row: dict):
    """Return (score string or None, notes). Preserve model decisions separately."""
    decisions = [g.score_decision for g in response.grading if g.score_decision is not None]
    if not decisions:
        return None, ["No teacher score decision; rerun teacher extraction to calculate expected_score"]
    if any(g.review_notes or g.section.review_notes for g in response.grading):
        return None, ["Teacher observations are ambiguous or malformed; score withheld"]
    if any(d != decisions[0] for d in decisions[1:]):
        return None, ["Conflicting teacher score decisions; score withheld"]
    if len({g.section.page for g in response.grading}) != len(response.grading):
        return None, ["Repeated grading sections on one page; score association requires review"]
    d = decisions[0]
    if d.get("status") != "extracted":
        return None, ["Teacher score requires review: " + str(d.get("reasoning", "No reason supplied"))]
    try:
        context = rubric_context(row)
    except (ValueError, TypeError):
        return None, ["Invalid rubric metadata; score withheld"]
    if (not row.get("question_id") or d.get("question_id") != row["question_id"]
            or d.get("rubric_sha256") != context["rubric_sha256"]):
        return None, ["Teacher score question/rubric does not match current metadata; rerun extraction"]
    score, maximum = number(d.get("expected_score")), number(row.get("rubric_max_score"))
    if score is None or maximum is None or maximum < 0 or not 0 <= score <= maximum:
        return None, ["Teacher score is missing, nonnumeric, or outside the rubric maximum"]
    if number(d.get("max_score")) != maximum:
        return None, ["Teacher score maximum differs from the supplied rubric"]
    for key in ("reasoning", "evidence"):
        if not isinstance(d.get(key), str) or not d[key].strip():
            return None, [f"Teacher score lacks {key}"]
    basis = d.get("basis")
    if basis not in ("explicit_total", "sum_awards", "rubric_marks"):
        return None, ["Unsupported teacher score basis"]
    raw_scores = {g.raw_score_text for g in response.grading if g.raw_score_text}
    if basis in ("explicit_total", "sum_awards") and not any(
            any(char.isdigit() for char in raw) for raw in raw_scores):
        return None, ["Numeric teacher score has no raw numeric observation"]
    if basis == "explicit_total" and len(raw_scores) > 1:
        return None, ["Multiple raw numeric observations require reconciliation before accepting a total"]
    if basis == "rubric_marks" and not any(g.marks for g in response.grading):
        return None, ["Rubric-derived teacher score has no mark observation"]
    if basis in ("sum_awards", "rubric_marks"):
        parts = d.get("components")
        if not isinstance(parts, list) or not parts:
            return None, ["Calculated teacher score lacks components"]
        criteria = context["rubric_criteria"]
        if basis == "rubric_marks" and (not isinstance(criteria, list) or not criteria):
            return None, ["Mark interpretation requires rubric criteria"]
        by_id = {}
        if isinstance(criteria, list):
            for c in criteria:
                if not isinstance(c, dict) or not isinstance(c.get("criterion_id"), str) or c["criterion_id"] in by_id:
                    return None, ["Rubric criterion IDs are invalid or duplicated"]
                by_id[c["criterion_id"]] = number(c.get("points"))
        total, seen, evidence_seen = Decimal(0), set(), set()
        for part in parts:
            if not isinstance(part, dict):
                return None, ["Invalid score component"]
            award = number(part.get("points"))
            if award is None or award < 0 or not isinstance(part.get("evidence"), str) or not part["evidence"].strip():
                return None, ["Invalid score component award/evidence"]
            if part["evidence"].strip() in evidence_seen:
                return None, ["Repeated component evidence could double-count a teacher annotation"]
            evidence_seen.add(part["evidence"].strip())
            cid = part.get("criterion_id")
            if basis == "rubric_marks" or cid is not None:
                if not isinstance(cid, str) or cid not in by_id or cid in seen:
                    return None, ["Score components have unknown or repeated criterion IDs"]
                limit = by_id[cid]
                if limit is None or not 0 <= award <= limit:
                    return None, ["Score component exceeds its criterion maximum"]
                seen.add(cid)
            total += award
        if total != score:
            return None, ["Teacher score components do not sum to the final score"]
        if basis == "rubric_marks" and seen != set(by_id):
            return None, ["Teacher marks do not account for every rubric criterion; score withheld"]
    comparison_notes = _first_page_comparison(response, d, score)
    if comparison_notes:
        return None, comparison_notes
    return str(score), []
