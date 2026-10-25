# Pipeline implementation status

## Implemented

- Recursive PDF discovery, concurrent local workers, and shared job locks.
- Prepared PDFs excluding page 1; source files remain unchanged.
- Separate student/grading OCR Markdown passes through Codex CLI.
- Markdown parsing, cross-page grouping, and conservative question matching.
- Exact 36-column record building with supplied metadata, stable IDs for unchanged
  response structure, checksums, and internal provenance.
- Record validation with failed/review statuses and preserved supplied values.
- Rubric-aware teacher score decisions mapped to expected_score after checks, with
  raw evidence and extracted/verified score distinctions retained internally.
- Atomic CSV publication, mirrored output paths, round-trip validation, and
  preservation of the previous export on pre-publication failures.
- Existing-Markdown export without OCR, content-aware caches, source/configuration
  invalidation, internal failure records, and local Docker output mounts.

## Remaining limitations

- The student OCR pass still receives graded images; it does not meet the intended
  input-isolation requirement. Prompts alone do not establish isolation.
- Legacy Markdown has no historical source/configuration manifest. Export-only
  retains provenance warnings; normal processing regenerates those jobs.
- Unresolved response identities use page/occurrence order; changed OCR structure
  can require ID reconciliation.
- No review UI or human-verification workflow; review issues are in internal JSON.
- OCR retries rerun both passes; successful individual passes are not checkpointed.
- Synthetic inline checks covered parsing, records, validation, export, and worker
  integration with extraction stubs. Task 5 additionally checked real export-only
  CLI subprocesses, concurrent claims, lock-owner termination, and publication
  failure recovery. Docker Compose configuration validates; live extraction
  accuracy and Docker runtime behavior remain unverified.

See the README for export commands, metadata inputs, caching, and upgrade notes.

See [verification results](pipeline-verification.md) for the checked cases and limits.
