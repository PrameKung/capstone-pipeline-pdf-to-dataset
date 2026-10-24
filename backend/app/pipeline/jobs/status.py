"""Show durable per-source progress without printing answers or credentials."""
import json
import os
from pathlib import Path

from app.pipeline.jobs.state import read_json


def status(state: Path) -> dict:
    latest = {}
    for path in (state / "runs").rglob("*.json"):
        record = read_json(path)
        source = record.get("source_relative_path")
        if source and record.get("started_at", "") >= latest.get(source, {}).get("started_at", ""):
            latest[source] = record
    fields = ("source_relative_path", "run_id", "status", "stage", "source_page",
              "completed_page_passes", "total_page_passes", "error_code", "error_message",
              "error_details", "failure_evidence", "failure_evidence_error",
              "started_at", "updated_at", "output_location")
    return {"paused": read_json(state / "ocr-paused.json") or None,
            "jobs": [{key: value for key, value in record.items() if key in fields}
                     for _, record in sorted(latest.items())]}


if __name__ == "__main__":
    print(json.dumps(status(Path(os.environ.get("PIPELINE_DATA_DIR", "../.pipeline"))), indent=2))
