"""Run the local Codex CLI on one prepared PDF from a worker job."""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import tempfile
from pathlib import Path

import pymupdf

DEFAULT_MODEL = "gpt-6.1-sol"
DEFAULT_EFFORT = "high"
VALID_EFFORTS = {"minimal", "low", "medium", "high", "xhigh"}

MARKDOWN_FORMAT = r"""
Output contract:
- Return Markdown only, without an introduction, summary, or outer code fence.
- Follow image order and source reading order. Use exactly `## Page N` for each
  original page (the first supplied page is page 2), then `### Question LABEL`
  for each visibly labeled question. Preserve the visible label, including subparts.
  Use `### Question unknown` when no association is visible; never invent a label.
- Preserve the source language, mathematical errors, incomplete expressions, and
  step order. Do not add derivations, implied steps, or missing symbols.
- Use $...$ for inline mathematics and $$ on separate lines for display mathematics,
  with a blank line before and after each display block. Put each standalone
  equation or work step in its own display block, outside lists and tables.
- Use standard KaTeX/MathJax-compatible LaTeX, such as \frac{a}{b}, \sqrt{x},
  x_{n}, x^{2}, \pi, \omega, \sum, and \int. Use single backslashes for commands
  in the Markdown output, not JSON-escaped double backslashes. Use \\ only for
  row breaks inside supported environments such as aligned, cases, or bmatrix.
- Balance delimiters and braces. Use \begin{aligned} ... \end{aligned} inside
  one display block only when alignment is needed. Do not use \(...\), \[...\],
  equation/align environments, custom macros, packages, document preambles,
  equation numbering commands, HTML, or code fences for math.
- Keep prose outside math delimiters. Escape literal currency dollars as \$.
  Use [unclear] for unreadable content and [diagram: ...] for a brief description
  of visible diagram features. Keep these annotations outside math blocks. If an
  expression cannot be transcribed without guessing, report its readable fragments
  separately with [unclear] between them; never repair the source mathematics.
""".strip()


def _codex_env() -> dict[str, str]:
    env = os.environ.copy()
    # Require the local ChatGPT login; never fall back to API-key billing.
    env.pop("OPENAI_API_KEY", None)
    env.pop("CODEX_API_KEY", None)
    auth_file = env.get("CODEX_AUTH_FILE")
    if auth_file:
        source = Path(auth_file)
        if not source.is_file():
            raise RuntimeError(
                f"Codex login file is unavailable at {source}; run `codex login` on the host."
            )
        codex_home = Path(env.get("CODEX_HOME", "/tmp/codex-home"))
        codex_home.mkdir(parents=True, exist_ok=True)
        target = codex_home / "auth.json"
        if not target.exists():
            fd, temp_name = tempfile.mkstemp(prefix=".auth-", dir=codex_home)
            os.close(fd)
            temp_auth = Path(temp_name)
            try:
                shutil.copyfile(source, temp_auth)
                temp_auth.chmod(0o600)
                try:
                    os.link(temp_auth, target)
                except FileExistsError:
                    pass
            finally:
                temp_auth.unlink(missing_ok=True)
    return env


def check_codex(model: str, effort: str) -> str:
    codex = shutil.which("codex")
    if not codex:
        raise RuntimeError("Codex CLI is unavailable. Install it and run `codex login` first.")

    env = _codex_env()
    help_result = subprocess.run([codex, "exec", "--help"], capture_output=True, text=True, env=env)
    help_text = help_result.stdout + help_result.stderr
    if help_result.returncode or "--image" not in help_text or "--output-last-message" not in help_text:
        raise RuntimeError("Codex CLI must support `exec --image` and `--output-last-message`.")

    login_result = subprocess.run([codex, "login", "status"], capture_output=True, text=True, env=env)
    login_text = (login_result.stdout + login_result.stderr).lower()
    if login_result.returncode or "logged in using chatgpt" not in login_text:
        raise RuntimeError("Sign in to Codex with your ChatGPT subscription using `codex login`.")

    models_result = subprocess.run(
        [codex, "debug", "models"], capture_output=True, text=True, env=env, timeout=60
    )
    if models_result.returncode:
        raise RuntimeError("Could not check Codex model availability; check CLI access and try again.")
    try:
        model_catalog = json.loads(models_result.stdout)
        selected_model = next(item for item in model_catalog["models"] if item["slug"] == model)
    except (KeyError, StopIteration, json.JSONDecodeError, TypeError) as exc:
        raise RuntimeError(f"Codex model `{model}` is not available in the current model catalog.") from exc
    supported_efforts = {
        item.get("effort") for item in selected_model.get("supported_reasoning_levels", [])
    }
    if effort not in supported_efforts:
        supported = ", ".join(sorted(value for value in supported_efforts if value)) or "none listed"
        raise RuntimeError(f"Codex model `{model}` does not support effort `{effort}` (supports: {supported}).")

    print("Codex account status: Logged in using ChatGPT", flush=True)
    return codex


def _render(prepared_pdf: Path, target: Path, dpi: int) -> list[Path]:
    if not 72 <= dpi <= 400:
        raise ValueError("PIPELINE_CODEX_RENDER_DPI must be between 72 and 400.")
    target.mkdir(parents=True, exist_ok=True)
    paths: list[Path] = []
    with pymupdf.open(prepared_pdf) as document:
        for index, page in enumerate(document):
            pixmap = page.get_pixmap(dpi=dpi, colorspace=pymupdf.csRGB, alpha=False)
            image_path = target / f"page-{index + 2:04}.png"
            pixmap.save(image_path)
            paths.append(image_path)
    return paths


def _run_pass(
    codex: str,
    name: str,
    prompt: str,
    image_paths: list[Path],
    work_dir: Path,
    *,
    model: str | None,
    effort: str,
    timeout: int,
) -> str:
    result_path = work_dir / "result.md"
    print(f"Codex CLI working: {name} extraction ({len(image_paths)} pages)", flush=True)
    command = [
        codex, "exec", "--cd", str(work_dir), "--skip-git-repo-check", "--ephemeral",
        "--sandbox", "read-only", "--config", f'model_reasoning_effort="{effort}"',
        "--output-last-message", str(result_path),
    ]
    if model:
        command.extend(("--model", model))
    command.append(prompt)
    for image in image_paths:
        command.extend(("--image", str(image)))
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, timeout=timeout, env=_codex_env()
        )
    except subprocess.TimeoutExpired as exc:
        raise RuntimeError(f"Codex {name} extraction timed out after {timeout} seconds.") from exc
    if result.returncode:
        detail = (result.stderr or result.stdout).strip().splitlines()
        message = detail[-1][:300] if detail else "no diagnostic returned"
        raise RuntimeError(f"Codex {name} extraction failed (exit {result.returncode}): {message}")
    if not result_path.is_file() or not result_path.read_text(encoding="utf-8").strip():
        raise RuntimeError(f"Codex {name} extraction returned no Markdown.")
    print(f"Codex CLI completed: {name} extraction", flush=True)
    return result_path.read_text(encoding="utf-8").strip()


def _save_markdown(path: Path, content: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    staging = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    try:
        staging.write_text(content.rstrip() + "\n", encoding="utf-8")
        os.replace(staging, path)
    except Exception:
        staging.unlink(missing_ok=True)
        raise


def extract_pdf(prepared_pdf: Path, output_path: Path, state_dir: Path) -> Path:
    """Write student and teacher OCR Markdown for a worker's prepared PDF."""
    prepared_pdf = prepared_pdf.resolve()
    state_dir = state_dir.resolve()
    output_path = output_path.resolve()
    if not prepared_pdf.is_file() or not prepared_pdf.is_relative_to(state_dir):
        raise ValueError("Codex input must be a prepared PDF inside the worker state directory.")

    effort = os.environ.get("PIPELINE_CODEX_EFFORT", DEFAULT_EFFORT)
    if effort not in VALID_EFFORTS:
        raise ValueError(f"PIPELINE_CODEX_EFFORT must be one of {', '.join(sorted(VALID_EFFORTS))}.")
    timeout = int(os.environ.get("PIPELINE_CODEX_TIMEOUT_SECONDS", "1800"))
    model = os.environ.get("PIPELINE_CODEX_MODEL", DEFAULT_MODEL)
    codex = check_codex(model, effort)
    dpi = int(os.environ.get("PIPELINE_CODEX_RENDER_DPI", "200"))

    with tempfile.TemporaryDirectory(prefix=".codex-", dir=state_dir) as temp:
        work = Path(temp)
        student_dir, grading_dir = work / "student", work / "grading"
        # Both passes receive full-color page renders from the same prepared PDF.
        student_images = _render(prepared_pdf, student_dir, dpi)
        grading_images = _render(prepared_pdf, grading_dir, dpi)
        student = _run_pass(
            codex,
            "student",
            "These full-color page images contain both student work and teacher annotations. "
            "Transcribe only student handwriting, equations, and diagrams. Identify student work by its context "
            "and ignore teacher marks, scores, checks, comments, and feedback. Preserve mistakes and incomplete work. "
            "Do not solve, correct, or guess unclear content. These are pages 2 onward of the original PDF; "
            "label headings with original page numbers. If a page has no visible student work, write "
            "[no visible student work] under its page heading.\n\n" + MARKDOWN_FORMAT,
            student_images,
            student_dir,
            model=model, effort=effort, timeout=timeout,
        )
        student_response_path = output_path.with_name("student-response.md")
        _save_markdown(student_response_path, student)
        print(
            f"Saved Codex student response: {student_response_path.relative_to(state_dir).as_posix()}",
            flush=True,
        )
        grading = _run_pass(
            codex,
            "grading",
            "Transcribe only teacher grading marks, comments, and raw score notation from these color images. "
            "They are pages 2 onward of the original PDF. Do not re-grade, solve, or infer scores. "
            "Mark unclear meaning or question association explicitly. Under each question heading use these "
            "three fields in this order: `Raw score:`, `Marks:`, `Feedback:`. Use [not visible] for an absent "
            "field. Keep raw score notation as literal text, unchanged (do not convert a score such as 2/5 "
            "into a LaTeX fraction or a computed value). Describe visible checks and crosses in words. "
            "Use LaTeX for mathematical expressions in feedback, following the output contract. "
            "If a page has no visible grading, write [no visible grading] under its page heading.\n\n"
            + MARKDOWN_FORMAT,
            grading_images,
            grading_dir,
            model=model, effort=effort, timeout=timeout,
        )
        grading_response_path = output_path.with_name("grading-response.md")
        _save_markdown(grading_response_path, grading)
        print(
            f"Saved Codex grading response: {grading_response_path.relative_to(state_dir).as_posix()}",
            flush=True,
        )

    _save_markdown(output_path, f"# OCR extraction\n\n## Student work\n\n{student}\n\n## Teacher grading\n\n{grading}")
    return output_path
