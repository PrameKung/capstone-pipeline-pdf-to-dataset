import hashlib
import json
import multiprocessing
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

import cv2
import numpy as np
import pymupdf

from app.pipeline.jobs.worker import _claim_key, find_pdfs, process_pdf, run_worker
from app.pipeline.pdf.preprocess import PREPROCESS_VERSION, cut_first_page_and_render
from tests.synthetic_pages import write_pdf


def _competing_worker(raw, state, pdf, start, results):
    if not start.wait(20):
        raise TimeoutError("Synthetic worker start barrier timed out")
    results.put(process_pdf(Path(raw), Path(state), Path(pdf)))


class PreprocessingPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temporary = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary.cleanup)
        self.root = Path(self.temporary.name)
        self.raw = self.root / "raw"
        self.state = self.root / ".pipeline"
        self.pdf = self.raw / "signal" / "318" / "1.pdf"
        write_pdf(self.pdf)

    def test_pdf_identity_excluded_original_unchanged_and_artifacts_consistent(self):
        original_hash = hashlib.sha256(self.pdf.read_bytes()).hexdigest()
        job = self.state / "jobs" / "signal" / "318" / "1"
        paths = cut_first_page_and_render(self.pdf, job, dpi=72)
        self.assertEqual([path.name for path in paths], ["page-002.png"])
        self.assertEqual(hashlib.sha256(self.pdf.read_bytes()).hexdigest(), original_hash)
        self.assertFalse(list(job.rglob("page-001*")))
        with pymupdf.open(job / "pages-without-first.pdf") as document:
            self.assertEqual(document.page_count, 1)
            self.assertNotIn("IDENTITY", document[0].get_text())
        with pymupdf.open(self.pdf) as source:
            pixmap = source[1].get_pixmap(alpha=False)
            expected = cv2.imdecode(np.frombuffer(pixmap.tobytes("png"), np.uint8), cv2.IMREAD_COLOR)
        grading = cv2.imread(str(paths[0]))
        np.testing.assert_array_equal(grading, expected)
        summary = json.loads((job / "preprocessing.json").read_text())
        self.assertEqual(summary["preprocess_version"], PREPROCESS_VERSION)
        self.assertEqual(summary["processed_original_pages"], [2])
        self.assertIn("protection_gap_kernel", summary["student_view_config"])
        counts = summary["page_statistics"]["page-002.png"]
        for folder, field in (("red-masks", "detected_red_pixels"), ("removal-masks", "removed_red_pixels"), ("overlap-candidates", "uncertain_pixels")):
            mask = cv2.imread(str(job / folder / "page-002.png"), cv2.IMREAD_GRAYSCALE)
            self.assertEqual(cv2.countNonZero(mask), counts[field])
        student = cv2.imread(str(job / "student-view" / "page-002.png"))
        removed = cv2.imread(str(job / "removal-masks" / "page-002.png"), cv2.IMREAD_GRAYSCALE)
        np.testing.assert_array_equal(student[:, :, 2] != grading[:, :, 2], removed != 0)
        self.assertFalse((self.root / "output").exists())

    def test_single_page_and_invalid_pdf_do_not_publish_artifacts(self):
        for kind in ("single", "invalid"):
            with self.subTest(kind=kind):
                source = self.raw / f"{kind}.pdf"
                if kind == "single":
                    write_pdf(source, pages=1)
                else:
                    source.write_bytes(b"synthetic invalid PDF")
                job = self.state / kind
                with self.assertRaises((ValueError, pymupdf.FileDataError)):
                    cut_first_page_and_render(source, job)
                self.assertFalse(job.exists())
                self.assertFalse(list(self.state.glob(f".{kind}.tmp-*")))

    def test_student_teacher_pdfs_embed_only_derived_pages_with_source_labels(self):
        # Use two distinct page sizes, including rotation, to verify ordering
        # and geometry as well as excluding the identity page and hidden text.
        with pymupdf.open(self.pdf) as source:
            extra = source.new_page(width=180, height=120)
            extra.insert_text((15, 25), "SYNTHETIC NONRED STROKE")
            extra.set_rotation(90)
            source.set_metadata({"author": "synthetic-sensitive-metadata"})
            source.saveIncr()
        job = self.state / "bundles"
        cut_first_page_and_render(self.pdf, job, dpi=72)
        with pymupdf.open(self.pdf) as source:
            sizes = [(source[i].rect.width, source[i].rect.height) for i in (1, 2)]
        for view in ("student", "teacher"):
            with self.subTest(view=view), pymupdf.open(job / f"{view}.pdf") as document:
                self.assertEqual(document.page_count, 2)
                self.assertEqual(document.metadata.get("author", ""), "")
                self.assertEqual([page.get_label() for page in document], ["2", "3"])
                self.assertEqual([entry[1] for entry in document.get_toc()], ["Source page 2", "Source page 3"])
                for index, page in enumerate(document):
                    self.assertEqual((page.rect.width, page.rect.height), sizes[index])
                    self.assertEqual(page.get_text(), "")
                    self.assertEqual(len(page.get_images()), 1)
                    pixmap = page.get_pixmap(alpha=False)
                    actual = cv2.imdecode(np.frombuffer(pixmap.tobytes("png"), np.uint8), cv2.IMREAD_COLOR)
                    expected = cv2.imread(str(job / f"{view}-view" / f"page-{index + 2:03}.png"))
                    np.testing.assert_array_equal(actual, expected)
        teacher = cv2.imread(str(job / "teacher-view/page-003.png"))
        self.assertTrue(np.all(teacher == 255))

    def test_old_version_regenerates_then_current_version_skips(self):
        claims = self.state / "claims"
        claims.mkdir(parents=True)
        done = claims / f"{_claim_key(self.raw, self.pdf)}.rendered"
        done.write_text("9\n")
        self.assertTrue(process_pdf(self.raw, self.state, self.pdf))
        summary = self.state / "jobs/signal/318/1/preprocessing.json"
        first_mtime = summary.stat().st_mtime_ns
        self.assertFalse(process_pdf(self.raw, self.state, self.pdf))
        self.assertEqual(summary.stat().st_mtime_ns, first_mtime)
        self.assertEqual(done.read_text().strip(), str(PREPROCESS_VERSION))

    def test_failed_preparation_keeps_old_artifacts_and_releases_claim(self):
        self.assertTrue(process_pdf(self.raw, self.state, self.pdf))
        job = self.state / "jobs/signal/318/1"
        originals = {path.relative_to(job): path.read_bytes() for path in job.rglob("*") if path.is_file()}
        done = self.state / "claims" / f"{_claim_key(self.raw, self.pdf)}.rendered"
        done.write_text("9\n")
        with patch("app.pipeline.pdf.preprocess.compute_student_view", side_effect=OSError("synthetic write failure")):
            with self.assertRaisesRegex(OSError, "synthetic"):
                process_pdf(self.raw, self.state, self.pdf)
        self.assertEqual(originals, {path.relative_to(job): path.read_bytes() for path in job.rglob("*") if path.is_file()})
        self.assertEqual(done.read_text(), "9\n")
        self.assertFalse(list((self.state / "claims").glob("*.lock")))
        self.assertFalse(list(job.parent.glob(".1.tmp-*")))

    def test_two_processes_only_one_claims_the_same_pdf(self):
        context = multiprocessing.get_context("spawn")
        start = context.Event()
        results = context.Queue()
        processes = [context.Process(target=_competing_worker, args=(str(self.raw), str(self.state), str(self.pdf), start, results)) for _ in range(2)]
        try:
            for process in processes:
                process.start()
            start.set()
            outcomes = [results.get(timeout=30) for _ in processes]
            for process in processes:
                process.join(timeout=30)
                self.assertEqual(process.exitcode, 0)
            self.assertEqual(sorted(outcomes), [False, True])
            self.assertFalse(list((self.state / "claims").glob("*.lock")))
        finally:
            for process in processes:
                if process.is_alive():
                    process.terminate()
                    process.join(timeout=5)
            results.close()
            results.join_thread()

    def test_recursive_discovery_and_parallel_distinct_pdfs(self):
        second = self.raw / "other/nested/2.PDF"
        write_pdf(second)
        (self.raw / "ignored.txt").write_text("synthetic")
        self.assertEqual(set(find_pdfs(self.raw)), {self.pdf, second})
        run_worker(self.raw, self.state, workers=2, poll_seconds=0.01, once=True)
        for relative in ("signal/318/1", "other/nested/2"):
            self.assertTrue((self.state / "jobs" / relative / "removal-masks/page-002.png").is_file())


if __name__ == "__main__":
    unittest.main()
