import json
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import split_for_asr as split


class SilenceTests(unittest.TestCase):
    def test_parse_silences(self):
        text = (
            "[silencedetect @ 0x1] silence_start: 1640.2\n"
            "[silencedetect @ 0x1] silence_end: 1652.8 | silence_duration: 12.6\n"
            "[silencedetect @ 0x1] silence_start: 1900.0\n"
        )
        out = split.parse_silences(text, duration=1929.2)
        self.assertEqual(len(out), 2)
        self.assertEqual(out[0], {"start": 1640.2, "end": 1652.8, "duration": 12.6})
        self.assertEqual(out[1]["end"], 1929.2)

    def test_choose_split_keeps_parts_under_limit(self):
        silences = [
            {"start": 590.0, "end": 593.0, "duration": 3.0},
            {"start": 1635.0, "end": 1647.0, "duration": 12.0},
            {"start": 1710.0, "end": 1713.0, "duration": 3.0},
        ]
        splits = split.choose_splits(1929.2, silences, max_part=1740, min_pause=2.5)
        self.assertEqual(len(splits), 1)
        self.assertAlmostEqual(splits[0]["at"], 1641.0)
        bounds = [0.0, splits[0]["at"], 1929.2]
        self.assertTrue(all(b - a <= 1740 for a, b in zip(bounds, bounds[1:])))

    def test_choose_multiple_splits(self):
        silences = [
            {"start": 1495.0, "end": 1505.0, "duration": 10.0},
            {"start": 2995.0, "end": 3005.0, "duration": 10.0},
            {"start": 3300.0, "end": 3303.0, "duration": 3.0},
        ]
        splits = split.choose_splits(3600.0, silences, max_part=1740, min_pause=2.5)
        self.assertEqual([round(item["at"], 1) for item in splits], [1500.0, 3000.0])
        bounds = [0.0, 1500.0, 3000.0, 3600.0]
        self.assertTrue(all(b - a <= 1740 for a, b in zip(bounds, bounds[1:])))

    def test_no_pause_fails_honestly(self):
        with self.assertRaises(split.SplitError):
            split.choose_splits(1929.2, [], max_part=1740, min_pause=2.5)

    def test_split_at_must_be_in_a_pause(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.mp4"
            source.write_bytes(b"x")
            with mock.patch.object(split, "duration_of", return_value=1929.2), \
                    mock.patch.object(split, "detect_silences", return_value=[
                        {"start": 1640.0, "end": 1650.0, "duration": 10.0}]):
                with self.assertRaises(split.SplitError):
                    split.plan_parts(source, Path(temp) / "split", split_at=1000.0)

    def test_plan_parts_writes_reusable_plan(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.mp4"
            source.write_bytes(b"x")
            out = Path(temp) / "split"
            silences = [{"start": 1640.0, "end": 1652.0, "duration": 12.0}]
            with mock.patch.object(split, "duration_of", return_value=1929.2), \
                    mock.patch.object(split, "detect_silences", return_value=silences):
                plan, path = split.plan_parts(source, out)
                self.assertTrue(path.is_file())
                self.assertEqual(len(plan["parts"]), 2)
                self.assertLess(plan["parts"][0]["duration"], 1740)
                self.assertLess(plan["parts"][1]["duration"], 1740)
                reused, reused_path = split.plan_parts(source, out)
                self.assertEqual(reused["splits"], plan["splits"])
                self.assertEqual(reused_path, path)

    def test_merge_parts_shifts_timeline(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan = {
                "source": str(root / "source.mp4"),
                "duration": 20.0,
                "splits": [{"at": 10.0, "pause": {"start": 9.0, "end": 11.0, "duration": 2.0}}],
                "parts": [
                    {"index": 1, "start": 0.0, "end": 10.0, "duration": 10.0,
                     "audio": str(root / "part-01.mp3")},
                    {"index": 2, "start": 10.0, "end": 20.0, "duration": 10.0,
                     "audio": str(root / "part-02.mp3")},
                ],
            }
            part1 = root / "part-01.raw.json"
            part2 = root / "part-02.raw.json"
            part1.write_text(json.dumps({
                "engine": "elevenlabs", "engines_used": ["elevenlabs"], "timing": "word",
                "segments": [{"start": 1.0, "end": 2.0, "text": "one",
                              "words": [{"text": "one", "start": 1.0, "end": 2.0}]}],
                "text": "one", "tier_report": [], "failed_chunks": []}))
            part2.write_text(json.dumps({
                "engine": "elevenlabs", "engines_used": ["elevenlabs"], "timing": "word",
                "segments": [{"start": 1.0, "end": 2.0, "text": "two",
                              "words": [{"text": "two", "start": 1.0, "end": 2.0}]}],
                "text": "two", "tier_report": [{"chunk": 0, "ok": True}],
                "failed_chunks": [{"chunk": 1, "offset": 0.0, "duration": 10.0}]}))
            parts = [
                {"index": 1, "start": 0.0, "end": 10.0, "audio": plan["parts"][0]["audio"],
                 "raw": part1, "txt": root / "part-01.txt", "chunks": root / "p1"},
                {"index": 2, "start": 10.0, "end": 20.0, "audio": plan["parts"][1]["audio"],
                 "raw": part2, "txt": root / "part-02.txt", "chunks": root / "p2"},
            ]
            chapter_map = root / "chapters.json"
            chapter_map.write_text(json.dumps({"chapters": [
                {"id": "c1", "start": 0.0, "end": 10.0},
                {"id": "c2", "start": 10.0, "end": 20.0},
            ]}), encoding="utf-8")
            result, out, txt = split.merge_parts(
                plan, parts, root / "combined.json", root / "split.json",
                review_path=root / "review.csv", chapter_map=chapter_map)
            self.assertTrue(out.is_file())
            self.assertTrue(txt.is_file())
            self.assertEqual([item["start"] for item in result["segments"]], [1.0, 11.0])
            self.assertEqual(result["segments"][1]["words"][0]["start"], 11.0)
            self.assertEqual(result["failed_chunks"][0]["part"], 2)
            self.assertEqual(result["failed_chunks"][0]["offset"], 10.0)
            self.assertEqual(result["tier_report"][0]["offset"], 10.0)
            self.assertEqual(result["timing"], "word")
            self.assertEqual(result["timing_parts"], ["word"])
            self.assertEqual(result["engine"], "elevenlabs")
            review = (root / "review.csv").read_text(encoding="utf-8")
            self.assertIn(",c1,", review)
            self.assertIn(",c2,", review)


if __name__ == "__main__":
    unittest.main()
