import json
import sys
import tempfile
import unittest
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import capcut_build as cb


def make_spec(root: Path):
    source = root / "master.mp4"
    source.write_bytes(b"not a real video")
    overlay = root / "overlay.png"
    overlay.write_bytes(b"not a real png")
    return {
        "name": "test_cut_1x1",
        "canvas": {"width": 1080, "height": 1080, "ratio": "1:1"},
        "fps": 30,
        "source": str(source),
        "source_duration": 100.0,
        "source_size": [1920, 1080],
        "fill": 1.7778,
        "segments": [
            {"in": 10.0, "out": 20.5, "note": "first"},
            {"in": 25.0, "out": 30.0, "note": "second"},
        ],
        "overlays": [{"path": str(overlay), "start": 0, "end": None, "name": "brand"}],
        "texts": [{"text": "Title\nSubtitle", "start": 0.2, "end": 6.0, "y": 0.22, "size": 9}],
    }


class BuildTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.root = Path(self.tmp.name)
        self.spec = make_spec(self.root)

    def tearDown(self):
        self.tmp.cleanup()

    def test_each_cut_is_its_own_segment(self):
        draft, report = cb.build(self.spec)
        main = draft["tracks"][0]
        self.assertEqual(main["type"], "video")
        self.assertEqual(len(main["segments"]), 2)
        self.assertEqual(report["segments"], 2)
        self.assertAlmostEqual(report["duration"], 15.5, places=3)

        first, second = main["segments"]
        self.assertEqual(first["source_timerange"], {"start": 10_000_000, "duration": 10_500_000})
        self.assertEqual(second["source_timerange"], {"start": 25_000_000, "duration": 5_000_000})
        self.assertEqual(second["target_timerange"]["start"], 10_500_000)
        self.assertEqual(draft["duration"], 15_500_000)

    def test_fill_overlay_and_text_placement(self):
        draft, _ = cb.build(self.spec)
        main_segment = draft["tracks"][0]["segments"][0]
        self.assertAlmostEqual(main_segment["clip"]["scale"]["x"], 1.7778, places=4)

        brand = draft["tracks"][1]
        self.assertEqual(brand["name"], "brand")
        self.assertEqual(brand["segments"][0]["target_timerange"]["duration"], 15_500_000)

        titles = draft["tracks"][-1]
        self.assertEqual(titles["type"], "text")
        segment = titles["segments"][0]
        self.assertAlmostEqual(segment["clip"]["transform"]["y"], 0.22, places=4)
        self.assertEqual(segment["target_timerange"], {"start": 200_000, "duration": 5_800_000})
        content = json.loads(draft["materials"]["texts"][0]["content"])
        self.assertEqual(content["text"], "Title\nSubtitle")

    def test_backwards_segment_is_rejected(self):
        self.spec["segments"] = [{"in": 20.0, "out": 10.0}]
        with self.assertRaises(cb.BuildError):
            cb.build(self.spec)

    def test_segment_past_the_source_is_rejected(self):
        self.spec["segments"] = [{"in": 90.0, "out": 101.0}]
        with self.assertRaises(cb.BuildError):
            cb.build(self.spec)

    def test_write_project_creates_draft_info(self):
        draft, report = cb.build(self.spec)
        project = cb.write_project(draft, report, self.root)
        written = json.loads((project / "draft_info.json").read_text(encoding="utf-8"))
        self.assertEqual(written["name"], "test_cut_1x1")
        self.assertEqual(len(written["tracks"][0]["segments"]), 2)

    def test_one_text_material_with_two_runs(self):
        self.spec["texts"] = [{
            "runs": [
                {"text": "Interview", "size": 10.13, "color": [1, 1, 1]},
                {"text": "\nENGLISH", "size": 5.32, "color": [0.94, 0.76, 0.34]},
            ],
            "x": 0.0, "y": 0.27, "align": 0, "start": 0.2,
        }]
        draft, _ = cb.build(self.spec)
        self.assertEqual(len(draft["materials"]["texts"]), 1)
        self.assertEqual(len(draft["tracks"][-1]["segments"]), 1)
        content = json.loads(draft["materials"]["texts"][0]["content"])
        self.assertEqual(content["text"], "Interview\nENGLISH")
        self.assertEqual(len(content["styles"]), 2)
        self.assertEqual(content["styles"][1]["range"], [9, 16])
        self.assertEqual(draft["materials"]["texts"][0]["alignment"], 0)


if __name__ == "__main__":
    unittest.main()
