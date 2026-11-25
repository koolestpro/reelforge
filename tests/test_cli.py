#!/usr/bin/env python3
"""Aspect and output naming checks. No media, no network."""
from __future__ import annotations

import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT / "forge"))

import config  # noqa: E402


class AspectTests(unittest.TestCase):
    def test_explicit_aspect_wins(self):
        clips = [{"aspect": "4:5"}]
        self.assertEqual(config.resolve_aspect({"output": {"aspect": "9:16"}}, clips, None), "4:5")

    def test_mixed_aspects_are_rejected(self):
        clips = [{"aspect": "4:5"}, {"aspect": "1:1"}]
        with self.assertRaises(config.ConfigError):
            config.resolve_aspect({"output": {"aspect": "9:16"}}, clips, None)

    def test_profile_aspect_is_used_when_clips_say_source(self):
        clips = [{"aspect": "source"}]
        self.assertEqual(config.resolve_aspect({"output": {"aspect": "9:16"}}, clips, None), "9:16")

    def test_bad_profile_aspect_is_rejected(self):
        clips = [{"aspect": "source"}]
        with self.assertRaises(config.ConfigError):
            config.resolve_aspect({"output": {"aspect": "16:9"}}, clips, None)


if __name__ == "__main__":
    unittest.main()
