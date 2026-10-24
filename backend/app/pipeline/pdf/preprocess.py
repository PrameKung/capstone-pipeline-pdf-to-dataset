"""Remove the identity page, then render the remaining PDF pages to images."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pymupdf


def cut_first_page_and_render(
    source_pdf: Path,
    artifact_dir: Path,
    dpi: int = 250,
) -> list[Path]:
    """Create a page-1-free working PDF and render it, preserving source page numbers.

    The input stays unchanged. The returned paths point to PNG renders under
    ``artifact_dir/pages``; the credential page is never rendered.
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

        pages_dir = staging_dir / "pages"
        pages_dir.mkdir()
        rendered_paths = []
        scale = dpi / 72
        with pymupdf.open(sanitized_pdf) as sanitized:
            for page_index in range(sanitized.page_count):
                original_page_number = page_index + 2
                image_path = pages_dir / f"page-{original_page_number:03}.png"
                pixmap = sanitized[page_index].get_pixmap(matrix=pymupdf.Matrix(scale, scale), alpha=False)
                pixmap.save(image_path)
                rendered_paths.append(image_path)

        if artifact_dir.exists():
            shutil.rmtree(artifact_dir)
        os.replace(staging_dir, artifact_dir)
        return [artifact_dir / path.relative_to(staging_dir) for path in rendered_paths]
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
