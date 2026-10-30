"""Create a working PDF with the top half of its first page permanently redacted."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pymupdf

PREPROCESS_VERSION = 12


def prepare_pdf_with_redacted_header(source_pdf: Path, artifact_dir: Path) -> Path:
    """Keep all pages and redact the displayed top half of page 1."""
    artifact_dir = artifact_dir.resolve()
    artifact_dir.parent.mkdir(parents=True, exist_ok=True)
    staging_dir = artifact_dir.with_name(f".{artifact_dir.name}.tmp-{uuid.uuid4().hex}")
    staging_dir.mkdir()

    try:
        with pymupdf.open(source_pdf) as source:
            if source.is_encrypted:
                raise ValueError(f"PDF is encrypted: {source_pdf.name}")
            if source.page_count < 1:
                raise ValueError(f"PDF has no pages: {source_pdf.name}")

            prepared_pdf = staging_dir / "pages-redacted.pdf"
            with pymupdf.open() as prepared:
                prepared.insert_pdf(source)
                first = prepared[0]
                visible = first.rect
                top_half = pymupdf.Rect(visible.x0, visible.y0, visible.x1,
                                        visible.y0 + visible.height / 2)
                # Redaction coordinates are unrotated; censor the displayed top.
                first.add_redact_annot(top_half * first.derotation_matrix, fill=(0, 0, 0))
                first.apply_redactions(images=2, graphics=2, text=0)
                # Discard removed objects rather than leaving recoverable content.
                prepared.save(prepared_pdf, garbage=4, deflate=True)

        if artifact_dir.exists():
            shutil.rmtree(artifact_dir)
        os.replace(staging_dir, artifact_dir)
        return artifact_dir / prepared_pdf.name
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
