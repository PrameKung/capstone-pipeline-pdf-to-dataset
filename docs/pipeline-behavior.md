# Pipeline behavior

This document describes the implemented local workflow. The CSV schema is defined
in [CSV output format](csv-output-format.md); setup and commands are in the README.

## Discovery and ownership

The worker recursively discovers `.pdf` files case-insensitively under the
configured raw root. Input, state, and output roots must be separate and not nested.
Paths that map to the same CSV filename are rejected. PDFs are processed concurrently
with a configurable thread pool; Docker replicas share local mounts.

A persistent `.joblock` file under `.pipeline/claims/` is locked using Linux
`flock` for the entire source operation. Another worker skips a busy job. The OS
releases the lock when the owner exits; never unlink these files while workers
run. Stop old worker versions before upgrading. A leftover legacy `.lock` causes
an error rather than overlapping an old worker.

## Preparation and OCR

Normal processing computes a source checksum and compares it with the extraction
manifest, preprocessing/OCR versions, model, effort, and rendering DPI. Missing
artifacts or changed inputs regenerate the prepared PDF and both OCR passes.
Preparation requires a readable, unencrypted PDF with at least two pages. It
copies pages 2 onward and leaves the source untouched.

Codex runs student and grading passes in separate working directories and saves
separate Markdown responses. An incomplete manifest prevents export-only mode
from using a partially refreshed job. New complete manifests associate source
checksum and extraction settings with the job. Retrying interrupted extraction
currently reruns both passes rather than resuming individual successful passes.

The CLI checks local ChatGPT login, supported flags, and model/effort availability.
It does not intentionally use API-key billing or switch providers. Docker mounts
host authentication read-only. OCR still supplies full graded page renders to
both passes; separate prompts and working directories do not satisfy the intended
student-input isolation requirement.

## Parsing and record building

`--export-only` reads saved `student-response.md` and `grading-response.md` without
loading PDF or Codex dependencies. It requires original-page headings beginning
at page 2 or later. Exact visible question labels group answers across pages;
unknown labels and repeated labels on a single page stay unresolved. Unmatched
grading remains available internally. Student text, raw scores, marks, and feedback
are kept separate, without calculating scores or repairing student mathematics.

Optional metadata files mirror source paths below `--metadata-dir`, with
`questions` keyed by exact OCR label and `responses` keyed by generated response
ID. Only supplied metadata populates reference answers, rubrics, target scores,
and review fields. Missing values stay blank; a known empty list is `[]`.

Attempt IDs derive from source-relative path and PDF checksum. Response IDs use
labels or page/occurrence identity. IDs remain stable for unchanged source and
response structure; renamed PDFs or relabeled/split OCR sections can change IDs.
Each record-building run has a new run ID and an atomic JSON snapshot under
`.pipeline/dataset/`, outside the replaceable job folder.

## Validation and review

Validation checks column order, cell types, IDs, original pages, JSON arrays,
finite scores, score bounds, rubric IDs/maxima/totals, criterion statuses, and
consistent metadata for a shared question and rubric version. It never rewrites
supplied cells. Fractional totals use decimal arithmetic.

Malformed structure receives `failed`. Unmapped or ambiguous records and score
or rubric discrepancies receive `needs_review`. Existing OCR notes are retained.
Only those rows appear in the internal exceptional-response list. Run-level
provenance warnings and unmatched grading are also retained for attention.

`expected_score` is a supplied target; it does not imply verification. Extracted
and human-verified scores remain separate null internal fields until a subsequent
workflow explicitly supplies them. There is no human-review UI or verification
workflow in this branch. Validation does not establish transcription accuracy.

## Publication and caching

A validated run with no `failed` rows produces one UTF-8 CSV per source under the
matching output subfolder, including `needs_review` rows. A response-free run
produces a header-only CSV. All 36 columns remain present.

The exporter writes a temporary `.csv` on the output filesystem, checks it by
reading it back, rechecks input hashes, persists a checksum-bound cache marker,
and atomically replaces the final CSV. Errors before publication preserve the
last successful CSV. An interrupted rename leaves either the old or new complete
CSV, never a partly written final file. A marker is reusable only if the actual
CSV checksum matches. Hard termination can leave hidden staging CSVs; remove
those only after workers stop.

Source, Markdown, metadata, destination, and output checksums control export reuse.
Missing or modified CSVs are rebuilt. Keep inputs idle while a job runs; shared
locks coordinate workers, not external editors. All replicas must use the same
raw/state/output configuration.

Legacy Markdown has no source manifest. Export-only accepts its first export with
an explicit provenance warning; after a successful export, a source checksum change
requires fresh OCR. Normal processing regenerates legacy jobs lacking a current
manifest. Corrupt or manually deleted state cannot prove historical linkage.

## Failure and retry

Failures are recorded in `.pipeline/claims/*.failure.json`. One-shot runs exit
nonzero if a processed job fails, but do not wait for jobs owned by other workers.
Polling workers retry failed jobs after source/Markdown/metadata content changes
or worker restart. Successful export clears the failure file.

There is no complete durable pending/running/cancelled state machine or per-pass
resume mechanism yet. A shared file lock, extraction manifest, export checksum
marker, immutable record snapshots, and failure record provide the current
recovery behavior. See [verification](pipeline-verification.md) for checks and limits.
