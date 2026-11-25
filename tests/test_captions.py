#!/usr/bin/env python3
"""Caption parsing and mapping checks. No media, no network."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "forge"))

import captions  # noqa: E402


SRT = """1
00:00:10,500 --> 00:00:11,000
First line

2
00:00:14,000 --> 00:00:15,000
Second line
continues here
"""


class ParseTests(unittest.TestCase):
    def test_parses_cues_and_multiline_text(self):
        cues = captions.parse_srt(SRT)
        self.assertEqual(len(cues), 2)
        self.assertEqual(cues[0]["start"], 10.5)
        self.assertEqual(cues[1]["text"], "Second line continues here")

    def test_accepts_period_milliseconds(self):
        cues = captions.parse_srt("1\n00:00:01.250 --> 00:00:02.750\nHello\n")
        self.assertEqual(cues[0]["start"], 1.25)
        self.assertEqual(cues[0]["end"], 2.75)

    def test_rejects_bad_timestamp(self):
        with self.assertRaises(ValueError):
            captions.parse_timestamp("not a time")

    def test_formats_srt_time(self):
        self.assertEqual(captions.format_timestamp(0.9), "00:00:00,900")
        self.assertEqual(captions.format_timestamp(3675.5), "01:01:15,500")


class MapTests(unittest.TestCase):
    def setUp(self):
        self.timemap = {"keep": [[0, 2], [4, 6]], "speed": 1.0, "clip_duration": 4.0}
        self.chapter = {"start": 10.0, "end": 18.0}
        self.cues = captions.parse_srt(SRT)

    def test_maps_through_a_cut_and_clamps_to_clip(self):
        doc = captions.build_caption_doc(self.cues, self.chapter, self.timemap, 1920)
        self.assertEqual(len(doc["captions"]), 2)
        self.assertEqual(doc["captions"][0]["start"], 0.9)
        self.assertEqual(doc["captions"][1]["start"], 2.0)
        self.assertEqual(doc["captions"][1]["end"], 3.0)

    def test_speed_applies_after_mapping(self):
        timemap = dict(self.timemap, speed=2.0)
        doc = captions.build_caption_doc(self.cues, self.chapter, timemap, 1920)
        self.assertEqual(doc["captions"][1]["start"], 1.0)
        self.assertEqual(doc["captions"][1]["end"], 1.5)

    def test_cues_outside_the_clip_are_dropped(self):
        cues = [{"start": 30.0, "end": 31.0, "text": "Later"}]
        doc = captions.build_caption_doc(cues, self.chapter, self.timemap, 1920)
        self.assertEqual(doc["captions"], [])

    def test_style_scales_with_height(self):
        doc = captions.build_caption_doc(self.cues, self.chapter, self.timemap, 1080)
        self.assertEqual(doc["y_bottom"], 950)
        self.assertEqual(doc["font_size"], 28)

    def test_first_cue_is_held_clear_of_the_thumbnail(self):
        cues = [{"start": 12.0, "end": 12.2, "text": "Early"}]
        doc = captions.build_caption_doc(cues, self.chapter, self.timemap, 1920)
        self.assertGreaterEqual(doc["captions"][0]["start"], 0.9)


if __name__ == "__main__":
    unittest.main()
