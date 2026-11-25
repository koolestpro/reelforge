import sys
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import map_captions


class MapTimeTests(unittest.TestCase):
    def test_maps_inside_a_kept_segment(self):
        keep = [[0.0, 2.755], [3.845, 8.855]]
        self.assertAlmostEqual(map_captions.map_time(3.92, keep), 2.83, places=3)

    def test_maps_a_trimmed_gap_to_the_next_segment(self):
        keep = [[0.0, 2.755], [3.845, 8.855]]
        self.assertAlmostEqual(map_captions.map_time(3.2, keep), 2.755, places=3)

    def test_maps_past_the_last_segment_to_the_end(self):
        keep = [[0.0, 2.755], [3.845, 8.855]]
        self.assertAlmostEqual(map_captions.map_time(12.0, keep), 7.765, places=3)


class MapSpecTests(unittest.TestCase):
    def test_first_caption_is_held_clear_of_the_thumbnail(self):
        spec = {
            "chapter_start": 102.6,
            "captions": [
                {"source_start": 102.6, "source_end": 103.0, "text": "One.", "flag": ""},
            ],
        }
        timemap = {"keep": [[0.0, 3.5]], "speed": 1.0}
        result = map_captions.map_spec(spec, timemap)
        cap = result["captions"][0]
        self.assertEqual(cap["start"], 0.9)
        self.assertGreaterEqual(cap["end"], 0.9)
        self.assertLessEqual(cap["end"], 3.5)


if __name__ == "__main__":
    unittest.main()
