from dataclasses import asdict, replace
import json
import multiprocessing
import os
from pathlib import Path
import tempfile
import time
import unittest
from unittest.mock import patch

import cv2
import numpy as np

from app.pipeline.codex.client import OcrError, codex_environment
from app.pipeline.codex.extract import describe_inputs, digest, extract_document, is_published
from app.pipeline.codex.records import validate_record
from app.pipeline.codex.settings import OCR_VERSION, OcrSettings
from app.pipeline.jobs.state import atomic_json, checksum, claim, ocr_slot, read_json
from app.pipeline.jobs.worker import _claim_key, process_pdf, run_worker
from app.pipeline.pdf.preprocess import cut_first_page_and_render
from tests.synthetic_pages import write_pdf


def record_for(kind, page):
    item = {"question_label": "1", "needs_review": False, "review_notes": []}
    if kind == "student":
        item["answer_markdown"] = "$x = 2 + 3 = 6$"  # Deliberate synthetic error must remain unchanged.
    else:
        item.update(raw_score="2+2", marks=["check"], feedback=None)
    return {"source_page": page, "items": [item], "page_notes": []}


class FakeClient:
    version = "synthetic-cli"

    def __init__(self, fail=None):
        self.calls = []
        self.fail = fail

    def extract(self, kind, page, image):
        self.calls.append((kind, page, image))
        if self.fail == (kind, page):
            raise OcrError("timeout", "Synthetic timeout")
        return record_for(kind, page)


def _slot_probe(state, active, peak):
    with ocr_slot(Path(state), 1):
        with active.get_lock():
            active.value += 1
            peak.value = max(peak.value, active.value)
        time.sleep(0.15)
        with active.get_lock():
            active.value -= 1


def _hold_claim(path, ready):
    with claim(Path(path)) as acquired:
        ready.set()
        if acquired:
            time.sleep(30)


class OcrPipelineTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.raw, self.state = self.root / "raw", self.root / "state"
        self.pdf = self.raw / "nested/one.pdf"
        write_pdf(self.pdf)
        self.job = self.state / "jobs/nested/one"
        self.ocr = self.state / "ocr/nested/one"
        cut_first_page_and_render(self.pdf, self.job, dpi=72)
        self.settings = OcrSettings(max_image_side=512)
        self.client = FakeClient()

    def extract(self, run_id="synthetic-run", client=None, settings=None):
        settings = settings or self.settings
        return extract_document(self.job, self.ocr, self.state, settings, client or self.client,
                                {"run_id": run_id, "attempt_id": "synthetic-attempt", "source_relative_path": "nested/one.pdf"},
                                self.state / "runs" / f"{run_id}.json", describe_inputs(self.job, settings), source_pdf=self.pdf)

    def test_separate_inputs_excluded_identity_literal_math_scores_and_merge(self):
        result = self.extract()
        self.assertEqual(result["status"], "completed")
        self.assertEqual([(kind, page) for kind, page, _ in self.client.calls], [("student", 2), ("teacher", 2)])
        for kind, _, image in self.client.calls:
            self.assertEqual(image.parent.name, kind)
            np.testing.assert_array_equal(cv2.imread(str(image)), cv2.imread(str(self.job / f"{kind}-view/page-002.png")))
        student = (self.ocr / "student.md").read_text()
        teacher = (self.ocr / "teacher.md").read_text()
        self.assertIn("2 + 3 = 6", student)
        self.assertNotIn("Raw score", student)
        self.assertIn("Raw score: 2+2", teacher)
        self.assertNotIn("2 + 3 = 6", teacher)
        combined = (self.ocr / "ocr.md").read_text()
        self.assertIn("## Student answers", combined)
        self.assertIn("## Teacher grading", combined)
        self.assertTrue(is_published(self.ocr, describe_inputs(self.job, self.settings)))

    def test_failure_preserves_published_set_and_resume_reuses_student(self):
        self.extract()
        old = {name: (self.ocr / name).read_bytes() for name in ("student.md", "teacher.md", "ocr.md", "latest.json")}
        changed_settings = OcrSettings(max_image_side=600)
        failing = FakeClient(fail=("teacher", 2))
        with self.assertRaises(OcrError):
            self.extract("failed-run", failing, changed_settings)
        self.assertEqual(old, {name: (self.ocr / name).read_bytes() for name in old})
        resumed = FakeClient()
        self.extract("resumed-run", resumed, changed_settings)
        self.assertEqual([(kind, page) for kind, page, _ in resumed.calls], [("teacher", 2)])

    def test_changed_image_invalidates_only_affected_pass(self):
        self.extract()
        image_path = self.job / "student-view/page-002.png"
        data = cv2.imread(str(image_path))
        data[20:30, 20:30] = 127
        cv2.imwrite(str(image_path), data)
        self.assertFalse(is_published(self.ocr, describe_inputs(self.job, self.settings)))
        self.client.calls.clear()
        self.extract("changed")
        self.assertEqual([(kind, page) for kind, page, _ in self.client.calls], [("student", 2)])

    def test_capacity_change_preserves_legacy_single_slot_signatures(self):
        original = digest({"version": OCR_VERSION, **asdict(self.settings)})
        self.assertEqual(self.settings.signature(), original)
        self.assertEqual(replace(self.settings, capacity=2).signature(), original)
        self.assertEqual(replace(self.settings, capacity=4).signature(), original)
        for change in ({"model": "different-model"}, {"effort": "low"}, {"max_image_side": 600}):
            self.assertNotEqual(replace(self.settings, **change).signature(), original)

    def test_capacity_change_does_not_rerun_completed_pdf(self):
        process_pdf(self.raw, self.state, self.pdf, ocr_settings=self.settings, client=self.client)
        before = (self.ocr / "latest.json").read_bytes()
        self.client.calls.clear()
        self.assertFalse(process_pdf(self.raw, self.state, self.pdf,
                                     ocr_settings=replace(self.settings, capacity=2), client=self.client))
        self.assertEqual(self.client.calls, [])
        self.assertEqual((self.ocr / "latest.json").read_bytes(), before)
        self.assertEqual(len(list((self.state / "runs").rglob("*.json"))), 1)

    def test_capacity_change_resumes_successful_page_checkpoints(self):
        with self.assertRaises(OcrError):
            process_pdf(self.raw, self.state, self.pdf, ocr_settings=self.settings, client=FakeClient(fail=("teacher", 2)))
        client = FakeClient()
        self.assertTrue(process_pdf(self.raw, self.state, self.pdf, ocr_settings=replace(self.settings, capacity=2),
                                    client=client, retry_failed=True))
        self.assertEqual([(kind, page) for kind, page, _ in client.calls], [("teacher", 2)])

    def test_corrupt_checkpoint_is_not_reused(self):
        self.extract()
        checkpoint = next(self.ocr.glob("checkpoints/*.json"))
        saved = read_json(checkpoint)
        saved["record"]["source_page"] = 1
        atomic_json(checkpoint, saved)
        self.client.calls.clear()
        self.extract("repaired")
        self.assertEqual(len(self.client.calls), 1)

    def test_invalid_schema_page_and_empty_output_fail(self):
        invalid = [record_for("teacher", 2), {"source_page": 2, "items": [], "page_notes": []}, record_for("student", 1)]
        for value in invalid:
            with self.assertRaises(ValueError):
                validate_record(value, "student", 2)
        unknown = record_for("teacher", 2)
        unknown["items"][0]["question_label"] = None
        self.assertTrue(validate_record(unknown, "teacher", 2)["items"][0]["needs_review"])

    def test_source_change_during_ocr_does_not_publish(self):
        client = FakeClient()
        original_extract = client.extract
        def change(kind, page, image):
            result = original_extract(kind, page, image)
            if kind == "teacher":
                with self.pdf.open("ab") as handle:
                    handle.write(b"\n% synthetic replacement\n")
            return result
        client.extract = change
        with self.assertRaisesRegex(OcrError, "Source PDF changed"):
            self.extract(client=client)
        self.assertFalse((self.ocr / "latest.json").exists())

    def test_same_path_new_bytes_get_new_attempt_and_durable_records(self):
        self.assertTrue(process_pdf(self.raw, self.state, self.pdf))
        before = list((self.state / "runs").rglob("*.json"))
        first = read_json(before[0])
        self.assertFalse(process_pdf(self.raw, self.state, self.pdf))
        with self.pdf.open("ab") as handle:
            handle.write(b"\n% synthetic changed PDF\n")
        self.assertTrue(process_pdf(self.raw, self.state, self.pdf))
        records = [read_json(path) for path in (self.state / "runs").rglob("*.json")]
        self.assertEqual(len(records), 2)
        self.assertEqual(len({record["attempt_id"] for record in records}), 2)
        self.assertEqual({record["status"] for record in records}, {"completed"})
        self.assertNotEqual(first["source_sha256"], checksum(self.pdf))

    def test_failed_ocr_is_durable_skipped_until_explicit_retry(self):
        self.client.fail = ("teacher", 2)
        with self.assertRaises(OcrError):
            process_pdf(self.raw, self.state, self.pdf, ocr_settings=self.settings, client=self.client)
        record = read_json(next((self.state / "runs").rglob("*.json")))
        self.assertEqual(record["status"], "failed")
        self.assertEqual(record["completed_page_passes"], 1)
        self.assertFalse(process_pdf(self.raw, self.state, self.pdf, ocr_settings=self.settings, client=self.client))
        next((self.state / "claims").glob("*.failed.json")).unlink()
        self.client.fail = None
        self.client.calls.clear()
        self.assertTrue(process_pdf(self.raw, self.state, self.pdf, ocr_settings=self.settings, client=self.client))
        self.assertEqual([(kind, page) for kind, page, _ in self.client.calls], [("teacher", 2)])
        attempts = {read_json(path)["attempt_id"] for path in (self.state / "runs").rglob("*.json")}
        self.assertEqual(len(attempts), 1)

    def test_missing_artifact_regenerates_preprocessing(self):
        process_pdf(self.raw, self.state, self.pdf)
        (self.job / "teacher-view/page-002.png").unlink()
        self.assertTrue(process_pdf(self.raw, self.state, self.pdf))
        self.assertTrue((self.job / "teacher-view/page-002.png").is_file())

    def test_api_key_auth_rejected_without_secret_in_error(self):
        auth = self.root / "auth.json"
        auth.write_text(json.dumps({"auth_mode": "apikey", "OPENAI_API_KEY": "synthetic-private-secret"}))
        with patch.dict(os.environ, {"CODEX_AUTH_FILE": str(auth)}):
            with self.assertRaises(OcrError) as caught:
                codex_environment()
        self.assertNotIn("synthetic-private-secret", str(caught.exception))

    def test_reconcile_crash_after_publication_without_repeating_ocr(self):
        process_pdf(self.raw, self.state, self.pdf, ocr_settings=self.settings, client=self.client)
        path = next((self.state / "runs").rglob("*.json"))
        record = read_json(path)
        record.update(status="running", stage="markdown")
        atomic_json(path, record)
        self.client.calls.clear()
        self.assertFalse(process_pdf(self.raw, self.state, self.pdf, ocr_settings=self.settings, client=self.client))
        self.assertEqual(self.client.calls, [])
        self.assertEqual(read_json(path)["status"], "completed")

    def test_quota_pauses_other_documents_without_fallback(self):
        class QuotaClient(FakeClient):
            def extract(self, kind, page, image):
                raise OcrError("quota", "Synthetic subscription quota")
        with self.assertRaises(OcrError):
            process_pdf(self.raw, self.state, self.pdf, ocr_settings=self.settings, client=QuotaClient())
        self.assertEqual(read_json(self.state / "ocr-paused.json")["code"], "quota")
        second = self.raw / "second.pdf"
        write_pdf(second)
        self.assertFalse(process_pdf(self.raw, self.state, second, ocr_settings=self.settings, client=self.client))
        self.assertEqual(self.client.calls, [])

    def test_polling_considers_later_sources_after_cached_first_source(self):
        second = self.raw / "second.pdf"
        write_pdf(second)
        visited = []
        def inspect(raw, state, source, **kwargs):
            visited.append(source)
            return False
        ticks = 0
        real_sleep = time.sleep
        def stop_after_ticks(seconds):
            nonlocal ticks
            ticks += 1
            if ticks > 8:
                raise InterruptedError("Synthetic stop")
            real_sleep(0.01)
        with patch("app.pipeline.jobs.worker.process_pdf", side_effect=inspect), patch("app.pipeline.jobs.worker.time.sleep", side_effect=stop_after_ticks):
            with self.assertRaises(InterruptedError):
                run_worker(self.raw, self.state, 1, 0.01, False)
        self.assertEqual(set(visited), {self.pdf, second})

    def test_global_slot_capacity_shared_across_processes(self):
        context = multiprocessing.get_context("spawn")
        active, peak = context.Value("i", 0), context.Value("i", 0)
        processes = [context.Process(target=_slot_probe, args=(str(self.state), active, peak)) for _ in range(3)]
        for process in processes:
            process.start()
        for process in processes:
            process.join(20)
            self.assertEqual(process.exitcode, 0)
        self.assertEqual(peak.value, 1)

    def test_claim_recovers_after_process_crash(self):
        context = multiprocessing.get_context("spawn")
        ready = context.Event()
        path = self.state / "claims/test.guard"
        process = context.Process(target=_hold_claim, args=(str(path), ready))
        process.start()
        try:
            self.assertTrue(ready.wait(10))
            with claim(path) as acquired:
                self.assertFalse(acquired)
            process.terminate()
            process.join(10)
            with claim(path) as acquired:
                self.assertTrue(acquired)
        finally:
            if process.is_alive():
                process.terminate()
                process.join(5)


if __name__ == "__main__":
    unittest.main()
