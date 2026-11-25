#!/usr/bin/env python3
"""Profile, time and clip list checks. No media, no network."""
from __future__ import annotations

import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "forge"))

import config  # noqa: E402


class TimeTests(unittest.TestCase):
    def test_seconds_and_clock_forms(self):
        self.assertEqual(config.parse_time("75"), 75.0)
        self.assertEqual(config.parse_time("01:15"), 75.0)
        self.assertEqual(config.parse_time("1:01:15"), 3675.0)

    def test_bad_time_rejected(self):
        with self.assertRaises(config.ConfigError):
            config.parse_time("one")
        with self.assertRaises(config.ConfigError):
            config.parse_time("1:2:3:4")


class SlugTests(unittest.TestCase):
    def test_slug_is_lowercase_and_safe(self):
        self.assertEqual(config.slugify("The Turning Point!"), "the-turning-point")
        self.assertEqual(config.slugify("***"), "clip")


class ProfileTests(unittest.TestCase):
    def test_template_profile_loads(self):
        path, profile, style = config.load_profile("_template")
        self.assertTrue((path / "clips.csv").is_file())
        self.assertIn("output", profile)
        self.assertIn("thumb_first", style)

    def test_template_clips_parse(self):
        clips = config.read_clips(config.PROFILES / "_template" / "clips.csv")
        self.assertGreaterEqual(len(clips), 2)
        self.assertTrue(all(clip["end"] > clip["start"] for clip in clips))

    def test_unknown_profile_is_rejected(self):
        with self.assertRaises(config.ConfigError):
            config.load_profile("does-not-exist")


class ClipValidationTests(unittest.TestCase):
    def write(self, text):
        handle = tempfile.NamedTemporaryFile("w", suffix=".csv", delete=False)
        handle.write(text)
        handle.close()
        self.addCleanup(Path(handle.name).unlink)
        return Path(handle.name)

    def test_overlap_is_rejected(self):
        path = self.write(
            "id,start,end,title\n"
            "c1,0,10,First\n"
            "c2,5,12,Second\n"
        )
        with self.assertRaises(config.ConfigError):
            config.read_clips(path)

    def test_reverse_range_is_rejected(self):
        path = self.write("id,start,end,title\nc1,10,5,Backwards\n")
        with self.assertRaises(config.ConfigError):
            config.read_clips(path)

    def test_missing_title_is_rejected(self):
        path = self.write("id,start,end,title\nc1,0,5,\n")
        with self.assertRaises(config.ConfigError):
            config.read_clips(path)

    def test_bad_aspect_is_rejected(self):
        path = self.write("id,start,end,title,aspect\nc1,0,5,One,16:9\n")
        with self.assertRaises(config.ConfigError):
            config.read_clips(path)


if __name__ == "__main__":
    unittest.main()
