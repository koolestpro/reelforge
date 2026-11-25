import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path
from unittest import mock

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import intake


META = {
    "source": "/tmp/source.mp4",
    "size_bytes": 123,
    "duration": 1929.21,
    "format": "mov,mp4",
    "video": {"codec": "h264", "width": 1920, "height": 1080, "fps": 30.0, "pix_fmt": "yuv420p"},
    "audio": {"codec": "aac", "sample_rate": 48000, "channels": 2, "bit_rate": 128000},
}


class IntakeTests(unittest.TestCase):
    def test_next_action_follows_artifacts(self):
        self.assertIn("Stage 2", intake.proposed_next_action({}))
        self.assertIn("Stage 3", intake.proposed_next_action({"raw transcript": ["x"]}))
        self.assertIn("Stage 4", intake.proposed_next_action({
            "raw transcript": ["x"], "corrected Yoruba transcript": ["x"]}))
        self.assertIn("Stage 5", intake.proposed_next_action({
            "raw transcript": ["x"], "corrected Yoruba transcript": ["x"],
            "chapter plan": ["x"], "structured cut plan": ["x"]}))
        self.assertIn("Stage 6", intake.proposed_next_action({
            "raw transcript": ["x"], "corrected Yoruba transcript": ["x"],
            "chapter plan": ["x"], "structured cut plan": ["x"],
            "cut plan review": ["x"], "approval log": ["x"]}))

    def test_find_artifacts(self):
        with tempfile.TemporaryDirectory() as temp:
            library = Path(temp)
            (library / "transcript.raw.elevenlabs.json").write_text("{}")
            (library / "chapters.md").write_text("# plan")
            found = intake.find_artifacts(library)
        self.assertIn("raw transcript", found)
        self.assertIn("chapter plan", found)

    def test_report_has_gate(self):
        with tempfile.TemporaryDirectory() as temp:
            library = Path(temp)
            text = intake.report_text(META, library, {}, "Stage 2: transcribe")
        self.assertIn("1929.21", text)
        self.assertIn("Stage 2", text)
        self.assertIn("Stop here", text)

    def test_main_writes_and_refuses_overwrite(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            library = root / "library"
            library.mkdir()
            source = root / "source.mp4"
            source.write_bytes(b"not really a video")
            report = library / "intake.md"
            with mock.patch.object(intake, "probe", return_value=META):
                with redirect_stdout(io.StringIO()):
                    code = intake.main([str(source), "--library", str(library)])
                    self.assertEqual(code, 0)
                    self.assertTrue(report.is_file())
                    code = intake.main([str(source), "--library", str(library)])
                    self.assertEqual(code, 2)

    def test_main_rejects_missing_library(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp4"
            source.write_bytes(b"x")
            with redirect_stdout(io.StringIO()):
                code = intake.main([str(source), "--library", str(root / "missing")])
        self.assertEqual(code, 2)


if __name__ == "__main__":
    unittest.main()
