"""Remove the identity page, render the remaining pages, and mask red marks."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

import cv2
import numpy as np
import pymupdf


def make_student_view(
    grading_image: Path,
    red_mask_path: Path,
    student_image_path: Path,
    overlap_candidates_path: Path,
) -> int:
    """Save a red-ink mask and a student view with masked pixels whitened.

    The input grading image is never changed. Possible dark-writing overlaps are
    saved separately for inspection; obscured handwriting is not reconstructed.
    """
    image = cv2.imread(str(grading_image), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read rendered page: {grading_image.name}")

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    low_red = cv2.inRange(hsv, np.array((0, 35, 35)), np.array((10, 255, 255)))
    high_red = cv2.inRange(hsv, np.array((170, 35, 35)), np.array((180, 255, 255)))
    red_mask = cv2.bitwise_or(low_red, high_red)
    red_mask = cv2.dilate(red_mask, np.ones((3, 3), dtype=np.uint8), iterations=1)

    # Flag dark pixels close to detected red marks as possible overlaps.
    nearby_red = cv2.dilate(red_mask, np.ones((7, 7), dtype=np.uint8), iterations=1)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dark_pixels = cv2.threshold(gray, 110, 255, cv2.THRESH_BINARY_INV)[1]
    overlap_candidates = cv2.bitwise_and(nearby_red, dark_pixels)
    overlap_candidates = cv2.bitwise_and(overlap_candidates, cv2.bitwise_not(red_mask))

    student_view = image.copy()
    student_view[red_mask > 0] = (255, 255, 255)

    for path, data in (
        (red_mask_path, red_mask),
        (student_image_path, student_view),
        (overlap_candidates_path, overlap_candidates),
    ):
        path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(path), data):
            raise OSError(f"Could not write preprocessing image: {path.name}")

    return int(cv2.countNonZero(red_mask))


def cut_first_page_and_render(
    source_pdf: Path,
    artifact_dir: Path,
    dpi: int = 250,
) -> list[Path]:
    """Create a page-1-free working PDF and render it, preserving source page numbers.

    The input stays unchanged. The job folder contains the page-1-free working
    PDF, untouched grading renders, red masks, student views, and overlap
    candidate masks. The credential page is never rendered.
    """
    if dpi < 1:
        raise ValueError("dpi must be at least 1")

    artifact_dir = artifact_dir.resolve()
    artifact_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = artifact_dir.with_name(f".{artifact_dir.name}.tmp-{uuid.uuid4().hex}")
    staging_dir.mkdir()

    try:
        with pymupdf.open(source_pdf) as source:
            if source.is_encrypted:
                raise ValueError(f"PDF is encrypted: {source_pdf.name}")
            if source.page_count < 2:
                raise ValueError(f"PDF has no pages after the credential page: {source_pdf.name}")

            sanitized_pdf = staging_dir / "pages-without-first.pdf"
            with pymupdf.open() as sanitized:
                sanitized.insert_pdf(source, from_page=1, to_page=source.page_count - 1)
                sanitized.save(sanitized_pdf)

        grading_dir = staging_dir / "grading-view"
        grading_dir.mkdir()
        rendered_paths = []
        scale = dpi / 72
        with pymupdf.open(sanitized_pdf) as sanitized:
            for page_index in range(sanitized.page_count):
                original_page_number = page_index + 2
                image_path = grading_dir / f"page-{original_page_number:03}.png"
                pixmap = sanitized[page_index].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                pixmap.save(image_path)
                rendered_paths.append(image_path)

        red_pixel_counts = {}
        for image_path in rendered_paths:
            page_name = image_path.name
            red_pixel_counts[page_name] = make_student_view(
                image_path,
                staging_dir / "red-masks" / page_name,
                staging_dir / "student-view" / page_name,
                staging_dir / "overlap-candidates" / page_name,
            )

        summary = {
            "source_name": source_pdf.name,
            "render_dpi": dpi,
            "processed_original_pages": [
                int(path.stem.removeprefix("page-")) for path in rendered_paths
            ],
            "red_pixel_counts": red_pixel_counts,
            "red_mask": {
                "hue_ranges": [[0, 10], [170, 180]],
                "minimum_saturation": 35,
                "minimum_value": 35,
                "dilation_kernel": 3,
            },
            "student_view": "Detected red pixels are replaced with white. Possible overlaps are saved as masks; hidden writing is not reconstructed.",
        }
        (staging_dir / "preprocessing.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

        if artifact_dir.exists():
            shutil.rmtree(artifact_dir)
        os.replace(staging_dir, artifact_dir)
        return [artifact_dir / path.relative_to(staging_dir) for path in rendered_paths]
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
