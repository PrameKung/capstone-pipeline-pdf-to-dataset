"""Deterministic ink swatches and strokes, not real exam content."""

from pathlib import Path

import cv2
import numpy as np
import pymupdf


def separate_mark(color: tuple[int, int, int], *, shadow: bool = False) -> tuple[np.ndarray, np.ndarray, np.ndarray]:
    paper = np.tile(np.linspace(190, 235, 240, dtype=np.uint8), (160, 1)) if shadow else np.full((160, 240), 250, np.uint8)
    clean = cv2.cvtColor(paper, cv2.COLOR_GRAY2BGR)
    for x, ink in ((25, (25, 25, 25)), (95, (160, 55, 30)), (165, (175, 175, 175))):
        cv2.line(clean, (x, 20), (x + 25, 55), ink, 2, cv2.LINE_AA)
        cv2.line(clean, (x + 25, 20), (x, 55), ink, 2, cv2.LINE_AA)
    marked = clean.copy()
    cv2.line(marked, (35, 105), (90, 130), color, 5, cv2.LINE_AA)
    cv2.line(marked, (90, 130), (200, 85), color, 5, cv2.LINE_AA)
    mark_pixels = np.any(marked != clean, axis=2)
    return clean, marked, mark_pixels


def crossing_mark() -> tuple[np.ndarray, np.ndarray]:
    clean = np.full((140, 220, 3), 245, np.uint8)
    for x, color in ((50, (25, 25, 25)), (110, (170, 55, 30)), (170, (175, 175, 175))):
        cv2.line(clean, (x, 20), (x, 120), color, 3, cv2.LINE_AA)
    overlay = np.full_like(clean, 255)
    cv2.line(overlay, (20, 70), (200, 70), (35, 40, 180), 5, cv2.LINE_AA)
    marked = np.rint(clean.astype(np.float32) * overlay / 255).astype(np.uint8)
    return clean, marked


def write_pdf(path: Path, *, pages: int = 2) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with pymupdf.open() as document:
        page = document.new_page(width=240, height=160)
        page.insert_text((15, 25), "SYNTHETIC IDENTITY PAGE - EXCLUDE")
        for _ in range(pages - 1):
            _, marked, _ = separate_mark((20, 30, 150))
            page = document.new_page(width=240, height=160)
            encoded = cv2.imencode(".png", marked)[1].tobytes()
            page.insert_image(page.rect, stream=encoded)
        document.save(path)
