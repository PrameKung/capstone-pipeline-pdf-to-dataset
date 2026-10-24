"""Write an inspectable synthetic comparison under the ignored local work area."""

import argparse
import json
from pathlib import Path

import cv2
import numpy as np

from app.pipeline.pdf.student_view import compute_student_view
from tests.synthetic_pages import crossing_mark, separate_mark


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--output-dir", type=Path, default=Path(__file__).resolve().parents[2] / ".pipeline/previews/red-removal")
    args = parser.parse_args()
    args.output_dir.mkdir(parents=True, exist_ok=True)
    rows = []
    metrics = {}
    cases = [("bright", (20, 30, 220), False), ("dark", (15, 20, 120), False), ("faint", (200, 200, 240), False), ("shadow", (15, 20, 120), True)]
    fixtures = [(name, *separate_mark(color, shadow=shadow)) for name, color, shadow in cases]
    clean, marked = crossing_mark()
    fixtures.append(("crossings", clean, marked, np.any(clean != marked, axis=2)))
    for name, clean, marked, mark_pixels in fixtures:
        result = compute_student_view(marked)
        baseline = cv2.cvtColor(marked[:, :, 2], cv2.COLOR_GRAY2BGR)
        expected = cv2.cvtColor(clean[:, :, 2], cv2.COLOR_GRAY2BGR)
        columns = [("Clean baseline", expected), ("Graded", marked), ("Old student view", baseline), ("New student view", result.student_view), ("Uncertainty", cv2.cvtColor(result.overlap_candidates, cv2.COLOR_GRAY2BGR))]
        tiles = []
        case_dir = args.output_dir / name
        case_dir.mkdir(exist_ok=True)
        for title, data in columns:
            tile = np.full((200, 260, 3), 255, np.uint8)
            tile[35:35 + data.shape[0], 10:10 + data.shape[1]] = data
            cv2.putText(tile, f"{name}: {title}", (8, 22), cv2.FONT_HERSHEY_SIMPLEX, 0.40, (0, 0, 0), 1, cv2.LINE_AA)
            tiles.append(tile)
            if not cv2.imwrite(str(case_dir / (title.lower().replace(" ", "-") + ".png")), data):
                raise OSError("Could not write synthetic preview")
        rows.append(np.hstack(tiles))
        metrics[name] = {
            **result.counts,
            "baseline_mean_absolute_error_on_mark": float(np.abs(baseline[:, :, 2].astype(float) - expected[:, :, 2])[mark_pixels].mean()),
            "new_mean_absolute_error_on_mark": float(np.abs(result.student_view[:, :, 2].astype(float) - expected[:, :, 2])[mark_pixels].mean()),
            "changed_pixels_outside_mark": int(np.count_nonzero(np.any(result.student_view != baseline, axis=2) & ~mark_pixels)),
        }
    if not cv2.imwrite(str(args.output_dir / "comparison.png"), np.vstack(rows)):
        raise OSError("Could not write comparison")
    (args.output_dir / "metrics.json").write_text(json.dumps(metrics, indent=2) + "\n", encoding="utf-8")
    print(f"Synthetic comparison saved to {args.output_dir}")
    print(json.dumps(metrics, indent=2))


if __name__ == "__main__":
    main()
