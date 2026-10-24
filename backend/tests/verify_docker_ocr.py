"""Opt-in synthetic smoke check using two Docker containers and the host state mount.

Run prepare, two concurrent worker commands, then verify with the same --root.
No model requests or private source files are used.
"""
import argparse
import json
from pathlib import Path
import time

from app.pipeline.codex.settings import OcrSettings
from app.pipeline.jobs.state import checksum, read_json
from app.pipeline.jobs.worker import run_worker
from tests.synthetic_pages import write_pdf
from tests.test_ocr_pipeline import record_for


class AuditClient:
    version = "synthetic-docker-smoke"

    def __init__(self, root):
        self.root = root

    def extract(self, kind, page, image):
        # This is called while holding the real cross-container OCR slot.
        key = image.relative_to(self.root).as_posix()
        with (self.root / "events.jsonl").open("a") as handle:
            handle.write(json.dumps({"event": "start", "key": key, "kind": kind, "page": page}) + "\n")
        time.sleep(0.1)
        with (self.root / "events.jsonl").open("a") as handle:
            handle.write(json.dumps({"event": "end", "key": key}) + "\n")
        return record_for(kind, page)


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("phase", choices=("prepare", "worker", "verify"))
    parser.add_argument("--root", type=Path, required=True)
    parser.add_argument("--capacity", type=int, default=1)
    parser.add_argument("--workers", type=int, default=2)
    args = parser.parse_args()
    root = args.root.resolve()
    if args.phase == "prepare":
        if root.exists():
            raise ValueError("Choose a new empty smoke-test directory")
        hashes = {}
        for index in range(8):
            pdf = root / "raw" / f"group-{index % 2}" / f"{index}.pdf"
            write_pdf(pdf)
            hashes[pdf.relative_to(root).as_posix()] = checksum(pdf)
        (root / "hashes.json").write_text(json.dumps(hashes))
        print("Prepared 8 synthetic PDFs")
    elif args.phase == "worker":
        raise SystemExit(bool(run_worker(root / "raw", root / "state", args.workers, 0.01, True,
                                        ocr_settings=OcrSettings(max_image_side=512, capacity=args.capacity), client=AuditClient(root))))
    else:
        hashes = read_json(root / "hashes.json")
        assert all(checksum(root / path) == value for path, value in hashes.items())
        latest = list((root / "state/ocr").rglob("latest.json"))
        manifests = [read_json(path) for path in (root / "state/runs").rglob("*.json")]
        assert len(latest) == len(manifests) == 8
        assert all(record["status"] == "completed" for record in manifests)
        assert not list((root / "state").rglob("page-001*"))
        active = 0
        peak = 0
        calls = []
        for line in (root / "events.jsonl").read_text().splitlines():
            event = json.loads(line)
            active += 1 if event["event"] == "start" else -1
            peak = max(peak, active)
            assert 0 <= active <= args.capacity, "Global OCR capacity exceeded"
            if event["event"] == "start":
                assert event["page"] == 2
                calls.append(event["key"])
        assert active == 0 and len(calls) == len(set(calls)) == 16
        assert peak == args.capacity, "Requested parallelism was not exercised"
        for pointer in latest:
            value = read_json(pointer)
            directory = pointer.parent / "runs" / value["run_id"]
            assert all(checksum(directory / name) == expected for name, expected in value["outputs"].items())
        print(json.dumps({"documents": 8, "validated_page_passes": 16, "duplicate_calls": 0,
                          "max_concurrent_ocr": peak, "unchanged_sources": True, "identity_excluded": True}))


if __name__ == "__main__":
    main()
