# OCR review prompt additions

Goal: Add the approved preparation/transcription/completeness instructions to the
existing student extraction request without changing the Markdown or CSV schema.
Spec: ../../../../capstone-reviewer/docs/ocr-preparation-and-review-prompts.md

Scope: same-pass page survey, evidence-localized uncertainty, graph/cancellation
handling, and a final completeness check. Independent audit calls, JSON sidecars,
image processing changes and automatic retries remain separate future work.

- [x] Add prompt blocks and include them only in the student request.
- [x] Bump OCR_VERSION from 12 to 13 so normal extraction refreshes old caches.
- [x] Document supported behavior and remaining scope in README and proposal.
- [x] Verify syntax, actual request assembly with synthetic input and stubbed CLI,
      parser compatibility, and run the existing test suite.

Constraints: preserve original page numbering, redaction, student errors, teacher
score isolation, existing confidence comment and literal [unclear] review flag.
No new model calls, actual dataset reruns, Docker starts, commits or pushes.

Baseline: 11 existing tests pass on Windows; test_preprocess fails to import
because the existing worker depends on Unix fcntl. This predates the change.
Do not change worker locking or add a fake production fcntl implementation.
Prompt text assertions alone cannot measure OCR quality; no such accuracy claim.

Validation after change: syntax and git diff --check pass. Offline smoke invoked
real extract_pdf with stubbed rendering/CLI (platform modules replaced only in
the temporary test process); all three blocks reached student but not grading.
Parser preserved source page/subpart, incorrect math, strikethrough and [unclear]
review flag, and separated confidence metadata. CSV remains 38 columns.
Existing unittest suite: 11 pass, test_preprocess import still fails for fcntl
on Windows. No PDF/lock/live-model validation claimed for the offline smoke.
