# CSV output format

Write one CSV per input PDF under `output/`, mirroring the folders below `raw/` and replacing `.pdf` with `.csv`. For example, `raw/signal/318/1.pdf` produces `output/signal/318/1.csv`.

The output folder contains CSV files only. Store images, manifests, job state, and other intermediate files in the local work area, outside `output/`.

Exclude the first page of each PDF because it contains student credentials. Do not create records from that page. Retain original 1-based PDF page numbering in internal provenance; the first eligible page is page 2.

Treat each PDF as one attempt. Persist its anonymous `attempt_id` and each `response_id` in `.pipeline/` so IDs remain stable across reruns. A changed source PDF receives a new attempt ID. A rerun replaces the existing CSV only after successful processing and validation; a failed or cancelled run leaves the previous CSV intact.

## Rows and header

The output schema matches all 36 columns of `kru_grade_ai_statistics_final_student_style.csv`, in their original order. This is the sole CSV header for this format; do not append pipeline-specific columns. The reference contains 184 rows, all labeled as synthetic responses.

Write one row per question response, repeating the associated question and rubric metadata. Preserve this exact column order:

```csv
source,subject,topic,concept_tags,question_type,question_id,rubric_version,question_text,answer,solution,rubric_max_score,rubric_criteria,rubric_scoring_guide,question_review_status,reviewer_feedback,reviewed_answer,reviewed_solution,student_id,response_id,response_origin,attempt_id,sample_type,answer_text,expected_score,rubric_analysis,covered_rubric_points,missed_rubric_points,error_categories,error_location,missed_concepts,grader_feedback,improvement_suggestion,difficulty,difficulty_reason,answer_image_path,image_review_status
```

| Column | Cell format | Meaning and conversion rule |
| --- | --- | --- |
| `source` | Text | Source exam or dataset name; distinct from the pipeline's source PDF path. |
| `subject` | Text | Subject assessed, such as `Statistics`. |
| `topic` | Text | Specific topic assessed by the question. |
| `concept_tags` | Text; blank in reference | Concept labels. The reference establishes no list encoding. |
| `question_type` | Text | Observed values: `Calculation`, `Statistical Analysis`, `Hypothesis Testing`, `Experiment Design`. |
| `question_id` | String ID | Stable question identifier shared by responses to the same question. |
| `rubric_version` | String; blank in reference | Rubric version identifier, if supplied. |
| `question_text` | Multiline text | Complete question, preserving Markdown tables and math notation. |
| `answer` | Text | Reference answer, separate from the student response. |
| `solution` | Multiline text | Reference explanation or worked solution. |
| `rubric_max_score` | Number | Maximum score for the question. |
| `rubric_criteria` | JSON array of objects | Criteria with `criterion_id`, `description`, `points`, `partial_credit`, and optional `rubric_policy`. |
| `rubric_scoring_guide` | Text | Overall scoring instructions and acceptable alternatives. |
| `question_review_status` | Text; blank in reference | Question review state. No allowed status vocabulary is established. |
| `reviewer_feedback` | Text; blank in reference | Reviewer comments about the question or reference material. |
| `reviewed_answer` | Text; blank in reference | Reviewer-approved reference answer, if explicitly supplied. |
| `reviewed_solution` | Multiline text; blank in reference | Reviewer-approved solution, if explicitly supplied. |
| `student_id` | String ID; blank in reference | Anonymous student identifier, if available. Never derive it from the excluded credentials page. |
| `response_id` | Unique string ID | Identifier for an individual response. Preserve existing IDs. |
| `response_origin` | Text | Response origin. Every reference row uses `synthetic`; do not apply that value to real student work. |
| `attempt_id` | String ID | Attempt identifier. The reference uses `1` throughout; pipeline attempts require stable anonymous IDs per PDF. |
| `sample_type` | Text | Observed response categories: `full`, `partial`, `wrong`. Preserve supplied labels rather than infer them during conversion. |
| `answer_text` | Multiline text | Faithful student response, including errors and incomplete work. Exclude reference solutions, teacher marks, and grading feedback. |
| `expected_score` | Number | Explicitly supplied target score, or a validated teacher-derived award from raw scores/marks and the supplied rubric; fractional values are allowed. This field does not establish human verification; keep extracted and human-verified scores distinct in internal records. |
| `rubric_analysis` | JSON array of objects | Per-criterion evaluation with `criterion_id`, `max_score`, `awarded_score`, `status`, and `reasoning`. Observed statuses: `covered`, `partial`, `missed`. |
| `covered_rubric_points` | Semicolon-separated IDs | Fully covered criteria, such as `C1;C2`; blank when none. |
| `missed_rubric_points` | Semicolon-separated IDs | Missed criteria; blank when none. Partial criteria remain distinguished in `rubric_analysis`. |
| `error_categories` | JSON array of strings | Supplied error labels, such as `["arithmetic_error"]`; `[]` for a known empty list. |
| `error_location` | Text | Step or location containing an error; may contain semicolon-separated descriptions. |
| `missed_concepts` | JSON array of strings | Missing or misunderstood concepts; `[]` for a known empty list. |
| `grader_feedback` | Text | Supplied explanation of grading; separate from student work and literal teacher feedback. |
| `improvement_suggestion` | Text | Suggested improvement; blank if not supplied. |
| `difficulty` | Text | Observed values: `Easy`, `Medium`, `Hard`. |
| `difficulty_reason` | Text | Explanation of the question's difficulty. |
| `answer_image_path` | Path string; blank in reference | Path to an existing answer image, if supplied. This branch does not generate images. |
| `image_review_status` | Text; blank in reference | Image review state. No allowed status vocabulary is established. |

Meanings of entirely blank reference fields are inferred from their names; the reference does not demonstrate their populated values or establish status enums. Observed categories above describe this file, not an exhaustive vocabulary for other datasets.

## Conversion and validation rules

- Match Markdown fields to the exact column names above. Keep all 36 columns, even when values are unavailable.
- Repeat question-level metadata for each response. Keep reference answers, student responses, rubric evaluations, and review fields separate.
- Preserve Markdown and LaTeX as cell text, including line breaks and embedded tables. A table inside `question_text` remains in that single cell.
- Transcribe supplied values only. Do not solve questions, correct student work, invent rubric evaluations, or invent missing scores and review approvals. The teacher pass may calculate an award from unambiguous teacher annotations and the supplied rubric, with raw evidence and reasoning retained internally.
- Use empty cells for missing values. Use `[]` only when a JSON list is known to be empty, not when information is unavailable.
- Serialize JSON fields as valid JSON, then use a CSV writer for CSV escaping. Do not construct CSV rows by joining strings with commas.
- Use UTF-8 and standard CSV quoting: quote cells containing commas, quotes, or newlines, and double embedded quotation marks. Preserve IDs as strings.
- Validate unique `response_id` values and consistent question/rubric metadata for a shared question and rubric version.
- When scoring data is supplied, check that criterion IDs match the rubric, criterion awards are within their maxima, rubric points sum to `rubric_max_score`, and criterion awards sum to `expected_score`. Check that `expected_score` is between zero and `rubric_max_score`. Flag discrepancies rather than silently changing scores or regrading.
- Keep the pipeline's original-page provenance and separate extracted/human-verified score records when preparing a dataset from PDF output. This reference template alone cannot represent that complete record.

Teacher-derived awards fill `expected_score` only when it is otherwise blank. Explicit response metadata takes precedence and conflicts are flagged. Reject stale rubric fingerprints, ambiguous evidence, invalid bounds, and inconsistent component sums by leaving the derived score blank. Keep the extracted award separate from human verification. This is interpretation of teacher grading, not independent regrading.

## Pipeline metadata outside the CSV

This exact schema has no dedicated columns for raw teacher score notation, separate extracted and human-verified scores, source pages, run configuration, or extraction status. Retain those distinctions in internal pipeline records rather than overloading dataset fields or adding CSV columns. Successful parsing does not establish human verification.

Keep unmapped responses with a blank `question_id` and a stable `response_id`; track their `needs_review` state internally. Review remains exception-based for records marked `needs_review` or `failed`. Do not repurpose question or image review fields as extraction status fields.
