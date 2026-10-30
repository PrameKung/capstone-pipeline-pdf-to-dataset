"""Validate agent-reported extraction confidence without inventing estimates."""
import math


def confidence(value):
    if type(value) not in (int, float) or not 0 <= value <= 1 or not math.isfinite(value):
        return None
    return float(value)


def response_confidence(response, extracted_score):
    answer, score, notes = "", "", []
    if response.answer_text.strip():
        values = [confidence(section.answer_text_confidence) for section in response.sections]
        if values and all(value is not None for value in values):
            answer = str(min(values))
        else:
            notes.append("Answer OCR confidence missing or invalid; rerun student extraction")
    if extracted_score is not None:
        decisions = [g.score_decision for g in response.grading if g.score_decision is not None]
        value = confidence(decisions[0].get("expected_score_confidence")) if decisions else None
        if value is not None:
            score = str(value)
        else:
            notes.append("Teacher score confidence missing or invalid; rerun grading extraction")
    return answer, score, notes
