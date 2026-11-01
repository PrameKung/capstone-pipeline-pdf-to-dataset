# Merge review: OCR evidence prompts

- Branch: `feature/ocr-evidence-handoff-20261101`
- Base: `origin/master` at `2c68420`
- Scope: changes since the clone, plus a shareable prompt and offline regression test
- Diff size: 7 files changed, 282 insertions(+), 3 deletions(-)

| Area | master | This branch |
|---|---|---|
| Student OCR | Existing transcription/scope/confidence prompt | Adds all-page survey, localized uncertainty, graph/cancellation handling and final completeness self-check |
| Grading | Separate teacher pass | Unchanged |
| OCR cache | Version 12 | Version 13; next normal extraction refreshes older cached OCR |
| CSV | 38 columns | Same columns and parser contract |
| Tests | Existing confidence/template/preprocessing tests | Adds isolated real request-assembly test with mocked rendering/CLI boundary |
| Handoff | General README | Copyable PDF-to-CSV prompt and pointer to full review/training prompt in reviewer repo |

## Verified

- Existing confidence/template tests: 11 pass.
- New offline student request test: pass. It confirms all three blocks reach the
  student request only and checks parser preservation of incorrect mathematics,
  strikethrough, uncertainty, page/subpart and separate confidence metadata.
- `git diff --check`: pass.
- Native Windows cannot import `test_preprocess` because the unchanged worker
  imports Unix `fcntl`. The base commit has the same import. No PDF/locking or
  live-model accuracy claim is made by the offline test.

## Before merge/deployment

1. Review the three prompt blocks and the single cache-version bump.
2. Run the full backend suite on the supported Linux runtime before deployment.
3. Rebuild/recreate deployed workers when adopting v13. A normal rerun can invoke
   OCR again for old caches; export-only does not apply the new prompts.
4. Keep `.env`, PDFs, generated CSVs, checkpoints and credentials local.

The branch still sends full-color prepared images to isolated student/grading
passes. Prompt separation is not physical removal of teacher annotations.
Independent AI audit calls, inferred training derivatives, crop/retry controllers
and new image preparation are not added to `extract.py` by this change.

This is the smaller of the two merge candidates. It can be reviewed first;
the CSV contract remains compatible with the reviewer branch.
Textual merge compatibility is assessed against the stated base; recheck after
master advances with `git merge-tree --write-tree origin/master HEAD`.
