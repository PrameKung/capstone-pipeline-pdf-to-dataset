"""Synthetic OCR confidence checks; confidence is not grading correctness."""
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from app.pipeline.dataset.markdown import parse_markdown
from app.pipeline.dataset.metadata import csv_question_template
from app.pipeline.dataset.records import build_job_records
from app.pipeline.dataset.schema import CSV_COLUMNS
from app.pipeline.dataset.scoring import rubric_context
from app.pipeline.dataset.export import export_job
from app.pipeline.dataset.validation import validate_records


class ConfidenceTests(unittest.TestCase):
    def test_answer_metadata_is_separate_from_transcription(self):
        sections = parse_markdown('## Page 2\n### Question 1\n'
                                  '<!-- answer_text_confidence: 0.75 -->\nwrong math\n', 'student')
        self.assertEqual(sections[0].text, 'wrong math')
        self.assertEqual(sections[0].answer_text_confidence, 0.75)

    def test_invalid_confidence_is_blank_and_reviewed(self):
        for value in ['-0.1', '1.1', 'NaN', 'true', '"0.9"']:
            with self.subTest(value=value):
                section = parse_markdown('## Page 2\n### Question 1\n'
                                         f'<!-- answer_text_confidence: {value} -->\nwork', 'student')[0]
                self.assertIsNone(section.answer_text_confidence)
                self.assertEqual(section.text, 'work')
                self.assertTrue(section.review_notes)

    def test_zero_is_valid_and_duplicate_confidence_is_not(self):
        prefix = '## Page 2\n### Question 1\n'
        line = '<!-- answer_text_confidence: 0 -->\n'
        section = parse_markdown(prefix + line + 'work', 'student')[0]
        self.assertEqual(section.answer_text_confidence, 0)
        repeated = parse_markdown(prefix + line + line + 'work', 'student')[0]
        self.assertIsNone(repeated.answer_text_confidence)
        self.assertTrue(repeated.review_notes)

    def test_old_template_header_remains_accepted(self):
        old_columns = [c for c in CSV_COLUMNS if c not in
                       {'answer_text_confidence', 'expected_score_confidence'}]
        stream = io.StringIO()
        writer = csv.DictWriter(stream, fieldnames=old_columns)
        writer.writeheader()
        writer.writerow({'question_id': '1'})
        question = csv_question_template(stream.getvalue().encode())['questions']['1']
        self.assertNotIn('answer_text_confidence', question)
        self.assertNotIn('expected_score_confidence', question)

    def test_exported_confidence_requires_supported_content_and_score(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw, state = root / 'raw', root / 'state'
            raw.mkdir()
            source = raw / 'attempt.pdf'
            source.write_bytes(b'synthetic identity')
            job = state / 'jobs' / 'attempt'
            job.mkdir(parents=True)
            questions = {'1': {'question_id': '1', 'rubric_max_score': 2}}
            student = job / 'student-response.md'
            student.write_text('## Page 2\n### Question 1\n'
                               '<!-- answer_text_confidence: 0.9 -->\nfirst part\n'
                               '## Page 3\n### Question 1\n'
                               '<!-- answer_text_confidence: 0.6 -->\nsecond part\n')
            decision = dict(question_id='1', rubric_sha256=rubric_context(questions['1'])['rubric_sha256'],
                            status='extracted', expected_score=1, max_score=2,
                            expected_score_confidence=0.8, basis='first_page_table',
                            evidence='Written row 1 score', reasoning='Read visible total', components=[],
                            first_page_score=dict(status='extracted', page=1, row=1, raw_score='1',
                                                  score=1, evidence='Row 1'),
                            question_score=dict(status='not_visible', score=None, evidence='No local total'))
            grading = job / 'grading-response.md'
            def write_grading():
                grading.write_text('## Page 1\n### Question 1\nRaw score: 1\n'
                                   'Marks: none\nFeedback: none\nScore decision: '+json.dumps(decision))
            def build():
                return build_job_records(source, raw_dir=raw, state_dir=state, questions=questions)['records'][0]
            write_grading()
            record = build()
            self.assertEqual(record['row']['answer_text_confidence'], '0.6')
            self.assertEqual(record['row']['expected_score_confidence'], '0.8')
            self.assertNotIn('confidence', record['row']['answer_text'])
            self.assertFalse(validate_records([record], attempt_id=record['row']['attempt_id'])['has_failures'])
            template = root / 'questions.json'
            template.write_text(json.dumps({'questions': questions}))
            export_job(source, raw, state, root / 'output', extraction={}, question_template=template)
            with (root / 'output/attempt.csv').open(newline='') as stream:
                exported = next(csv.DictReader(stream))
            self.assertEqual(exported['answer_text_confidence'], '0.6')
            self.assertEqual(exported['expected_score_confidence'], '0.8')
            decision['evidence'] = ['Page 1 row 1: written award 1', 'Mapped parent question 1']
            write_grading()
            self.assertEqual(build()['row']['expected_score'], '1')
            for invalid in [[], [''], ['visible score', None], {'page': 1}]:
                decision['evidence'] = invalid
                write_grading()
                self.assertEqual(build()['row']['expected_score'], '')
            decision['evidence'] = ['Page 1 row 1: written award 1']
            record['row']['answer_text_confidence'] = '1.1'
            self.assertTrue(validate_records([record], attempt_id=record['row']['attempt_id'])['has_failures'])
            for value in [True, -1, 2, '0.8']:
                decision['expected_score_confidence'] = value
                write_grading()
                self.assertEqual(build()['row']['expected_score_confidence'], '')
            decision['expected_score_confidence'] = 0.8
            decision['expected_score'] = 3
            write_grading()
            record = build()
            self.assertEqual(record['row']['expected_score'], '')
            self.assertEqual(record['row']['expected_score_confidence'], '')
            student.write_text('## Page 2\n### Question 1\nlegacy work\n')
            self.assertEqual(build()['row']['answer_text_confidence'], '')
            student.write_text('## Page 2\n### Question 1\n'
                               '<!-- answer_text_confidence: 1 -->\nfirst part\n'
                               '## Page 3\n### Question 1\nno confidence here\n')
            self.assertEqual(build()['row']['answer_text_confidence'], '')
