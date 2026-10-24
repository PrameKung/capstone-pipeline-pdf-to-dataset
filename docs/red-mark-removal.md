# Conservative red-mark removal

Preprocessing version 11 improves the previous red-channel-only student view and produces separate student/teacher PDF bundles. It runs locally using the existing NumPy/OpenCV dependencies. Original PDFs and grading renders remain unchanged; only pages 2 onward are processed.

## Method

1. Detect candidate red pixels in HSV hue ranges 0–20 and 160–179, with saturation at least 12, value at least 20, and red exceeding both other channels by at least 6. A strong seed requires saturation at least 35 and a channel margin of at least 20. These are OpenCV's 8-bit hue units.
2. Keep candidates as evidence without morphological closing. Only 8-connected candidate components containing a strong seed are eligible for removal. Faint edges can connect through color-supported pixels; spatial proximity alone is insufficient.
3. Estimate local paper brightness in a 31×31 window. Candidate samples must be non-red, nearly neutral (channel spread at most 20), and have red-channel brightness at least 160. Average samples within 12 levels of the local bright reference; require at least eight samples. This follows smooth shadows without changing the page globally.
4. Protect non-red writing and very dark candidate pixels (red channel at most 90). Close short gaps in this **protection mask only** with an 11×11 kernel, then dilate protection with a 7×7 kernel. This conservatively protects some crossings where writing remains visible on both sides; it does not reconstruct hidden strokes.
5. Start with red-channel grayscale. Replace eligible, unprotected pixels with the local paper estimate, which can be lighter or darker than the red channel. Where evidence is insufficient, preserve the baseline. Save the exact changed-pixel mask and uncertainty evidence.
6. For the teacher view, retain original color pixels wherever the candidate red mask is set and use white elsewhere. Use the detection mask, not the removal mask, so uncertain red remains visible. Bundle the student and teacher images into separate PDFs without copying original text, metadata, or hidden layers. Page dimensions match the source, with original page numbers in labels/bookmarks starting at 2.

Thresholds and kernels are centralized in `StudentViewConfig` in `backend/app/pipeline/pdf/student_view.py`. Defaults target the worker's 250 DPI render. Kernel sizes are pixels and do not automatically scale with DPI. Python callers may pass a config for experiments; the worker uses defaults. Record effective settings in `preprocessing.json` and bump `PREPROCESS_VERSION` when changing worker defaults. The current cache does not fingerprint content or settings.

## Artifacts and counts

All artifacts live under the existing `.pipeline/jobs/<source-relative-stem>/` directory; `output/` remains reserved for future CSV files.

| Artifact | Meaning |
| --- | --- |
| `grading-view/` | Untouched color render; teacher extraction evidence |
| `red-masks/` | All detected color candidates, including uncertain ones |
| `removal-masks/` | Pixels actually changed from the red-channel baseline |
| `student-view/` | BGR grayscale image after conservative replacement |
| `teacher-view/` | All detected red candidates in original colors on white, including uncertain ones |
| `student.pdf`, `teacher.pdf` | Viewable image-only bundles of eligible pages, labeled with source page numbers |
| `overlap-candidates/` | Retained red candidates plus nearby protected writing; includes uncertainty beyond overlaps |
| `preprocessing.json` | Version, method, effective configuration, pages, and counts |

Per-page statistics report detected red pixels, seed-supported pixels, removed pixels, retained red pixels, and uncertain pixels. Detected equals removed plus retained. Uncertain counts may exceed retained counts because they include nearby writing. These counts measure processing, not correctness, and do not create extraction statuses or a review queue.

## Verification

From the repository root, run inside Docker:

```sh
docker compose --profile test run --build --rm tests
docker compose --profile test run --rm tests python -m tests.preview_student_view --output-dir /data/.pipeline/previews/red-removal
```

The 17 tests use deterministic synthetic strokes and PDFs. They cover both red hue ranges, bright/dark/faint ink, smooth shadows, black/blue/pencil preservation, crossings, weak disconnected candidates, missing paper evidence, unchanged input images/PDFs, exclusion of page 1, original page numbering, invalid PDFs, version-based reruns, failure cleanup, recursive discovery, and concurrent thread/process workers. Teacher-view tests require all non-candidate pixels to be white and all candidate pixels to match the original. PDF tests cover page order, sizes/rotation, labels, absence of copied text/metadata, and pixel equality after rendering the bundles.

For isolated synthetic marks, tests require mean absolute error against the known clean red-channel image to fall below half of the old baseline error in each tested case. Pixels outside the added marks must remain identical to the baseline. Crossing tests require the tested intersections to be flagged and preserved, not recovered. This criterion describes fixtures only and is not real-exam accuracy.

The preview writes a contact sheet, per-case images, and numerical comparisons under `.pipeline/previews/red-removal/` (or the configured host state mount). All 17 tests passed in the Docker image. A separate version-10 smoke check ran two worker containers against shared local state: eight synthetic PDFs produced 16 eligible page artifacts, the two workers completed six and two PDFs respectively without duplicate completion logs, source checksums were unchanged, and no first-page render or stale claim remained. The backend health endpoint returned `ok`.

Local real PDFs were subsequently processed in Docker and rendered for contact-sheet inspection. Verification checks source hashes, page counts/labels, dimensions, absence of hidden text/metadata, and teacher pixels against the candidate masks. Paper-edge color noise and potentially retained marks remain; this is not a ground-truth accuracy measurement. Private artifacts/reports stay under ignored `.pipeline/`. The later OCR integration adds content-aware preprocessing, durable run history, and isolated transcription of student/teacher views. See the README and pipeline status for the current OCR workflow; image tests do not establish transcription accuracy.

## Limitations

- Color cannot distinguish teacher ink from student red ink, printed red content, or colored diagrams. Such content can be removed if it meets the same criteria.
- The red-only teacher view excludes black/blue teacher marks and may miss weak or occluded red. It is an inspection aid, not a replacement for untouched grading evidence or a verified record. At uncertain overlaps, content can occur in both derived views.
- Real-scan inspection also found false-positive dark handwriting in the teacher view where scanned ink has a warm color cast. The broad candidate mask is not yet a clean separation of teacher-only marks; paper-edge noise is also retained. Color classification needs further tuning against real samples.
- Weak red components without strong seeds, very dark ink, unreliable backgrounds, and suspected crossings intentionally retain the baseline. Faint or desaturated marks outside detection thresholds may remain without a flag.
- A fully opaque mark or a wide crossing can hide all evidence of underlying writing. Protection is heuristic; the pipeline cannot guarantee detection or recover the hidden answer.
- Paper estimation assumes locally bright, mostly neutral paper. Strong color casts, abrupt shadows, low resolution, compression, and unusual ink need real-sample evaluation. Reverse-page bleed-through comparison is not implemented.
- PDFs declaring unusually large physical pages generate very large rasters at 250 DPI and take more time/memory. The current worker preserves source dimensions and does not cap pixel count or automatically lower DPI.
- Student views may still contain teacher marks. OCR prompts omit identifiable grading and flag ambiguity; original graded images are never attached to the student pass. Prompts and schemas cannot certify grading-free transcriptions.
- The later worker integration checks source SHA-256 and required artifacts, records run history, and uses crash-released locks. OCR checkpoints and last-successful publication live outside the preprocessing folder. Preprocessing still replaces its job directory after staging; it does not archive every historical full-resolution render.
