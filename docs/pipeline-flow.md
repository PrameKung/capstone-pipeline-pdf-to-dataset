# Pipeline flow

The normal worker prepares, extracts, validates, and exports each PDF. The
export-only command starts from existing Markdown and never invokes Codex.

```mermaid
flowchart TD
    A[Discover PDFs recursively in raw/] --> B[Claim source with shared file lock]
    B --> C{Export only?}
    C -->|No| D{Current extraction manifest and artifacts?}
    D -->|No| E[Redact top half of page 1 and run OCR passes]
    E --> F[Save extraction manifest]
    D -->|Yes| G[Read student and grading Markdown]
    F --> G
    C -->|Yes| G
    G --> H{Export inputs and CSV checksum unchanged?}
    H -->|Yes| I[Skip cached export]
    H -->|No| J[Parse and match responses]
    J --> K[Attach supplied metadata and stable IDs]
    K --> L[Validate and save internal JSON run]
    L --> M{Any failed records?}
    M -->|Yes| N[Record failure and keep previous CSV]
    M -->|No| O[Write and round-trip-check staging CSV]
    O --> P[Atomically replace final CSV]
```

For `raw/signal/318/1.pdf`:

- `.pipeline/jobs/signal/318/1/` holds the prepared PDF, separate OCR responses,
  and combined `ocr.md`. Rendering uses temporary workspaces.
- `.pipeline/dataset/` holds internal JSON runs with provenance and review issues.
- `.pipeline/claims/` holds shared locks, extraction/export manifests, and failures.
- `output/signal/318/1.csv` holds the 36-column dataset. Review status and raw
  teacher records remain in internal JSON, outside the CSV schema.

The source stays unchanged and the top half of page 1 is permanently redacted in prepared content.
`needs_review` rows are exported; `failed` records block publication. The student
OCR pass currently still receives graded images, so the intended input-isolation
requirement is not yet satisfied. See [verification](pipeline-verification.md)
for what has been checked and what remains unverified.
