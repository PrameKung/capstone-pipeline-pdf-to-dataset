"""Shared dataset column order and metadata field groups."""

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

