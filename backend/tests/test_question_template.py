"""Synthetic CSV template loading and metadata isolation checks."""
import csv
import io
import json
import tempfile
import unittest
from pathlib import Path

from app.pipeline.dataset.metadata import csv_question_template, load_metadata
from app.pipeline.dataset.schema import CSV_COLUMNS


def template_bytes(rows):
    stream = io.StringIO()
    writer = csv.DictWriter(stream, fieldnames=CSV_COLUMNS)
    writer.writeheader()
    writer.writerows(rows)
    return stream.getvalue().encode()


class QuestionTemplateTests(unittest.TestCase):
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
