# CSV output format

Write one CSV per input PDF under `output/`, mirroring the folders below `raw/` and replacing `.pdf` with `.csv`. For example, `raw/signal/318/1.pdf` produces `output/signal/318/1.csv`.

The output folder contains CSV files only. Store images, manifests, job state, and other intermediate files in the local work area, outside `output/`.

Exclude the first page of each PDF because it contains student credentials. Do not create records from that page. Page references in `source_pages` use the original 1-based PDF numbering, so the first eligible page is page 2.

Treat each PDF as one attempt. Persist its anonymous `attempt_id` and each `response_id` in `.pipeline/` so IDs remain stable across reruns. A changed source PDF receives a new attempt ID. A rerun replaces the existing CSV only after successful processing and validation; a failed or cancelled run leaves the previous CSV intact.

## Rows and header

Write one row per student response to a question, with this fixed header order:

```csv
response_id,attempt_id,question_id,subquestion_id,answer_text,answer_evidence_path,extracted_score,raw_score_text,verified_score,max_score,human_feedback,human_markings_json,extraction_status,grading_status,review_notes,source_pdf,source_pages,source_sha256,run_id,student_model,student_effort,grading_model,grading_effort
```

| Column | Meaning |
| --- | --- |
| `response_id` | Stable anonymous ID for this question response. |
| `attempt_id` | Stable anonymous ID for the source attempt/PDF. |
| `question_id` | Mapped question identifier; blank if unresolved. |
| `subquestion_id` | Subquestion identifier when known; otherwise blank. |
| `answer_text` | Faithful student transcription in Markdown/LaTeX. Must contain no teacher marks, scores, or feedback. |
| `answer_evidence_path` | Relative path in the local work area to the supporting crop/image; blank if unavailable. |
| `extracted_score` | Score interpreted from teacher notation; blank if absent or ambiguous. This is not verified ground truth. |
| `raw_score_text` | Literal score notation as written, such as `2+2`; blank if absent. |
| `verified_score` | Score explicitly approved or corrected by a human reviewer; blank until verified. |
| `max_score` | Maximum score when established by trusted question metadata; otherwise blank. |
| `human_feedback` | Teacher feedback transcribed from the page; blank if absent or unreadable. |
| `human_markings_json` | JSON array of literal marking labels and/or descriptions, e.g. `["checkmark","cross"]`. |
| `extraction_status` | `extracted`, `needs_review`, or `failed` for student transcription/mapping. |
| `grading_status` | `extracted`, `needs_review`, `human_verified`, or `failed` for grading data. |
| `review_notes` | Brief uncertainty or reviewer note; blank when none. |
| `source_pdf` | Source path relative to `raw/`, e.g. `signal/318/1.pdf`. |
| `source_pages` | JSON array of original source page numbers, excluding page 1, e.g. `[2,3]`. |
| `source_sha256` | SHA-256 checksum of the source PDF. |
| `run_id` | Unique ID for the processing run. |
| `student_model`, `student_effort` | Codex model and reasoning effort used for student extraction. |
| `grading_model`, `grading_effort` | Codex model and reasoning effort used for grading extraction. |

If an answer is found but its question cannot be identified, keep it as a row. Leave `question_id` blank, record the known source page(s), set `extraction_status` to `needs_review`, and keep its anonymous response ID stable across retries and reruns.

## Encoding rules

- Use UTF-8, one header row, and standard CSV quoting.
- Keep all columns in the fixed order, even when values are unavailable.
- Represent missing scalar values as empty cells.
- Encode array or object values as JSON strings inside their CSV cells.
- Keep teacher scores and feedback out of `answer_text`.
- Leave `verified_score` empty until a human reviewer explicitly approves or corrects the score.
- Human review is exception-based. Only rows with `needs_review` or `failed` require attention; a CSV with many successfully extracted rows does not need a full manual pass before export.

Column values in this document are illustrative, not extracted exam data.
