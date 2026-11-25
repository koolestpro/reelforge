import csv
import sys
import tempfile
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import wer_review


def write_csv(path, rows):
    with open(path, "w", newline="", encoding="utf-8") as fh:
        w = csv.DictWriter(fh, fieldnames=["line", "start", "end", "chapter", "engine",
                                           "text", "correction"])
        w.writeheader()
        w.writerows(rows)


class WerReviewTests(unittest.TestCase):
    def test_counts_identical(self):
        e, r, ce, cr = wer_review.counts("Ẹ n lẹ", "Ẹ n lẹ")
        self.assertEqual((e, r, ce, cr), (0, 3, 0, 4))

    def test_blank_corrections_are_skipped(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "review.csv"
            write_csv(path, [
                {"line": 1, "start": 0, "end": 2, "chapter": "30c1", "engine": "elevenlabs",
                 "text": "Ẹ n lẹ", "correction": ""},
                {"line": 2, "start": 2, "end": 4, "chapter": "30c1", "engine": "elevenlabs",
                 "text": "Ẹ ṣé", "correction": "Ẹ ṣé"},
            ])
            rows = wer_review.collect(path)
        self.assertEqual(len(rows), 1)
        self.assertEqual(rows[0]["wer"], 0.0)

    def test_overall_wer_aggregates_edits(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "review.csv"
            write_csv(path, [
                {"line": 1, "start": 0, "end": 2, "chapter": "30c1", "engine": "elevenlabs",
                 "text": "Ẹ n lẹ", "correction": "Ẹ n lẹ"},
                {"line": 2, "start": 2, "end": 4, "chapter": "30c1", "engine": "elevenlabs",
                 "text": "Ẹ n lọ", "correction": "Ẹ n lẹ"},
            ])
            rows = wer_review.collect(path)
            groups = wer_review.summarize(rows)
        self.assertAlmostEqual(groups[("all", "")]["wer"], 1 / 6, places=4)
        self.assertAlmostEqual(groups[("chapter", "30c1")]["wer"], 1 / 6, places=4)

    def test_report_without_review_says_so(self):
        text = wer_review.report([], {}, "x.csv")
        self.assertIn("No reviewed rows", text)


if __name__ == "__main__":
    unittest.main()
