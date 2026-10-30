# Local pipeline overview

Turn locally stored graded exam PDFs into question-level datasets with separate
student answers, teacher observations, and internal provenance. The backend is
Python, runs locally, and uses the locally authenticated Codex CLI for OCR. It
does not require a hosted service, database, distributed queue, or paid model API.

## Files and commands

| Location | Contents |
| --- | --- |
| `raw/` | Original PDFs, recursively discovered; read-only in Docker |
| `.pipeline/jobs/` | Prepared PDFs with the top half of page 1 redacted and saved OCR Markdown |
| `.pipeline/dataset/` | Internal JSON records, provenance, and review issues |
| `.pipeline/claims/` | Worker locks, extraction/export manifests, failure records |
| `output/` | One 36-column CSV per PDF, preserving relative folders and filename |

For example, `raw/signal/318/1.pdf` exports to `output/signal/318/1.csv`.
The source is unchanged. The top half of page 1 is permanently redacted; all
pages retain original page numbering in internal records. Private input/output,
state, and credentials stay out of Git.

Run `make pipeline-once` for the complete worker flow or `make pipeline-export`
for existing Markdown without OCR. Set raw/state/output roots and worker capacity
using the documented CLI flags or environment variables. Run backend Python and
uv commands from `backend/`.

Docker starts with `docker compose up --build` and supports worker replicas with
`docker compose up --build -d --scale worker=3`. Workers share read-only inputs,
local writable state/output mounts, and a read-only host Codex login mount. The
API binds to loopback. Stop workers before changing shared lock or capacity
configuration. See the README for setup, resource limits, and upgrade notes.

## Data quality and remaining limits

Markdown parsing preserves student work, including mistakes, and keeps teacher
grading separately. Supplied metadata maps visible question labels to dataset
IDs and supplies references, rubrics, and target scores. Conversion does not solve
questions or create missing rubrics. The teacher pass may calculate scores from
visible teacher annotations and the supplied rubric; uncertain interpretations
remain blank and require review.

Validation flags exceptions without requiring manual approval for every row.
Unmapped responses keep blank question IDs and internal `needs_review` status.
Structurally failed records block publication; ambiguous rows remain exportable.
The 36-column CSV does not carry the complete provenance or verification state;
retain its associated internal JSON run.

The implemented student OCR pass still receives graded images. This is a known
gap against the intended isolation requirement. OCR accuracy and real Docker
runtime behavior have not been verified by the synthetic conversion checks.

See [flow](pipeline-flow.md), [behavior](pipeline-behavior.md),
[status](pipeline-status.md), [CSV schema](csv-output-format.md), and
[verification](pipeline-verification.md).
