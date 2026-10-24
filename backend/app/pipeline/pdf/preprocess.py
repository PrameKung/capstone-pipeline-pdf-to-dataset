"""Remove the identity page, render remaining pages, and prepare student views."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

import cv2
import numpy as np
import pymupdf

PREPROCESS_VERSION = 9
RED_HUE_RANGES = ((0, 20), (160, 180))
RED_MIN_SATURATION = 15
RED_MIN_VALUE = 20
RED_MIN_CHANNEL_MARGIN = 8


def make_student_view(
    grading_image: Path,
    red_mask_path: Path,
    student_image_path: Path,
    overlap_candidates_path: Path,
) -> int:
    """Save red-ink evidence and a grayscale student view from the red channel.

    Red ink is usually much lighter in the red channel than in the other
    channels. Using it for the student view suppresses red without deleting
    nearby dark writing. The input grading image is never changed; possible
    dark-writing overlaps are saved separately for inspection.
    """
    image = cv2.imread(str(grading_image), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read rendered page: {grading_image.name}")

    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    low_red = cv2.inRange(
        hsv,
        np.array((RED_HUE_RANGES[0][0], RED_MIN_SATURATION, RED_MIN_VALUE)),
        np.array((RED_HUE_RANGES[0][1], 255, 255)),
    )
    high_red = cv2.inRange(
        hsv,
        np.array((RED_HUE_RANGES[1][0], RED_MIN_SATURATION, RED_MIN_VALUE)),
        np.array((RED_HUE_RANGES[1][1], 255, 255)),
    )
    red_mask = cv2.bitwise_or(low_red, high_red)

    # Require red to be measurably stronger than both other color channels.
    blue, green, red = cv2.split(image)
    red_channel = red.astype(np.int16)
    red_dominant = (
        (red_channel - green.astype(np.int16) >= RED_MIN_CHANNEL_MARGIN)
        & (red_channel - blue.astype(np.int16) >= RED_MIN_CHANNEL_MARGIN)
    )
    red_mask = cv2.bitwise_and(red_mask, red_dominant.astype(np.uint8) * 255)
    # Bridge tiny breaks in detected strokes without expanding into nearby paper.
    red_mask = cv2.morphologyEx(
        red_mask,
        cv2.MORPH_CLOSE,
        np.ones((7, 7), dtype=np.uint8),
        iterations=1,
    )

    # Flag dark pixels close to detected red marks as possible overlaps.
    nearby_red = cv2.dilate(red_mask, np.ones((7, 7), dtype=np.uint8), iterations=1)
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    dark_pixels = cv2.threshold(gray, 110, 255, cv2.THRESH_BINARY_INV)[1]
    overlap_candidates = cv2.bitwise_and(nearby_red, dark_pixels)
    overlap_candidates = cv2.bitwise_and(overlap_candidates, cv2.bitwise_not(red_mask))

    # A red-channel grayscale view fades red ink without expanding a binary
    # mask across adjacent student strokes. Keep the untouched color render for
    # teacher-mark extraction and provenance.
    student_view = cv2.cvtColor(red, cv2.COLOR_GRAY2BGR)

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
            "preprocess_version": PREPROCESS_VERSION,
            "source_name": source_pdf.name,
            "render_dpi": dpi,
            "processed_original_pages": [
                int(path.stem.removeprefix("page-")) for path in rendered_paths
            ],
            "red_pixel_counts": red_pixel_counts,
            "red_mask": {
                "hue_ranges": [list(bounds) for bounds in RED_HUE_RANGES],
                "minimum_saturation": RED_MIN_SATURATION,
                "minimum_value": RED_MIN_VALUE,
                "minimum_red_channel_margin": RED_MIN_CHANNEL_MARGIN,
                "mask_operation": "7x7 morphological close to bridge gaps; used for evidence and overlap flags",
                "overlap_candidate_dilation_kernel": 7,
            },
            "student_view": "Grayscale from the red channel to reduce red-mark contrast without whitening adjacent student writing. Untouched color renders are retained for grading evidence.",
        }
        (staging_dir / "preprocessing.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

        if artifact_dir.exists():
            shutil.rmtree(artifact_dir)
        os.replace(staging_dir, artifact_dir)
        return [artifact_dir / path.relative_to(staging_dir) for path in rendered_paths]
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
