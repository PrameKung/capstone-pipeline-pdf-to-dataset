"""Create a working PDF without its credential page."""

from __future__ import annotations

import os
import shutil
import uuid
from pathlib import Path

import pymupdf

PREPROCESS_VERSION = 11


def prepare_pdf_without_first_page(source_pdf: Path, artifact_dir: Path) -> Path:
    """Save pages 2 onward as the only artifact in a per-PDF job folder."""
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

            prepared_pdf = staging_dir / "pages-without-first.pdf"
            with pymupdf.open() as prepared:
                prepared.insert_pdf(source, from_page=1, to_page=source.page_count - 1)
                prepared.save(prepared_pdf)

        if artifact_dir.exists():
            shutil.rmtree(artifact_dir)
        os.replace(staging_dir, artifact_dir)
        return artifact_dir / prepared_pdf.name
    except Exception:
        shutil.rmtree(staging_dir, ignore_errors=True)
        raise
