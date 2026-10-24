"""Central defaults for subscription-authenticated OCR."""
from dataclasses import asdict, dataclass
import hashlib
import json
import os

OCR_VERSION = 1


@dataclass(frozen=True)
class OcrSettings:
    model: str = "gpt-6.1-sol"
    effort: str = "high"
    timeout_seconds: int = 900
    max_image_side: int = 3200
    capacity: int = 1

    def __post_init__(self):
        if not self.model or not self.effort:
            raise ValueError("OCR model and effort must be specified")
        if self.timeout_seconds < 1 or self.capacity < 1 or self.max_image_side < 512:
            raise ValueError("OCR timeout/capacity must be positive; image side must be >=512")

    @classmethod
    def from_env(cls):
        defaults = cls()
        return cls(
            model=os.getenv("PIPELINE_CODEX_MODEL") or defaults.model,
            effort=os.getenv("PIPELINE_CODEX_EFFORT") or defaults.effort,
            timeout_seconds=int(os.getenv("PIPELINE_CODEX_TIMEOUT_SECONDS") or defaults.timeout_seconds),
            max_image_side=int(os.getenv("PIPELINE_OCR_MAX_IMAGE_SIDE") or defaults.max_image_side),
            capacity=int(os.getenv("PIPELINE_OCR_CAPACITY") or defaults.capacity),
        )

    def signature(self) -> str:
        settings = asdict(self)
        # Concurrency changes scheduling, not OCR content. Keep the historical
        # capacity=1 field in the hash so existing single-slot checkpoints and
        # publications stay valid when capacity increases. This is a cache
        # compatibility constant, not the runtime concurrency limit.
        settings["capacity"] = 1
        return hashlib.sha256(json.dumps({"version": OCR_VERSION, **settings}, sort_keys=True).encode()).hexdigest()
