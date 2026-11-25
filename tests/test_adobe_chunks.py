import json
import sys
import tempfile
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import adobe_chunks


class ChunkPointTests(unittest.TestCase):
    def test_three_chunks_cut_at_nearby_pauses(self):
        silences = [
            {"start": 440.0, "end": 443.0, "duration": 3.0},
            {"start": 895.0, "end": 899.0, "duration": 4.0},
        ]
        splits = adobe_chunks.choose_chunk_points(1352.0, silences, 3)
        self.assertEqual([item["at"] for item in splits], [441.5, 897.0])

    def test_longest_pause_wins(self):
        silences = [
            {"start": 445.0, "end": 446.5, "duration": 1.5},
            {"start": 452.0, "end": 456.0, "duration": 4.0},
            {"start": 895.0, "end": 899.0, "duration": 4.0},
        ]
        splits = adobe_chunks.choose_chunk_points(1352.0, silences, 3)
        self.assertEqual(splits[0]["at"], 454.0)

    def test_missing_pause_fails_honestly(self):
        with self.assertRaises(adobe_chunks.ChunkError):
            adobe_chunks.choose_chunk_points(1352.0, [], 3)

    def test_same_pause_is_not_reused(self):
        silences = [{"start": 670.0, "end": 676.0, "duration": 6.0}]
        with self.assertRaises(adobe_chunks.ChunkError):
            adobe_chunks.choose_chunk_points(1352.0, silences, 3, window=400.0)

    def test_plan_bounds_and_names(self):
        splits = [
            {"at": 400.0, "target": 450.0, "pause": {"start": 399.0, "end": 401.0,
                                                    "duration": 2.0}},
            {"at": 800.0, "target": 900.0, "pause": {"start": 799.0, "end": 801.0,
                                                    "duration": 2.0}},
        ]
        items = adobe_chunks.plan_chunks(1200.0, splits, 3)
        self.assertEqual([item["duration"] for item in items], [400.0, 400.0, 400.0])
        self.assertEqual([adobe_chunks.chunk_name(item) for item in items],
                         ["chunk_01_0000-0400.wav", "chunk_02_0400-0800.wav",
                          "chunk_03_0800-1200.wav"])

    def test_short_chunk_is_rejected(self):
        splits = [{"at": 20.0, "target": 600.0, "pause": {"start": 19.0, "end": 21.0,
                                                         "duration": 2.0}}]
        with self.assertRaises(adobe_chunks.ChunkError):
            adobe_chunks.plan_chunks(1200.0, splits, 2)


class ManifestTests(unittest.TestCase):
    def test_manifest_is_a_list_for_audio_join(self):
        with tempfile.TemporaryDirectory() as temp:
            out = Path(temp)
            items = [
                {"index": 1, "start": 0.0, "end": 400.0, "duration": 400.0,
                 "file": "chunk_01_0000-0400.wav"},
                {"index": 2, "start": 400.0, "end": 1200.0, "duration": 800.0,
                 "file": "chunk_02_0400-1200.wav"},
            ]
            adobe_chunks.write_outputs(
                out, out / "master.mp4", [out / "part1.mp4", out / "part2.mp4"],
                1200.0, 2, 1.5, "-30dB", items, [])
            manifest = json.loads((out / "audio_chunks.json").read_text())
            self.assertIsInstance(manifest, list)
            self.assertEqual([item["file"] for item in manifest],
                             ["chunk_01_0000-0400.wav", "chunk_02_0400-1200.wav"])
            self.assertTrue((out / "chunks_plan.json").is_file())
            self.assertTrue((out / "README.md").is_file())


if __name__ == "__main__":
    unittest.main()
