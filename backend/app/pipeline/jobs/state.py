"""Local atomic records and crash-released locks shared by Docker workers."""
from contextlib import contextmanager
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import tempfile
import time


def now() -> str:
    return datetime.now(timezone.utc).isoformat()


def checksum(path: Path) -> str:
    with path.open("rb") as handle:
        return hashlib.file_digest(handle, "sha256").hexdigest()


def atomic_text(path: Path, text: str) -> None:
    atomic_bytes(path, text.encode("utf-8"))


def atomic_bytes(path: Path, data: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=".write-", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_json(path: Path, value: dict) -> None:
    atomic_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def read_json(path: Path) -> dict:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
        return value if isinstance(value, dict) else {}
    except (OSError, ValueError):
        return {}


@contextmanager
def claim(path: Path):
    # flock is released by the kernel even on worker crash. Never unlink the
    # guard inode: another process might already be waiting on that inode.
    if os.name != "posix":
        raise RuntimeError("Worker coordination requires Linux; use Docker Compose.")
    import fcntl
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a+b") as handle:
        try:
            fcntl.flock(handle, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            yield False
            return
        try:
            yield True
        finally:
            fcntl.flock(handle, fcntl.LOCK_UN)


@contextmanager
def ocr_slot(state: Path, capacity: int):
    # All replicas must use the same capacity. Slots are shared local flock
    # files, not stale timestamp leases that could double-submit an OCR call.
    while True:
        for index in range(capacity):
            with claim(state / "ocr-slots" / f"{index}.guard") as acquired:
                if acquired:
                    yield
                    return
        time.sleep(0.25)
