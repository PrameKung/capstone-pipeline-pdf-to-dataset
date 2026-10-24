import tempfile
import unittest
from dataclasses import replace
from pathlib import Path

import cv2
import numpy as np

from app.pipeline.pdf.preprocess import make_student_view
from app.pipeline.pdf.student_view import DEFAULT_STUDENT_VIEW_CONFIG, compute_student_view
from tests.synthetic_pages import crossing_mark, separate_mark


class StudentViewTests(unittest.TestCase):
    def test_isolated_marks_improve_without_changing_answer_pixels(self):
        cases = {
            "bright": (20, 30, 220), "dark": (15, 20, 120),
            "faint": (200, 200, 240), "upper_hue": (60, 20, 220),
        }
        for name, color in cases.items():
            for shadow in (False, True):
                # Faint ink must still be darker than shaded paper.
                ink = (150, 150, 180) if shadow and name == "faint" else color
                with self.subTest(name=name, shadow=shadow):
                    clean, marked, marks = separate_mark(ink, shadow=shadow)
                    before = marked.copy()
                    result = compute_student_view(marked)
                    baseline_error = np.abs(marked[:, :, 2].astype(float) - clean[:, :, 2])[marks].mean()
                    cleaned_error = np.abs(result.student_view[:, :, 2].astype(float) - clean[:, :, 2])[marks].mean()
                    self.assertLess(cleaned_error, baseline_error * 0.5)
                    np.testing.assert_array_equal(marked, before)
                    np.testing.assert_array_equal(result.student_view[:, :, 2][~marks], marked[:, :, 2][~marks])
                    self.assertGreater(result.counts["removed_red_pixels"], 0)

    def test_no_red_is_exact_red_channel_baseline(self):
        clean, _, _ = separate_mark((20, 30, 150), shadow=True)
        result = compute_student_view(clean)
        np.testing.assert_array_equal(result.student_view, cv2.cvtColor(clean[:, :, 2], cv2.COLOR_GRAY2BGR))
        self.assertFalse(result.red_mask.any())
        self.assertFalse(result.overlap_candidates.any())
        self.assertTrue(np.all(result.teacher_view == 255))

    def test_teacher_view_preserves_detected_red_only_including_uncertain_ink(self):
        _, image = crossing_mark()
        image[5:10, 20:60] = (230, 230, 245)  # weak isolated red, not removed
        result = compute_student_view(image)
        red = result.red_mask != 0
        np.testing.assert_array_equal(result.teacher_view[red], image[red])
        self.assertTrue(np.all(result.teacher_view[~red] == 255))
        self.assertFalse(result.removal_mask[5:10, 20:60].any())
        np.testing.assert_array_equal(result.teacher_view[5:10, 20:60], image[5:10, 20:60])

    def test_faint_edge_requires_color_connected_seed(self):
        image = np.full((90, 140, 3), 250, np.uint8)
        image[30:35, 20:65] = (230, 230, 245)  # weak, connected to seed
        image[35:40, 20:65] = (20, 20, 180)
        image[65:70, 20:65] = (230, 230, 245)  # same color, disconnected
        result = compute_student_view(image)
        self.assertTrue(np.all(result.removal_mask[30:35, 20:65]))
        self.assertFalse(result.removal_mask[65:70, 20:65].any())
        self.assertTrue(np.all(result.overlap_candidates[65:70, 20:65]))

    def test_crossings_are_flagged_and_not_reconstructed(self):
        _, image = crossing_mark()
        result = compute_student_view(image)
        for x in (50, 110, 170):
            self.assertTrue(result.overlap_candidates[67:74, x - 1:x + 2].any())
            self.assertFalse(result.removal_mask[67:74, x - 1:x + 2].any())
            np.testing.assert_array_equal(result.student_view[67:74, x - 1:x + 2, 2], image[67:74, x - 1:x + 2, 2])
        self.assertGreater(result.counts["removed_red_pixels"], 0)

    def test_adjacent_pencil_gap_is_not_closed_for_removal(self):
        image = np.full((90, 140, 3), 245, np.uint8)
        image[30:60, 45:48] = (20, 20, 160)
        image[30:60, 51:54] = (20, 20, 160)
        image[30:60, 49:50] = (180, 180, 180)
        result = compute_student_view(image)
        self.assertFalse(result.removal_mask[30:60, 48:51].any())
        np.testing.assert_array_equal(result.student_view[30:60, 48:51, 2], image[30:60, 48:51, 2])

    def test_dark_mixed_ink_and_missing_paper_are_preserved(self):
        for color, paper in (((10, 15, 65), 245), ((20, 20, 180), 130)):
            with self.subTest(color=color, paper=paper):
                image = np.full((80, 120, 3), paper, np.uint8)
                image[30:45, 30:90] = color
                result = compute_student_view(image)
                self.assertFalse(result.removal_mask.any())
                self.assertGreater(result.counts["retained_red_pixels"], 0)
                np.testing.assert_array_equal(result.student_view[:, :, 2], image[:, :, 2])

    def test_changed_pixels_match_removal_mask_and_uncertainty_is_disjoint(self):
        _, image = crossing_mark()
        result = compute_student_view(image)
        changed = result.student_view[:, :, 2] != image[:, :, 2]
        np.testing.assert_array_equal(changed, result.removal_mask != 0)
        self.assertFalse(np.any((result.removal_mask != 0) & (result.overlap_candidates != 0)))
        self.assertFalse(np.any((result.removal_mask != 0) & (result.red_mask == 0)))
        self.assertEqual(result.counts["detected_red_pixels"], result.counts["removed_red_pixels"] + result.counts["retained_red_pixels"])

    def test_legacy_file_interface_and_original_are_preserved(self):
        _, image, _ = separate_mark((20, 30, 150))
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            original = root / "original.png"
            cv2.imwrite(str(original), image)
            original_bytes = original.read_bytes()
            count = make_student_view(original, root / "red.png", root / "student.png", root / "overlap.png")
            self.assertEqual(count, compute_student_view(image).counts["detected_red_pixels"])
            self.assertEqual(original.read_bytes(), original_bytes)

    def test_invalid_input_and_configuration_fail_clearly(self):
        for image in (np.zeros((0, 0, 3), np.uint8), np.zeros((5, 5), np.uint8), np.zeros((5, 5, 3), float)):
            with self.assertRaisesRegex(ValueError, "uint8 BGR"):
                compute_student_view(image)
        with self.assertRaises(ValueError):
            replace(DEFAULT_STUDENT_VIEW_CONFIG, protection_kernel=2)


if __name__ == "__main__":
    unittest.main()
