"""Conservative, local red-ink removal; never reconstruct obscured writing."""

from __future__ import annotations

from dataclasses import dataclass

import cv2
import numpy as np


@dataclass(frozen=True)
class StudentViewConfig:
    """Pixel thresholds at the rendered resolution (normally 250 DPI)."""

    hue_ranges: tuple[tuple[int, int], ...] = ((0, 20), (160, 179))
    minimum_value: int = 20
    candidate_saturation: int = 12
    candidate_channel_margin: int = 6
    seed_saturation: int = 35
    seed_channel_margin: int = 20
    minimum_removal_red: int = 90
    protection_kernel: int = 7
    protection_gap_kernel: int = 11
    background_kernel: int = 31
    minimum_paper_value: int = 160
    maximum_paper_chroma: int = 20
    paper_tolerance: int = 12
    minimum_paper_samples: int = 8

    def __post_init__(self) -> None:
        for name in ("protection_kernel", "protection_gap_kernel", "background_kernel"):
            value = getattr(self, name)
            if value < 1 or value % 2 != 1:
                raise ValueError(f"{name} must be a positive odd integer")
        if not self.hue_ranges or any(not 0 <= low <= high <= 179 for low, high in self.hue_ranges):
            raise ValueError("hue_ranges must contain OpenCV hue bounds in 0..179")
        for name in (
            "minimum_value", "candidate_saturation", "candidate_channel_margin",
            "seed_saturation", "seed_channel_margin", "minimum_removal_red",
            "minimum_paper_value", "maximum_paper_chroma", "paper_tolerance",
        ):
            if not 0 <= getattr(self, name) <= 255:
                raise ValueError(f"{name} must be in 0..255")
        if self.seed_saturation < self.candidate_saturation or self.seed_channel_margin < self.candidate_channel_margin:
            raise ValueError("seed thresholds must be at least as strict as candidate thresholds")
        if not 1 <= self.minimum_paper_samples <= self.background_kernel ** 2:
            raise ValueError("minimum_paper_samples must fit the background window")


DEFAULT_STUDENT_VIEW_CONFIG = StudentViewConfig()


@dataclass
class StudentViewResult:
    student_view: np.ndarray
    teacher_view: np.ndarray
    red_mask: np.ndarray
    removal_mask: np.ndarray
    overlap_candidates: np.ndarray
    counts: dict[str, int]


def _mask(values: np.ndarray) -> np.ndarray:
    return values.astype(np.uint8) * 255


def compute_student_view(
    image: np.ndarray,
    config: StudentViewConfig = DEFAULT_STUDENT_VIEW_CONFIG,
) -> StudentViewResult:
    """Return evidence and a cleaned BGR grayscale view without changing image.

    Weak red pixels may be removed only in a color-connected component with a
    strong red seed. Neither closing nor unconstrained dilation adds removal
    pixels. Dark mixed pixels and nearby non-red writing veto removal. Reliable
    local paper samples are required; otherwise keep the red-channel baseline
    and flag uncertainty. These heuristics cannot identify the author of ink or
    detect every fully occluded stroke.
    """
    if image.dtype != np.uint8 or image.ndim != 3 or image.shape[2] != 3 or not image.size:
        raise ValueError("image must be a nonempty uint8 BGR image")

    blue, green, red = cv2.split(image)
    hsv = cv2.cvtColor(image, cv2.COLOR_BGR2HSV)
    hue_ok = np.zeros(red.shape, dtype=bool)
    for low, high in config.hue_ranges:
        hue_ok |= (hsv[:, :, 0] >= low) & (hsv[:, :, 0] <= high)
    margin = red.astype(np.int16) - np.maximum(blue, green).astype(np.int16)
    candidates = (
        hue_ok & (hsv[:, :, 2] >= config.minimum_value)
        & (hsv[:, :, 1] >= config.candidate_saturation)
        & (margin >= config.candidate_channel_margin)
    )
    seeds = (
        candidates & (hsv[:, :, 1] >= config.seed_saturation)
        & (margin >= config.seed_channel_margin)
    )
    red_mask = _mask(candidates)
    cleaned = red.copy()
    removal = np.zeros(red.shape, dtype=bool)
    uncertain = candidates.copy()
    supported = np.zeros(red.shape, dtype=bool)

    if np.any(candidates):
        # Color-constrained growth to faint edges, not geometric gap filling.
        component_count, labels = cv2.connectedComponents(red_mask, connectivity=8)
        seeded_labels = np.zeros(component_count, dtype=bool)
        seeded_labels[np.unique(labels[seeds])] = True
        seeded_labels[0] = False
        supported = seeded_labels[labels]
        del labels

        # Estimate paper only from bright, nearly neutral, non-red pixels.
        # The local maximum selects the bright band; the mean avoids whitening
        # everything to 255 and follows smooth illumination changes.
        chroma = image.max(axis=2).astype(np.int16) - image.min(axis=2).astype(np.int16)
        paper_possible = (
            ~candidates & (red >= config.minimum_paper_value)
            & (chroma <= config.maximum_paper_chroma)
        )
        background_size = (config.background_kernel, config.background_kernel)
        local_high = cv2.dilate(
            np.where(paper_possible, red, 0).astype(np.uint8),
            np.ones(background_size, dtype=np.uint8),
        )
        paper_samples = paper_possible & (
            red.astype(np.int16) >= local_high.astype(np.int16) - config.paper_tolerance
        )
        weights = paper_samples.astype(np.float32)
        count = cv2.boxFilter(weights, -1, background_size, normalize=False, borderType=cv2.BORDER_CONSTANT)
        total = cv2.boxFilter(
            red.astype(np.float32) * weights, -1, background_size,
            normalize=False, borderType=cv2.BORDER_CONSTANT,
        )
        paper = np.rint(total / np.maximum(count, 1)).astype(np.uint8)
        reliable_paper = count >= config.minimum_paper_samples

        # Neutral/blue/pencil strokes remain unchanged. Very dark red could be
        # ink mixed with a student stroke, so it is also protected. Dilation
        # expands only the protection area, never the removal area.
        writing = (~candidates & (
            (red < config.minimum_paper_value)
            | (red.astype(np.int16) < local_high.astype(np.int16) - config.paper_tolerance)
            | (chroma > config.maximum_paper_chroma)
        )) | (candidates & (red <= config.minimum_removal_red))
        # Join short gaps in visible writing for protection ONLY. For example,
        # pencil visible above and below a red stroke should veto replacing
        # the crossing even when the mixed pixel still looks strongly red.
        writing = cv2.morphologyEx(
            _mask(writing), cv2.MORPH_CLOSE,
            np.ones((config.protection_gap_kernel, config.protection_gap_kernel), dtype=np.uint8),
        ) != 0
        kernel = np.ones((config.protection_kernel, config.protection_kernel), dtype=np.uint8)
        protected = cv2.dilate(_mask(writing), kernel) != 0
        nearby_red = cv2.dilate(red_mask, kernel) != 0
        removal = supported & ~protected & reliable_paper & (paper != red)
        cleaned[removal] = paper[removal]
        uncertain = (candidates & ~removal) | (writing & nearby_red)

    detected_count = int(np.count_nonzero(candidates))
    removed_count = int(np.count_nonzero(removal))
    # Keep every detected red candidate, including uncertain red that remains
    # in the student baseline. This is a color-only view, not a reconstruction
    # of teacher ink hidden at crossings or proof of the ink's author.
    teacher_view = np.full_like(image, 255)
    teacher_view[candidates] = image[candidates]
    return StudentViewResult(
        student_view=cv2.cvtColor(cleaned, cv2.COLOR_GRAY2BGR),
        teacher_view=teacher_view,
        red_mask=red_mask,
        removal_mask=_mask(removal),
        overlap_candidates=_mask(uncertain),
        counts={
            "detected_red_pixels": detected_count,
            "seed_supported_red_pixels": int(np.count_nonzero(supported)),
            "removed_red_pixels": removed_count,
            "retained_red_pixels": detected_count - removed_count,
            "uncertain_pixels": int(np.count_nonzero(uncertain)),
        },
    )
