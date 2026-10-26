"""Offline regression checks using synthetic PDFs only."""
import hashlib
import tempfile
import unittest
from pathlib import Path

import pymupdf

from app.pipeline.pdf.preprocess import prepare_pdf_with_redacted_header
from app.pipeline.dataset.markdown import MarkdownFormatError, parse_markdown
from app.pipeline.jobs.worker import process_pdf, run_worker
from unittest.mock import patch


class PreparationTests(unittest.TestCase):
    def test_redaction_preserves_pages_and_source(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            source = root / 'source.pdf'
            with pymupdf.open() as doc:
                page = doc.new_page(width=200, height=300)
                page.insert_text((20, 40), 'PRIVATE HEADER')
                page.insert_text((20, 230), 'VISIBLE ANSWER')
                doc.new_page().insert_text((20, 40), 'SECOND PAGE')
                doc.save(source)
            before = hashlib.sha256(source.read_bytes()).digest()
            result = prepare_pdf_with_redacted_header(source, root / 'job')
            self.assertEqual(before, hashlib.sha256(source.read_bytes()).digest())
            with pymupdf.open(result) as doc:
                self.assertEqual(len(doc), 2)
                self.assertNotIn('PRIVATE', doc[0].get_text())
                self.assertIn('VISIBLE ANSWER', doc[0].get_text())
                self.assertIn('SECOND PAGE', doc[1].get_text())

    def test_scanned_single_page_and_rotations(self):
        for rotation in (0, 90, 180, 270):
            with self.subTest(rotation=rotation), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                source = root / 'scan.pdf'
                pixmap = pymupdf.Pixmap(pymupdf.csRGB, pymupdf.IRect(0, 0, 200, 300), False)
                pixmap.clear_with(120)
                with pymupdf.open() as doc:
                    page = doc.new_page(width=200, height=300)
                    page.insert_image(page.rect, pixmap=pixmap)
                    page.set_rotation(rotation)
                    doc.save(source)
                result = prepare_pdf_with_redacted_header(source, root / 'job')
                with pymupdf.open(result) as doc:
                    self.assertEqual(len(doc), 1)
                    rendered = doc[0].get_pixmap()
                    self.assertEqual(rendered.pixel(rendered.width // 2, rendered.height // 4), (0, 0, 0))
                    self.assertEqual(rendered.pixel(rendered.width // 2, 3 * rendered.height // 4), (120, 120, 120))
                    # Image data beneath the redaction is removed too.
                    image = pymupdf.Pixmap(doc, doc[0].get_images()[0][0])
                    self.assertNotEqual(hashlib.sha256(image.samples).digest(),
                                        hashlib.sha256(pixmap.samples).digest())

    def test_prepare_only_never_extracts_or_exports(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw = root / 'raw'
            raw.mkdir()
            source = raw / 'nested' / 'exam.pdf'
            source.parent.mkdir()
            with pymupdf.open() as doc:
                doc.new_page()
                doc.save(source)
            state, output = root / 'state', root / 'output'
            with patch('app.pipeline.jobs.worker.export_job', side_effect=AssertionError('Unexpected export')), patch('app.pipeline.codex.extract.extract_pdf', side_effect=AssertionError('Unexpected OCR')):
                self.assertTrue(process_pdf(raw, state, source, output_dir=output, prepare_only=True))
                prepared = state / 'jobs/nested/exam/pages-redacted.pdf'
                before = prepared.stat().st_mtime_ns
                self.assertFalse(process_pdf(raw, state, source, output_dir=output, prepare_only=True))
                self.assertEqual(prepared.stat().st_mtime_ns, before)
                self.assertEqual(list(prepared.parent.iterdir()), [prepared])
                self.assertFalse(output.exists())
                self.assertEqual(run_worker(raw, state, 2, 1, True, output_dir=output, prepare_only=True), 0)

    def test_page_one_is_eligible(self):
        sections = parse_markdown('## Page 1\n### Question 1\nVisible answer', 'student')
        self.assertEqual(sections[0].page, 1)
        with self.assertRaises(MarkdownFormatError):
            parse_markdown('## Page 0\n### Question 1\nInvalid', 'student')


if __name__ == '__main__':
    unittest.main()
