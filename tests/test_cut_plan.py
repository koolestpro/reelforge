import copy
import io
import sys
import tempfile
import unittest
from contextlib import redirect_stdout
from pathlib import Path

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import cut_plan


def transcript(words=True, timing="word"):
    if words:
        return {
            "engine": "test",
            "timing": timing,
            "duration": 10.0,
            "segments": [
                {"start": 0.0, "end": 0.9, "text": "Ẹ n lẹ.",
                 "words": [
                     {"text": "Ẹ", "start": 0.0, "end": 0.4},
                     {"text": "n", "start": 0.4, "end": 0.6},
                     {"text": "lẹ.", "start": 0.6, "end": 0.9},
                 ]},
                {"start": 2.0, "end": 2.6, "text": "Ẹ ṣé.",
                 "words": [
                     {"text": "Ẹ", "start": 2.0, "end": 2.3},
                     {"text": "ṣé.", "start": 2.3, "end": 2.6},
                 ]},
                {"start": 3.4, "end": 3.8, "text": "Àmín.",
                 "words": [{"text": "Àmín.", "start": 3.4, "end": 3.8}]},
                {"start": 6.2, "end": 6.5, "text": "Wọ́n dára.",
                 "words": [{"text": "Wọ́n", "start": 6.2, "end": 6.4},
                           {"text": "dára.", "start": 6.4, "end": 6.5}]},
                {"start": 13.8, "end": 14.0, "text": "Ẹ̀.",
                 "words": [{"text": "Ẹ̀.", "start": 13.8, "end": 14.0}]},
            ],
        }
    return {
        "engine": "test",
        "timing": timing,
        "duration": 10.0,
        "segments": [
            {"start": 2.0, "end": 2.6, "text": "Ẹ ṣé."},
            {"start": 3.4, "end": 3.8, "text": "Àmín."},
        ],
    }


def plan(start=0.9, end=6.5, **extra):
    chapter = {"id": "c1", "start": start, "end": end, "yo": "Test", "en": "Test"}
    chapter.update(extra)
    return {"source": "source.mp4", "duration": 10.0, "chapters": [chapter]}


PAUSES = [(0.9, 2.0), (2.6, 3.4), (6.5, 7.2), (14.0, 15.0)]


class CutPlanTests(unittest.TestCase):
    def test_word_boundary_is_exact(self):
        report = cut_plan.verify_plan(plan(), transcript(), PAUSES, 10.0)
        chapter = report["chapters"][0]
        self.assertTrue(report["ok"], report["errors"])
        self.assertEqual(chapter["start_evidence"]["confidence"], "exact")
        self.assertEqual(chapter["end_evidence"]["confidence"], "exact")
        self.assertEqual(chapter["opening_complete"], "likely")
        self.assertEqual(chapter["closing_complete"], "likely")
        self.assertFalse(chapter["review_required"])

    def test_boundary_inside_word_needs_review(self):
        report = cut_plan.verify_plan(plan(start=2.15), transcript(), PAUSES, 10.0)
        chapter = report["chapters"][0]
        self.assertTrue(chapter["start_evidence"]["inside_word"])
        self.assertEqual(chapter["boundary_confidence"], "needs_review")
        self.assertTrue(chapter["review_required"])
        self.assertTrue(any("inside the word" in item for item in report["warnings"]))

    def test_estimated_timing_is_never_exact(self):
        report = cut_plan.verify_plan(plan(), transcript(words=False, timing="estimated"), PAUSES, 10.0)
        chapter = report["chapters"][0]
        self.assertEqual(chapter["start_evidence"]["confidence"], "approximate")
        self.assertEqual(chapter["end_evidence"]["confidence"], "approximate")
        self.assertNotEqual(chapter["boundary_confidence"], "exact")
        self.assertTrue(any("estimated" in item for item in report["warnings"]))

    def test_segment_timing_is_not_reported_exact(self):
        report = cut_plan.verify_plan(plan(), transcript(words=False, timing="segment"), PAUSES, 10.0)
        chapter = report["chapters"][0]
        self.assertNotEqual(chapter["boundary_confidence"], "exact")

    def test_overlap_is_rejected_by_default(self):
        data = plan()
        data["chapters"].append({"id": "c2", "start": 3.0, "end": 4.5, "yo": "Two", "en": "Two"})
        report = cut_plan.verify_plan(data, transcript(), PAUSES, 10.0)
        self.assertFalse(report["ok"])
        self.assertTrue(any("overlaps" in item for item in report["errors"]))

    def test_overlap_can_be_explicitly_allowed(self):
        data = plan()
        data["chapters"].append({"id": "c2", "start": 3.0, "end": 4.5, "yo": "Two", "en": "Two"})
        report = cut_plan.verify_plan(data, transcript(), PAUSES, 10.0, allow_overlap=True)
        self.assertTrue(report["ok"], report["errors"])
        self.assertTrue(any("overlaps" in item for item in report["warnings"]))

    def test_out_of_bounds_is_rejected(self):
        report = cut_plan.verify_plan(plan(end=11.0), transcript(), PAUSES, 10.0)
        self.assertFalse(report["ok"])
        self.assertTrue(any("exceeds source duration" in item for item in report["errors"]))

    def test_invalid_range_is_rejected(self):
        report = cut_plan.verify_plan(plan(start=5.0, end=4.0), transcript(), PAUSES, 10.0)
        self.assertFalse(report["ok"])
        self.assertTrue(any("end must be after start" in item for item in report["errors"]))

    def test_scripture_cut_is_rejected(self):
        data = plan(start=2.0, end=3.0, scripture={
            "present": True, "start": 2.2, "end": 3.2,
            "source": "Qur'an test", "verified_by": "user", "keep_whole": True,
        })
        report = cut_plan.verify_plan(data, transcript(), PAUSES, 10.0)
        self.assertFalse(report["ok"])
        self.assertTrue(any("does not cover the declared scripture" in item for item in report["errors"]))

    def test_scripture_translation_cut_is_rejected(self):
        data = plan(start=2.0, end=4.0, scripture={
            "present": True, "start": 2.0, "end": 3.0, "translation_end": 4.5,
            "source": "Hadith test", "verified_by": "user", "keep_whole": True,
        })
        report = cut_plan.verify_plan(data, transcript(), PAUSES, 10.0)
        self.assertFalse(report["ok"])
        self.assertTrue(any("translation/explanation" in item for item in report["errors"]))

    def test_scripture_needs_human_verification(self):
        data = plan(start=2.0, end=4.0, scripture={
            "present": True, "start": 2.0, "end": 3.0,
            "source": "Qur'an test", "keep_whole": True,
        })
        report = cut_plan.verify_plan(data, transcript(), PAUSES, 10.0)
        self.assertTrue(report["ok"])
        self.assertTrue(any("not been verified" in item for item in report["warnings"]))
        strict = cut_plan.verify_plan(data, transcript(), PAUSES, 20.0, strict=True)
        self.assertFalse(strict["ok"])

    def test_teaser_length_and_scripture_exception(self):
        data = plan()
        data["duration"] = 20.0
        data["teasers"] = [{"id": "t1", "start": 2.0, "end": 14.0, "chapter_id": "c1"}]
        report = cut_plan.verify_plan(data, transcript(), PAUSES, 20.0)
        self.assertTrue(any("exceeds 10.0s" in item for item in report["warnings"]))
        strict = cut_plan.verify_plan(data, transcript(), PAUSES, 20.0, strict=True)
        self.assertFalse(strict["ok"])
        data["teasers"][0]["scripture"] = {
            "present": True, "start": 2.0, "end": 3.0,
            "source": "test", "verified_by": "user", "keep_whole": True,
        }
        report = cut_plan.verify_plan(data, transcript(), PAUSES, 20.0, strict=True)
        self.assertTrue(report["ok"], report["errors"])

    def test_approval_hash_detects_changed_ranges(self):
        data = plan()
        data["approved_chapters"] = ["c1"]
        data["approval_hash"] = cut_plan.approval_hash(data)
        report = cut_plan.verify_plan(data, transcript(), PAUSES, 10.0, require_approved=True)
        self.assertTrue(report["ok"], report["errors"])
        changed = copy.deepcopy(data)
        changed["chapters"][0]["start"] = 2.1
        report = cut_plan.verify_plan(changed, transcript(), PAUSES, 10.0, require_approved=True)
        self.assertFalse(report["ok"])
        self.assertTrue(any("approval_hash" in item for item in report["errors"]))

    def test_require_approved_rejects_unknown_id(self):
        data = plan()
        data["approved_chapters"] = ["c1", "c99"]
        data["approval_hash"] = cut_plan.approval_hash(data)
        report = cut_plan.verify_plan(data, transcript(), PAUSES, 10.0, require_approved=True)
        self.assertFalse(report["ok"])
        self.assertTrue(any("unknown chapter ids" in item for item in report["errors"]))

    def test_require_approved_rejects_empty_list(self):
        report = cut_plan.verify_plan(plan(), transcript(), PAUSES, 10.0, require_approved=True)
        self.assertFalse(report["ok"])
        self.assertTrue(any("no approved_chapters" in item for item in report["errors"]))

    def test_asr_word_key_is_used_for_timing(self):
        data = transcript()
        for segment in data["segments"]:
            for word in segment.get("words", []):
                word["word"] = word.pop("text")
        words = cut_plan.word_stream(data["segments"])
        self.assertTrue(words)
        self.assertTrue(all(word["word_timed"] for word in words))
        self.assertEqual(words[0]["start"], 0.0)

    def test_word_gaps_replace_a_missing_pause_map(self):
        report = cut_plan.verify_plan(plan(), transcript(), [], 10.0)
        chapter = report["chapters"][0]
        self.assertEqual(chapter["start_evidence"]["confidence"], "exact")
        self.assertTrue(any("using transcript word gaps" in item for item in report["warnings"]))
        self.assertEqual(report["pause_source"], "transcript word gaps")

    def test_cli_accepts_verify_prefix(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            plan_path = root / "cut_plan.json"
            transcript_path = root / "transcript.json"
            pauses_path = root / "pauses.json"
            plan_path.write_text(__import__("json").dumps(plan()), encoding="utf-8")
            transcript_path.write_text(__import__("json").dumps(transcript()), encoding="utf-8")
            pauses_path.write_text(__import__("json").dumps(PAUSES), encoding="utf-8")
            with redirect_stdout(io.StringIO()):
                code = cut_plan.main(["verify", str(plan_path),
                                      "--transcript", str(transcript_path),
                                      "--pauses", str(pauses_path)])
        self.assertEqual(code, 0)

    def test_markdown_report_contains_evidence(self):
        report = cut_plan.verify_plan(plan(), transcript(), PAUSES, 10.0)
        text = cut_plan.to_markdown(report)
        self.assertIn("Cut plan review", text)
        self.assertIn("c1", text)
        self.assertIn("exact", text)


if __name__ == "__main__":
    unittest.main()
