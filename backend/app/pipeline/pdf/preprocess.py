"""Remove the identity page, render remaining pages, and prepare student views."""

from __future__ import annotations

import json
import os
import shutil
import uuid
from dataclasses import asdict
from pathlib import Path

import cv2
import pymupdf

from app.pipeline.jobs.state import checksum

from app.pipeline.pdf.student_view import (
    DEFAULT_STUDENT_VIEW_CONFIG,
    StudentViewConfig,
    compute_student_view,
)

PREPROCESS_VERSION = 11


def make_student_view(
    grading_image: Path,
    red_mask_path: Path,
    student_image_path: Path,
    overlap_candidates_path: Path,
    *,
    removal_mask_path: Path | None = None,
    teacher_image_path: Path | None = None,
    config: StudentViewConfig = DEFAULT_STUDENT_VIEW_CONFIG,
) -> int:
    """Save conservative red removal artifacts; return the detected pixel count."""
    counts = _save_student_view(
        grading_image, red_mask_path, student_image_path, overlap_candidates_path,
        removal_mask_path=removal_mask_path, teacher_image_path=teacher_image_path, config=config,
    )
    return counts["detected_red_pixels"]


def _save_student_view(
    grading_image: Path,
    red_mask_path: Path,
    student_image_path: Path,
    overlap_candidates_path: Path,
    *,
    removal_mask_path: Path | None,
    teacher_image_path: Path | None,
    config: StudentViewConfig,
) -> dict[str, int]:
    image = cv2.imread(str(grading_image), cv2.IMREAD_COLOR)
    if image is None:
        raise ValueError(f"Could not read rendered page: {grading_image.name}")
    result = compute_student_view(image, config)
    artifacts = [
        (red_mask_path, result.red_mask),
        (student_image_path, result.student_view),
        (overlap_candidates_path, result.overlap_candidates),
    ]
    if removal_mask_path is not None:
        artifacts.append((removal_mask_path, result.removal_mask))
    if teacher_image_path is not None:
        artifacts.append((teacher_image_path, result.teacher_view))
    for path, data in artifacts:
        path.parent.mkdir(parents=True, exist_ok=True)
        if not cv2.imwrite(str(path), data):
            raise OSError(f"Could not write preprocessing image: {path.name}")
    return result.counts


def _write_view_pdf(
    image_paths: list[Path],
    page_sizes: list[tuple[float, float]],
    destination: Path,
) -> None:
    """Embed only derived images; never carry source text, metadata, or layers."""
    with pymupdf.open() as document:
        for image_path, (width, height) in zip(image_paths, page_sizes, strict=True):
            page = document.new_page(width=width, height=height)
            page.insert_image(page.rect, filename=str(image_path))
        document.set_page_labels([{"startpage": 0, "style": "D", "firstpagenum": 2}])
        document.set_toc([[1, f"Source page {index + 2}", index + 1] for index in range(len(image_paths))])
        document.save(destination, garbage=4, deflate=True)


def cut_first_page_and_render(
    source_pdf: Path,
    artifact_dir: Path,
    dpi: int = 250,
    *,
    student_view_config: StudentViewConfig = DEFAULT_STUDENT_VIEW_CONFIG,
) -> list[Path]:
    """Create a page-1-free working PDF and render it, preserving source page numbers.

    The input stays unchanged. The job folder contains the page-1-free working
    PDF, untouched grading renders, masks, student/teacher views and their PDF
    bundles. The credential page is never rendered or included in either bundle.
    """
    if dpi < 1:
        raise ValueError("dpi must be at least 1")
    source_sha256 = checksum(source_pdf)

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
        page_sizes = []
        scale = dpi / 72
        with pymupdf.open(sanitized_pdf) as sanitized:
            for page_index in range(sanitized.page_count):
                original_page_number = page_index + 2
                page_sizes.append((sanitized[page_index].rect.width, sanitized[page_index].rect.height))
                image_path = grading_dir / f"page-{original_page_number:03}.png"
                pixmap = sanitized[page_index].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                pixmap.save(image_path)
                rendered_paths.append(image_path)

        page_statistics = {}
        for image_path in rendered_paths:
            page_name = image_path.name
            page_statistics[page_name] = _save_student_view(
                image_path,
                staging_dir / "red-masks" / page_name,
                staging_dir / "student-view" / page_name,
                staging_dir / "overlap-candidates" / page_name,
                removal_mask_path=staging_dir / "removal-masks" / page_name,
                teacher_image_path=staging_dir / "teacher-view" / page_name,
                config=student_view_config,
            )

        for view in ("student", "teacher"):
            _write_view_pdf(
                [staging_dir / f"{view}-view" / path.name for path in rendered_paths],
                page_sizes,
                staging_dir / f"{view}.pdf",
            )

        summary = {
            "preprocess_version": PREPROCESS_VERSION,
            "source_sha256": source_sha256,
            "source_name": source_pdf.name,
            "render_dpi": dpi,
            "processed_original_pages": [
                int(path.stem.removeprefix("page-")) for path in rendered_paths
            ],
            "red_pixel_counts": {
                name: counts["detected_red_pixels"] for name, counts in page_statistics.items()
            },
            "page_statistics": page_statistics,
            "student_view_method": "color_seeded_local_paper_v1",
            "student_view_config": asdict(student_view_config),
            "view_pdfs": {
                "student": "student.pdf",
                "teacher": "teacher.pdf",
                "page_labels": "Original source page numbers, starting at 2",
            },
            "teacher_view": "Original colors of detected red candidates on white; includes uncertain red. No original page image, text layer, or hidden handwriting is embedded. Color does not identify the author; untouched grading renders remain authoritative evidence.",
            "red_mask": {
                "mask_operation": "Color candidates only; no geometric closing or expansion for removal",
                "removal_masks": "Only pixels actually replaced using reliable local paper samples",
                "overlap_candidates": "Retained red candidates and nearby writing; includes uncertain color and background",
            },
            "student_view": "Red-channel grayscale with confident red pixels replaced by local paper brightness. Suspected overlaps and uncertain pixels retain the baseline; hidden writing is never reconstructed. Untouched color renders remain grading evidence.",
        }
        if checksum(source_pdf) != source_sha256:
            raise ValueError("Source PDF changed during preprocessing; retry after the copy finishes")
        (staging_dir / "preprocessing.json").write_text(json.dumps(summary, indent=2) + "\n", encoding="utf-8")

        if artifact_dir.exists():
            shutil.rmtree(artifact_dir)
        os.replace(staging_dir, artifact_dir)
        return [artifact_dir / path.relative_to(staging_dir) for path in rendered_paths]
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
