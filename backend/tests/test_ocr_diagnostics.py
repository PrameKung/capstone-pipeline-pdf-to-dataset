from contextlib import contextmanager, redirect_stdout
import io
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import MagicMock, patch

from app.pipeline.codex.client import CodexClient, OcrError, read_page_response
from app.pipeline.codex.records import EMPTY_ANSWER_REVIEW_NOTE, RECORD_VALIDATION_VERSION, RecordValidationError, validate_record
from app.pipeline.codex.settings import OcrSettings
from app.pipeline.jobs.state import checksum, read_json
from app.pipeline.jobs.worker import _claim_key, process_pdf, run_worker
from tests.synthetic_pages import write_pdf
from tests.test_ocr_pipeline import FakeClient, record_for


@contextmanager
def mock_cli(response):
    outputs = []
    def popen(command, **kwargs):
        output = Path(command[command.index("--output-last-message") + 1])
        outputs.append(output)
        process = MagicMock()
        process.__enter__.return_value = process
        process.returncode = 0
        def communicate(*args, **kwargs):
            if response is not None:
                output.write_bytes(response)
            return "synthetic-secret-stdout", "synthetic-secret-stderr"
        process.communicate.side_effect = communicate
        return process
    with patch("app.pipeline.codex.client.subprocess.Popen", side_effect=popen):
        yield outputs


class OcrDiagnosticsTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.settings = OcrSettings(max_image_side=512)

    def test_response_errors_are_distinct_and_keep_exact_bytes(self):
        output = self.root / "response.json"
        cases = [(None, "response_missing"), (b" \n", "response_empty"),
                 (b"\xff", "invalid_utf8"), (b'{"private-answer":', "invalid_json")]
        for body, reason in cases:
            with self.subTest(reason=reason):
                output.unlink(missing_ok=True)
                if body is not None:
                    output.write_bytes(body)
                with self.assertRaises(OcrError) as caught:
                    read_page_response(output, "student", 6)
                error = caught.exception
                self.assertEqual(error.code, "invalid_output")
                self.assertEqual(error.details["reason"], reason)
                self.assertEqual(error.response, body)
                self.assertNotIn("private-answer", str(error) + json.dumps(error.details))

    def test_read_error_keeps_errno_without_exception_text(self):
        with patch.object(Path, "read_bytes", side_effect=PermissionError(13, "private-path-secret")):
            with self.assertRaises(OcrError) as caught:
                read_page_response(self.root / "response.json", "student", 2)
        self.assertEqual(caught.exception.details["reason"], "response_unreadable")
        self.assertEqual(caught.exception.details["errno"], 13)
        self.assertNotIn("private-path-secret", str(caught.exception))

    def test_schema_reasons_report_safe_field_locations(self):
        wrong_type = record_for("student", 6)
        wrong_type["items"][0]["answer_markdown"] = {"private-student-name": "private-answer"}
        missing = record_for("student", 6)
        del missing["items"][0]["answer_markdown"]
        extra = record_for("student", 6)
        extra["private-student-name"] = "private-answer"
        empty = record_for("student", 6)
        empty["items"][0]["answer_markdown"] = "  "
        empty["items"][0]["question_label"] = None
        cases = [(wrong_type, "field_type", "$.items[0].answer_markdown"),
                 (missing, "missing_field", "$.items[0].answer_markdown"),
                 (extra, "unexpected_fields", "$"),
                 (record_for("student", 5), "source_page_mismatch", "$.source_page"),
                 (empty, "empty_student_answer", "$.items[0].answer_markdown"),
                 ({"source_page": 6, "items": [], "page_notes": []}, "empty_page_without_explanation", "$.items")]
        for record, reason, field in cases:
            with self.subTest(reason=reason), self.assertRaises(RecordValidationError) as caught:
                validate_record(record, "student", 6)
            self.assertEqual((caught.exception.reason, caught.exception.path), (reason, field))
            self.assertNotIn("private-", str(caught.exception))

    def test_client_retains_response_after_temporary_workspace_cleanup(self):
        image = self.root / "page.png"
        image.write_bytes(b"synthetic page (CLI mocked)")
        client = CodexClient(self.settings)
        client.executable, client.env = "synthetic-codex", {}
        body = b'{"synthetic-private-answer":'
        with mock_cli(body) as outputs, self.assertRaises(OcrError) as caught:
            client.extract("student", 6, image)
        self.assertEqual(caught.exception.response, body)
        self.assertFalse(outputs[0].parent.exists())
        self.assertNotIn("synthetic-secret", str(caught.exception) + json.dumps(caught.exception.details))

    def test_failed_run_persists_evidence_without_logging_response_or_cli_output(self):
        raw, state = self.root / "raw", self.root / "state"
        pdf = raw / "one.pdf"
        write_pdf(pdf)
        client = CodexClient(self.settings)
        client.executable, client.env = "synthetic-codex", {}
        client.version = "synthetic-cli"
        body = b'{"synthetic-private-answer":'
        logs = io.StringIO()
        with mock_cli(body), redirect_stdout(logs):
            failures = run_worker(raw, state, 1, 0.01, True, ocr_settings=self.settings, client=client)
        self.assertEqual(failures, 1)
        manifest = read_json(next((state / "runs").rglob("*.json")))
        self.assertEqual(manifest["status"], "failed")
        self.assertEqual(manifest["error_details"]["reason"], "invalid_json")
        evidence = state / manifest["failure_evidence"]
        self.assertEqual((evidence / "response.bin").read_bytes(), body)
        detail = read_json(evidence / "error.json")
        self.assertEqual(detail["response_sha256"], checksum(evidence / "response.bin"))
        self.assertEqual((detail["kind"], detail["source_page"]), ("student", 2))
        self.assertTrue((evidence / "schema.json").is_file())
        self.assertEqual(set(path.name for path in evidence.iterdir()), {"response.bin", "schema.json", "error.json"})
        self.assertNotIn("synthetic-private-answer", logs.getvalue() + json.dumps(manifest))
        self.assertNotIn("synthetic-secret", logs.getvalue() + json.dumps(manifest) + json.dumps(detail))
        self.assertIn("invalid_json", logs.getvalue())
        self.assertFalse((state / "ocr/one/latest.json").exists())
        self.assertFalse(list((state / "ocr/one/checkpoints").glob("*.json")))

    def test_missing_response_is_reported_without_inventing_evidence_bytes(self):
        raw, state = self.root / "raw", self.root / "state"
        pdf = raw / "one.pdf"
        write_pdf(pdf)
        client = CodexClient(self.settings)
        client.executable, client.env = "synthetic-codex", {}
        with mock_cli(None), self.assertRaises(OcrError):
            process_pdf(raw, state, pdf, ocr_settings=self.settings, client=client)
        manifest = read_json(next((state / "runs").rglob("*.json")))
        evidence = state / manifest["failure_evidence"]
        self.assertEqual(read_json(evidence / "error.json")["reason"], "response_missing")
        self.assertFalse((evidence / "response.bin").exists())

    def test_targeted_retry_keeps_other_failures_and_reuses_checkpoint(self):
        raw, state = self.root / "raw", self.root / "state"
        first, second = raw / "nested/one.pdf", raw / "two.pdf"
        for pdf in (first, second):
            write_pdf(pdf)
            with self.assertRaises(OcrError):
                process_pdf(raw, state, pdf, ocr_settings=self.settings, client=FakeClient(fail=("teacher", 2)))
        second_failure = state / "claims" / f"{_claim_key(raw, second)}.failed.json"
        unchanged = second_failure.read_bytes()
        client = FakeClient()
        failures = run_worker(raw, state, 1, 0.01, True, ocr_settings=self.settings, client=client,
                              source=Path("nested/one.pdf"), retry_failed=True)
        self.assertEqual(failures, 0)
        self.assertEqual([(kind, page) for kind, page, _ in client.calls], [("teacher", 2)])
        self.assertEqual(second_failure.read_bytes(), unchanged)
        self.assertTrue((state / "ocr/nested/one/latest.json").exists())
        self.assertFalse((state / "ocr/two/latest.json").exists())

    def test_targeted_source_cannot_escape_raw(self):
        raw, state = self.root / "raw", self.root / "state"
        outside = self.root / "outside.pdf"
        write_pdf(outside)
        for source, once in ((Path("../outside.pdf"), True), (outside, True), (Path("one.pdf"), False)):
            with self.subTest(source=source, once=once), self.assertRaises(ValueError):
                run_worker(raw, state, 1, 0.01, once, source=source)

    def test_identified_blank_response_is_kept_and_flagged_idempotently(self):
        record = record_for("student", 6)
        item = record["items"][0]
        item.update(question_label="2.b", answer_markdown="")
        validate_record(record, "student", 6)
        validate_record(record, "student", 6)
        self.assertEqual(item["answer_markdown"], "")
        self.assertTrue(item["needs_review"])
        self.assertEqual(item["review_notes"].count(EMPTY_ANSWER_REVIEW_NOTE), 1)
        self.assertNotIn("raw_score", item)

    def test_document_with_identified_blank_publishes_as_needs_review(self):
        raw, state = self.root / "raw", self.root / "state"
        pdf = raw / "one.pdf"
        write_pdf(pdf)
        class BlankClient(FakeClient):
            def extract(self, kind, page, image):
                record = super().extract(kind, page, image)
                if kind == "student":
                    record["items"][0]["answer_markdown"] = ""
                return record
        self.assertTrue(process_pdf(raw, state, pdf, ocr_settings=self.settings, client=BlankClient()))
        latest = read_json(state / "ocr/one/latest.json")
        self.assertEqual(latest["status"], "needs_review")
        records = read_json(state / "ocr/one/runs" / latest["run_id"] / "records.json")
        self.assertEqual(records["record_validation_version"], RECORD_VALIDATION_VERSION)
        self.assertEqual(records["records"]["student"][0]["items"][0]["answer_markdown"], "")
        markdown = (state / "ocr/one/student.md").read_text()
        self.assertIn("Review: needs_review", markdown)
        self.assertIn(EMPTY_ANSWER_REVIEW_NOTE, markdown)
        self.assertNotIn("Raw score:", markdown)


if __name__ == "__main__":
    unittest.main()
