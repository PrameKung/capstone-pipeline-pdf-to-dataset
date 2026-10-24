"""Codex subprocess boundary: ChatGPT auth only, isolated page invocations."""
from __future__ import annotations

import json
import os
from pathlib import Path
import shutil
import signal
import subprocess
import tempfile

from app.pipeline.codex.records import RecordValidationError, prompt_for, schema_for, validate_record
from app.pipeline.codex.settings import OcrSettings


class OcrError(RuntimeError):
    """Only sanitized diagnostic codes may reach worker logs/manifests."""
    def __init__(self, code: str, message: str, *, details: dict | None = None, response: bytes | None = None):
        self.code = code
        self.details = details or {}
        # Private evidence for the orchestrator, never part of str(exc)/logs.
        self.response = response
        super().__init__(message)


def read_page_response(output: Path, kind: str, page: int) -> dict:
    """Classify failures without losing the original response during temp cleanup."""
    raw = None
    details = {}
    try:
        raw = output.read_bytes()
    except FileNotFoundError:
        details = {"reason": "response_missing"}
    except OSError as exc:
        details = {"reason": "response_unreadable", "errno": exc.errno}
    if raw is not None:
        if not raw.strip():
            details = {"reason": "response_empty"}
        else:
            try:
                record = json.loads(raw.decode("utf-8"))
                return validate_record(record, kind, page)
            except UnicodeDecodeError as exc:
                details = {"reason": "invalid_utf8", "byte_offset": exc.start}
            except json.JSONDecodeError as exc:
                # Do not serialize exc.doc or exception text containing source data.
                details = {"reason": "invalid_json", "line": exc.lineno, "column": exc.colno, "offset": exc.pos}
            except RecordValidationError as exc:
                details = {"reason": exc.reason, "field": exc.path}
            except (ValueError, RecursionError):
                details = {"reason": "json_or_structure_limit"}
    details.update(response_present=raw is not None, response_bytes=len(raw) if raw is not None else None)
    location = f" at {details['field']}" if "field" in details else ""
    raise OcrError("invalid_output", f"OCR output rejected: {details['reason']}{location}; no Markdown was published.",
                   details=details, response=raw)


def codex_environment() -> dict[str, str]:
    env = os.environ.copy()
    for key in ("OPENAI_API_KEY", "CODEX_API_KEY", "OPENAI_BASE_URL"):
        env.pop(key, None)
    source = Path(env.get("CODEX_AUTH_FILE") or str(Path.home() / ".codex/auth.json"))
    try:
        auth = json.loads(source.read_text())
        if auth.get("OPENAI_API_KEY") or auth.get("auth_mode") not in (None, "chatgpt") or not auth.get("tokens"):
            raise OcrError("authentication", "OCR requires a ChatGPT subscription login, not API-key authentication.")
    except (OSError, ValueError) as exc:
        raise OcrError("authentication", "Codex auth file is unavailable/invalid; run codex login on the host.") from exc
    home = Path(env.get("PIPELINE_CODEX_HOME", "/tmp/pipeline-codex"))
    home.mkdir(mode=0o700, parents=True, exist_ok=True)
    target = home / "auth.json"
    if not target.exists():
        fd, name = tempfile.mkstemp(prefix=".auth-", dir=home)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as handle:
                json.dump(auth, handle)
            os.chmod(name, 0o600)
            try:
                os.link(name, target)
            except FileExistsError:
                pass
        finally:
            Path(name).unlink(missing_ok=True)
    env["CODEX_HOME"] = str(home)
    return env


def _failure(text: str, returncode: int) -> OcrError:
    value = text.lower()
    if any(word in value for word in ("usage limit", "quota", "rate limit", "rate_limit", "429")):
        return OcrError("quota", "Codex subscription capacity exhausted; stop and retry later. No provider fallback was used.")
    if any(word in value for word in ("401", "unauthorized", "refresh token", "not logged", "authentication")):
        return OcrError("authentication", "Codex authentication failed; refresh the host ChatGPT login and restart the worker.")
    return OcrError("cli_failure", f"Codex extraction failed (exit {returncode}); output was not published.")


class CodexClient:
    def __init__(self, settings: OcrSettings):
        self.settings = settings
        self.executable = shutil.which("codex")
        self.env = None
        self.version = "unverified"

    def preflight(self) -> None:
        if not self.executable:
            raise OcrError("cli_missing", "Codex CLI is missing from the worker image.")
        self.env = codex_environment()
        try:
            def run(arguments):
                return subprocess.run([self.executable, *arguments], env=self.env, capture_output=True, text=True, timeout=60)
            help_result = run(["exec", "--help"])
            required = ("--image", "--output-schema", "--output-last-message", "--ephemeral", "--ignore-user-config")
            if help_result.returncode or any(flag not in help_result.stdout for flag in required):
                raise OcrError("cli_unsupported", "Codex CLI lacks required image/schema/isolated-config support.")
            login = run(["login", "status"])
            if login.returncode or "logged in using chatgpt" not in (login.stdout + login.stderr).lower():
                raise OcrError("authentication", "Codex must be logged in using ChatGPT.")
            catalog = run(["debug", "models"])
            if catalog.returncode:
                raise OcrError("model_catalog", "Could not verify the Codex account model catalog.")
            models = json.loads(catalog.stdout)["models"]
            selected = next((model for model in models if model["slug"] == self.settings.model), None)
            if selected is None:
                raise OcrError("model_unavailable", f"Requested Codex model {self.settings.model} is unavailable; no substitution was made.")
            if self.settings.effort not in {level["effort"] for level in selected.get("supported_reasoning_levels", [])}:
                raise OcrError("effort_unavailable", "Requested reasoning effort is not supported by the selected model.")
            if "image" not in selected.get("input_modalities", []):
                raise OcrError("vision_unavailable", "Selected model does not advertise image input support.")
            self.version = run(["--version"]).stdout.strip()
        except (subprocess.TimeoutExpired, json.JSONDecodeError, KeyError, TypeError) as exc:
            raise OcrError("preflight", "Codex preflight failed or timed out; no exam page was submitted.") from exc

    def extract(self, kind: str, page: int, image: Path) -> dict:
        if self.env is None:
            raise OcrError("preflight", "Run Codex preflight before extraction.")
        # Workspace contains one permitted page only, no sibling pass or raw PDF.
        with tempfile.TemporaryDirectory(prefix=f"ocr-{kind}-") as directory:
            work = Path(directory)
            local_image = work / "page.png"
            shutil.copyfile(image, local_image)
            schema = work / "schema.json"
            schema.write_text(json.dumps(schema_for(kind)), encoding="utf-8")
            output = work / "response.json"
            command = [
                self.executable, "exec", "--ignore-user-config", "--cd", str(work),
                "--skip-git-repo-check", "--ephemeral", "--sandbox", "read-only",
                "--model", self.settings.model,
                "--config", f'model_reasoning_effort="{self.settings.effort}"',
                "--config", 'model_provider="openai"',
                "--config", 'web_search="disabled"',
                "--disable", "shell_tool", "--disable", "unified_exec",
                "--disable", "apps", "--disable", "browser_use",
                "--disable", "computer_use", "--disable", "view_image",
                "--disable", "multi_agent", "--disable", "skill_search",
                "--output-schema", str(schema), "--output-last-message", str(output),
                "--image", str(local_image), "-",
            ]
            with subprocess.Popen(command, cwd=work, env=self.env, stdin=subprocess.PIPE,
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  start_new_session=os.name != "nt") as process:
                try:
                    stdout, stderr = process.communicate(prompt_for(kind, page), timeout=self.settings.timeout_seconds)
                except subprocess.TimeoutExpired as exc:
                    if os.name != "nt":
                        os.killpg(process.pid, signal.SIGKILL)
                    else:
                        process.kill()
                    process.communicate()
                    raise OcrError("timeout", "Codex page extraction timed out; successful checkpoints remain reusable.") from exc
                if process.returncode:
                    raise _failure(stderr + stdout, process.returncode)
            return read_page_response(output, kind, page)


if __name__ == "__main__":
    client = CodexClient(OcrSettings.from_env())
    client.preflight()
    print(f"Codex preflight OK: ChatGPT, {client.settings.model}, {client.settings.effort}, {client.version}")
