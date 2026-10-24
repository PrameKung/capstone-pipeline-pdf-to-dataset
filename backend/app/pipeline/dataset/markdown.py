"""Parse the OCR Markdown contract without interpreting answers or scores."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

PAGE = re.compile(r"^## Page ([0-9]+)\s*$")
QUESTION = re.compile(r"^### Question (.+?)\s*$")
GRADING_FIELD = re.compile(r"^(Raw score|Marks|Feedback):[ \t]*(.*)$")


class MarkdownFormatError(ValueError):
    """The document cannot be parsed safely with original-page provenance."""


@dataclass
class Section:
    page: int
    question_label: str | None
    text: str
    review_notes: list[str] = field(default_factory=list)


@dataclass
class GradingRecord:
    section: Section
    raw_score_text: str | None
    marks: str | None
    feedback: str | None
    review_notes: list[str] = field(default_factory=list)


@dataclass
class Response:
    question_label: str | None
    sections: list[Section]
    grading: list[GradingRecord] = field(default_factory=list)
    review_notes: list[str] = field(default_factory=list)

    @property
    def answer_text(self) -> str:
        return "\n\n".join(section.text for section in self.sections)

    @property
    def source_pages(self) -> list[int]:
        return list(dict.fromkeys(section.page for section in self.sections))

    @property
    def needs_review(self) -> bool:
        return bool(self.review_notes or any(s.review_notes for s in self.sections)
                    or any(g.review_notes or g.section.review_notes for g in self.grading))


@dataclass
class MatchedMarkdown:
    responses: list[Response]
    unmatched_grading: list[GradingRecord]


def parse_markdown(text: str, kind: Literal["student", "grading"]) -> list[Section]:
    """Read exact OCR headings; reject invalid provenance rather than guessing.

    Unknown/unlabeled sections remain separate. Boundary blank lines are removed;
    interior Markdown, LaTeX and whitespace are preserved.
    """
    if kind not in {"student", "grading"}:
        raise ValueError("kind must be student or grading")
    empty_marker = f"[no visible {'student work' if kind == 'student' else 'grading'}]"
    sections: list[Section] = []
    page: int | None = None
    label: str | None = None
    lines: list[str] = []
    has_question = False

    def flush() -> None:
        body = "\n".join(lines).strip("\n")
        if not body.strip() and not has_question:
            return
        if page is None:
            raise MarkdownFormatError("Content before the first original-page heading")
        if body.strip() == empty_marker and not has_question:
            return
        notes = []
        if label is None:
            notes.append("Question association is unknown")
        if not body.strip():
            notes.append("Empty question section")
        if "[unclear]" in body.lower():
            notes.append("OCR contains unclear content")
        sections.append(Section(page, label, body, notes))

    for line_number, line in enumerate(text.splitlines(), 1):
        page_match, question_match = PAGE.fullmatch(line), QUESTION.fullmatch(line)
        if page_match:
            flush()
            next_page = int(page_match[1])
            if next_page < 2 or (page is not None and next_page <= page):
                raise MarkdownFormatError(f"Invalid original page order at line {line_number}; pages must increase from 2 or later")
            page, label, lines, has_question = next_page, None, [], False
        elif question_match:
            if page is None:
                raise MarkdownFormatError(f"Question before page heading at line {line_number}")
            flush()
            value = question_match[1].strip()
            label = None if value.casefold() == "unknown" else value
            lines, has_question = [], True
        elif line.startswith(("## Page", "### Question", "# ", "## ", "### ")):
            raise MarkdownFormatError(f"Unexpected heading at line {line_number}")
        else:
            lines.append(line)
    flush()
    if page is None:
        raise MarkdownFormatError("No original-page headings found")
    return sections


def _grading(section: Section) -> GradingRecord:
    values: dict[str, list[str]] = {}
    current: str | None = None
    notes: list[str] = []
    for line in section.text.splitlines():
        match = GRADING_FIELD.fullmatch(line)
        if match:
            current = match[1]
            if current in values:
                notes.append(f"Repeated grading field: {current}")
            values.setdefault(current, []).append(match[2])
        elif current is not None:
            values[current].append(line)
        elif line.strip():
            notes.append("Unstructured grading content retained in source section")
    for name in ("Raw score", "Marks", "Feedback"):
        if name not in values:
            notes.append(f"Missing grading field: {name}")

    def value(name: str) -> str | None:
        if name not in values:
            return None
        result = "\n".join(values[name]).strip("\n")
        return None if result.strip() in {"", "[not visible]"} else result

    return GradingRecord(section, value("Raw score"), value("Marks"), value("Feedback"), notes)


def match_markdown(student: str, grading: str) -> MatchedMarkdown:
    """Match exact visible labels, never infer IDs, subparts, or numeric scores.

    A label occurring once per page is grouped across pages. Repeated labels on
    any single page remain separate and all grading for that label is unresolved.
    Unknown labels are never matched merely because they share a page.
    """
    student_sections = parse_markdown(student, "student")
    grading_sections = parse_markdown(grading, "grading")
    counts: dict[tuple[str, int], int] = {}
    for section in student_sections:
        if section.question_label is not None:
            key = section.question_label, section.page
            counts[key] = counts.get(key, 0) + 1
    ambiguous = {label for (label, _), count in counts.items() if count > 1}
    responses: list[Response] = []
    by_label: dict[str, Response] = {}
    for section in student_sections:
        label = section.question_label
        if label is not None and label not in ambiguous and label in by_label:
            by_label[label].sections.append(section)
            continue
        response = Response(label, [section])
        responses.append(response)
        if label in ambiguous:
            response.review_notes.append("Repeated question label on the same page; association is ambiguous")
        elif label is not None:
            by_label[label] = response
    unmatched: list[GradingRecord] = []
    for section in grading_sections:
        record = _grading(section)
        response = by_label.get(section.question_label)
        if response is None:
            record.review_notes.append("No unambiguous student response matches this question label")
            unmatched.append(record)
        else:
            response.grading.append(record)
    for response in responses:
        if not response.grading:
            response.review_notes.append("No matching teacher grading record")
        grading_pages = [g.section.page for g in response.grading]
        if len(grading_pages) != len(set(grading_pages)):
            response.review_notes.append("Repeated grading sections on the same page; association requires review")
        scores = {g.raw_score_text for g in response.grading if g.raw_score_text is not None}
        if len(scores) > 1:
            response.review_notes.append("Multiple distinct raw scores; interpretation requires review")
    return MatchedMarkdown(responses, unmatched)


def read_job_markdown(job_dir: Path) -> MatchedMarkdown:
    """Parse existing saved passes without running OCR or writing artifacts."""
    return match_markdown(
        (job_dir / "student-response.md").read_text(encoding="utf-8"),
        (job_dir / "grading-response.md").read_text(encoding="utf-8"),
    )
