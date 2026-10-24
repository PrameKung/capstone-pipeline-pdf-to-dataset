"""Remove the identity page, render remaining pages, and prepare student views."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from pathlib import Path

import cv2
import pymupdf

PREPROCESS_VERSION = 10


def make_student_view(
    grading_image: Path,
    student_image_path: Path,
) -> None:
    """Save a red-channel grayscale student view; preserve the color input."""
    image = cv2.imread(str(grading_image), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read rendered page: {grading_image.name}")

    # The red channel fades red marks without detecting or masking pixels.
    student_view = cv2.cvtColor(image[:, :, 2], cv2.COLOR_GRAY2BGR)
    student_image_path.parent.mkdir(parents=True, exist_ok=True)
    if not cv2.imwrite(str(student_image_path), student_view):
        raise OSError(f"Could not write student view: {student_image_path.name}")


def cut_first_page_and_render(
    source_pdf: Path,
    artifact_dir: Path,
    dpi: int = 250,
) -> list[Path]:
    """Create a page-1-free working PDF and render it, preserving source page numbers.

    The input stays unchanged. The job folder contains the page-1-free working
    PDF, untouched grading renders, and red-channel grayscale student views.
    The credential page is never rendered.
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

        for image_path in rendered_paths:
            make_student_view(
                image_path,
                staging_dir / "student-view" / image_path.name,
            )

        summary = {
            "preprocess_version": PREPROCESS_VERSION,
            "source_name": source_pdf.name,
            "render_dpi": dpi,
            "processed_original_pages": [
                int(path.stem.removeprefix("page-")) for path in rendered_paths
            ],
            "student_view": "Grayscale from the red channel; no red-ink detection or masking. Untouched color renders are retained for grading evidence.",
        }
        (staging_dir / "preprocessing.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

        if artifact_dir.exists():
            shutil.rmtree(artifact_dir)
        os.replace(staging_dir, artifact_dir)
        return [artifact_dir / path.relative_to(staging_dir) for path in rendered_paths]
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
