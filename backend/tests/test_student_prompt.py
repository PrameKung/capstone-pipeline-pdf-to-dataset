"""Offline request-assembly check, not a PDF, lock, CLI or OCR accuracy test."""
import importlib.util
from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch

from app.pipeline.dataset.markdown import parse_markdown
from app.pipeline.dataset.schema import CSV_COLUMNS


class StudentPromptTests(unittest.TestCase):
    def test_evidence_instructions_reach_only_student_and_contract_survives(self):
        source = Path(__file__).resolve().parents[1] / 'app/pipeline/codex/extract.py'
        spec = importlib.util.spec_from_file_location('isolated_prompt_assembly', source)
        extractor = importlib.util.module_from_spec(spec)
        # These modules are needed only to import the production assembly code.
        # Rendering and the complete CLI/locking boundary are mocked below.
        with patch.dict(sys.modules, {'fcntl': types.ModuleType('fcntl'), 'pymupdf': types.ModuleType('pymupdf')}):
            spec.loader.exec_module(extractor)
        student = ('## Page 2\n### Question 2.1\n'
                   '<!-- answer_text_confidence: 0.4 -->\n'
                   '1 + 1 = 3\n~~old work~~\n[unclear] [exponent in second equation]')
        grading = '## Page 2\n### Question 2.1\nRaw score: 0.5/1\nMarks: partial\nFeedback: teacher-only'
        calls = {}

        def render(pdf, directory, dpi):
            directory.mkdir(parents=True, exist_ok=True)
            return [directory/'page-1.png', directory/'page-2.png']

        def run_pass(state, limit, cli, name, prompt, images, directory, **options):
            calls[name] = (prompt, images)
            return student if name == 'student' else grading

        with tempfile.TemporaryDirectory() as directory:
            state = Path(directory)
            prepared = state/'prepared.pdf'
            prepared.write_bytes(b'synthetic placeholder; rendering is mocked')
            with patch.object(extractor, 'check_codex', return_value='unused'), \
                 patch.object(extractor, '_render', side_effect=render), \
                 patch.object(extractor, '_limited_pass', side_effect=run_pass):
                output = extractor.extract_pdf(prepared, state/'ocr.md', state)
            self.assertTrue(output.is_file())
            self.assertEqual((state/'student-response.md').read_text(encoding='utf-8').strip(), student)
            self.assertEqual((state/'grading-response.md').read_text(encoding='utf-8').strip(), grading)
        for block in [extractor.STUDENT_PREPARATION_PROMPT, extractor.STUDENT_EVIDENCE_PROMPT,
                      extractor.STUDENT_COMPLETENESS_PROMPT]:
            self.assertIn(block, calls['student'][0])
            self.assertNotIn(block, calls['grading'][0])
        self.assertNotIn('<supplied_rubric>', calls['student'][0])
        self.assertIn('<supplied_rubric>', calls['grading'][0])
        self.assertEqual(len(calls['student'][1]), 2)
        sections = parse_markdown(student, 'student')
        self.assertEqual(len(sections), 1)
        self.assertEqual((sections[0].page, sections[0].question_label), (2, '2.1'))
        self.assertIn('1 + 1 = 3', sections[0].text)
        self.assertIn('~~old work~~', sections[0].text)
        self.assertIn('[unclear]', sections[0].text)
        self.assertNotIn('answer_text_confidence', sections[0].text)
        self.assertEqual(sections[0].answer_text_confidence, 0.4)
        self.assertTrue(sections[0].review_notes)
        self.assertEqual(len(CSV_COLUMNS), 38)


if __name__ == '__main__':
    unittest.main()
