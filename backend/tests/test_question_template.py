"""Synthetic CSV template loading and metadata isolation checks."""
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from app.pipeline.dataset.metadata import csv_question_template, load_metadata
from app.pipeline.dataset.schema import CSV_COLUMNS
from app.pipeline.dataset.records import build_job_records
from app.pipeline.dataset.scoring import rubric_context
from app.pipeline.dataset.export import export_job


def template_bytes(rows):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


class QuestionTemplateTests(unittest.TestCase):
    def test_teacher_only_parent_score_exports_without_inventing_answers(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw, state, output = root / 'raw', root / 'state', root / 'output'
            raw.mkdir()
            source = raw / 'attempt.pdf'
            source.write_bytes(b'synthetic source identity')
            template = root / 'template.csv'
            template.write_bytes(template_bytes([
                {'question_id': '2', 'rubric_max_score': '4'},
                {'question_id': '2.1', 'rubric_max_score': '2'},
            ]))
            question = load_metadata(None, template)[2]['2']
            decision = dict(question_id='2', rubric_sha256=rubric_context(question)['rubric_sha256'],
                            status='extracted', expected_score=1.5, max_score=4,
                            basis='first_page_table', evidence='Visible row 2 shows 1.5',
                            reasoning='Read the written award', components=[],
                            first_page_score=dict(status='extracted', page=1, row=2,
                                                  raw_score='1.5', score=1.5, evidence='Row 2'),
                            question_score=dict(status='not_visible', score=None,
                                                evidence='No independent local total'))
            job = state / 'jobs' / 'attempt'
            job.mkdir(parents=True)
            (job / 'student-response.md').write_text('## Page 1\n[no visible student work]\n')
            (job / 'grading-response.md').write_text(
                '## Page 1\n### Question 2\nRaw score: 1.5\nMarks: none\nFeedback: none\n'
                'Score decision: ' + json.dumps(decision) + '\n')
            self.assertTrue(export_job(source, raw, state, output, extraction={},
                                       question_template=template))
            destination = output / 'attempt.csv'
            with destination.open(newline='') as stream:
                rows = list(csv.DictReader(stream))
            self.assertEqual([row['question_id'] for row in rows], ['2', '2.1'])
            self.assertEqual([row['answer_text'] for row in rows], ['', ''])
            self.assertEqual([row['expected_score'] for row in rows], ['1.5', ''])
            self.assertFalse(export_job(source, raw, state, output, extraction={},
                                        question_template=template))
            before = destination.read_bytes()
            template.write_text('invalid template')
            with self.assertRaises(ValueError):
                export_job(source, raw, state, output, extraction={}, question_template=template)
            self.assertEqual(destination.read_bytes(), before)

    def test_fixed_rows_keep_missing_answers_blank_and_unknown_work(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            raw, state = root / 'raw', root / 'state'
            raw.mkdir()
            source = raw / 'attempt.pdf'
            source.write_bytes(b'synthetic source identity')
            job = state / 'jobs' / 'attempt'
            job.mkdir(parents=True)
            student = job / 'student-response.md'
            student.write_text('## Page 2\n### Question 2.1\nincorrect student work\n'
                               '### Question unknown\nunmapped work\n')
            (job / 'grading-response.md').write_text('## Page 2\n[no visible grading]\n')
            questions = csv_question_template(template_bytes([
                {'question_id': '2', 'rubric_max_score': '4'},
                {'question_id': '2.1', 'rubric_max_score': '2', 'answer': 'Reference only'},
                {'question_id': '2.2', 'rubric_max_score': '2'},
            ]))['questions']
            result = build_job_records(source, raw_dir=raw, state_dir=state,
                                       questions=questions, template_layout=True)
            records = result['records']
            self.assertEqual([r['row']['question_id'] for r in records], ['2', '2.1', '2.2', ''])
            self.assertEqual([r['row']['answer_text'] for r in records],
                             ['', 'incorrect student work', '', 'unmapped work'])
            self.assertTrue(all(r['row']['expected_score'] == '' for r in records))
            self.assertFalse(result['validation']['has_failures'])
            self.assertEqual(records[0]['internal']['source_pages'], [])
            self.assertEqual(records[0]['internal']['status'], 'needs_review')
            ids = [r['row']['response_id'] for r in records]
            self.assertEqual(len(set(ids)), 4)
            student.write_text(student.read_text() + '### Question 2.2\nnew visible work\n')
            rerun = build_job_records(source, raw_dir=raw, state_dir=state,
                                      questions=questions, template_layout=True)
            self.assertEqual([r['row']['response_id'] for r in rerun['records']], ids)
            self.assertEqual(rerun['records'][2]['row']['answer_text'], 'new visible work')

    def test_csv_metadata_and_response_isolation(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'template.csv'
            path.write_bytes(template_bytes([{
                'question_id': '2.1', 'rubric_max_score': '2',
                'answer_text': 'PRIVATE RESPONSE', 'expected_score': '1',
            }]))
            metadata = Path(directory) / 'metadata.json'
            metadata.write_text(json.dumps({'questions': {'2.1': {'topic': 'Override'}}}))
            _, original, questions, responses = load_metadata(metadata, path)
            self.assertEqual(original, path.read_bytes())
            self.assertEqual(questions['2.1']['question_id'], '2.1')
            self.assertEqual(questions['2.1']['rubric_max_score'], 2.0)
            self.assertEqual(questions['2.1']['topic'], 'Override')
            self.assertNotIn('answer_text', questions['2.1'])
            self.assertNotIn('expected_score', questions['2.1'])
            self.assertEqual(responses, {})

    def test_invalid_ids_and_maxima(self):
        for rows in ([{'question_id': ''}],
                     [{'question_id': '1'}, {'question_id': '1'}],
                     [{'question_id': '1', 'rubric_max_score': 'NaN'}],
                     [{'question_id': '1', 'rubric_max_score': '-1'}]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                csv_question_template(template_bytes(rows))

    def test_json_template_still_supported(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'template.json'
            path.write_text('{"questions":{"1":{"question_id":"stable-id"}}}')
            self.assertEqual(load_metadata(None, path)[2]['1']['question_id'], 'stable-id')

    def test_canonical_labels_and_fixed_response_fields(self):
        question = csv_question_template(template_bytes([{
            'question_id': 'com-eng-math-ii-2026-1-p2.1',
            'response_origin': 'provided', 'sample_type': 'partial',
            'attempt_id': 'foreign-attempt', 'response_id': 'foreign-response',
        }]))['questions']['2.1']
        self.assertEqual(question['response_origin'], 'provided')
        self.assertEqual(question['sample_type'], 'partial')
        self.assertEqual(question['grader_feedback'], '')
        self.assertNotIn('attempt_id', question)
        self.assertNotIn('response_id', question)
