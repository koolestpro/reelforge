import sys
import tempfile
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import audio_join


class AudioJoinTests(unittest.TestCase):
    def test_adobe_suffix_is_resolved(self):
        with tempfile.TemporaryDirectory() as temp:
            enhanced = Path(temp)
            (enhanced / "chunk_01_0000-0608-enhanced-v2.wav").write_bytes(b"x")
            found = audio_join.find_enhanced(enhanced, "chunk_01_0000-0608.wav", "-enhanced-v2")
            self.assertEqual(found.name, "chunk_01_0000-0608-enhanced-v2.wav")

    def test_exact_name_wins(self):
        with tempfile.TemporaryDirectory() as temp:
            enhanced = Path(temp)
            (enhanced / "chunk_01.wav").write_bytes(b"x")
            (enhanced / "chunk_01-enhanced-v2.wav").write_bytes(b"x")
            found = audio_join.find_enhanced(enhanced, "chunk_01.wav", "-enhanced-v2")
            self.assertEqual(found.name, "chunk_01.wav")

    def test_missing_chunk_is_reported(self):
        with tempfile.TemporaryDirectory() as temp:
            enhanced = Path(temp)
            with self.assertRaisesRegex(SystemExit, "missing enhanced chunk"):
                audio_join.find_enhanced(enhanced, "chunk_01.wav", "-enhanced-v2")


if __name__ == "__main__":
    unittest.main()
