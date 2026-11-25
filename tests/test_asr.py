import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

PIPELINE = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PIPELINE))

import asr
import asr_benchmark as bench


class EstimateTests(unittest.TestCase):
    def test_estimate_covers_duration_and_is_ordered(self):
        text = ("Ẹ kú àárọ̀. Ẹ ṣé púpọ̀ fún ìrànlọ́wọ́ yín. "
                "Allah máa jẹ́ ká rí ọ̀rọ̀ rere. Àmín.")
        segs = asr.estimate_segments(text, 40.0)
        self.assertGreaterEqual(len(segs), 3)
        self.assertAlmostEqual(segs[0]["start"], 0.0)
        self.assertAlmostEqual(segs[-1]["end"], 40.0, places=2)
        for a, b in zip(segs, segs[1:]):
            self.assertLessEqual(a["end"], b["start"] + 1e-6)

    def test_estimate_empty(self):
        self.assertEqual(asr.estimate_segments("", 10.0), [])

    def test_estimate_long_text_splits_on_commas(self):
        text = "a" * 400
        segs = asr.estimate_segments(text, 20.0)
        self.assertGreater(len(segs), 1)
        self.assertTrue(all(len(s["text"]) <= 160 for s in segs))


class WordSegmentTests(unittest.TestCase):
    def test_words_group_on_pause_and_punctuation(self):
        words = [
            {"text": "Ẹ", "start": 0.0, "end": 0.2},
            {"text": "n", "start": 0.2, "end": 0.3},
            {"text": "lẹ.", "start": 0.3, "end": 0.6},
            {"text": "Ẹ", "start": 1.8, "end": 2.0},
            {"text": "ṣé.", "start": 2.0, "end": 2.4},
        ]
        segs = asr._segments_from_words(words, 5.0)
        self.assertEqual(len(segs), 2)
        self.assertEqual(segs[0]["text"], "Ẹ n lẹ.")
        self.assertEqual(segs[1]["start"], 1.8)
        self.assertEqual(len(segs[0]["words"]), 3)

    def test_words_keep_word_timings(self):
        words = [{"text": "Bismillah", "start": 1.0, "end": 1.6}]
        segs = asr._segments_from_words(words, 3.0)
        self.assertEqual(segs[0]["words"][0]["start"], 1.0)


class EngineTests(unittest.TestCase):
    def test_known_engines_present(self):
        for name in ("groq", "deepinfra", "elevenlabs", "speechyou",
                     "9jalingo", "hf", "ncair", "mms"):
            self.assertIn(name, asr.PROVIDERS)

    def test_default_chain_is_stable(self):
        self.assertEqual(asr._resolve_engines(None, 1), ["groq", "deepinfra", "mms"])

    def test_tier_starts_later(self):
        self.assertEqual(asr._resolve_engines(None, 2), ["deepinfra", "mms"])

    def test_unknown_engine_exits(self):
        with self.assertRaises(SystemExit):
            asr._resolve_engines("groq,nope", 1)

    def test_eleven_code_maps_yo(self):
        self.assertEqual(asr._eleven_code("yo"), "yor")
        self.assertEqual(asr._eleven_code("ha"), "hau")
        self.assertIsNone(asr._eleven_code(None))

    def test_list_engines_has_timing(self):
        for row in asr.list_engines():
            self.assertIn(row["timing"], ("word", "segment", "estimated"))


class ParserTests(unittest.TestCase):
    def test_parse_elevenlabs_words(self):
        js = {"text": "Ẹ n lẹ. Ẹ ṣé.",
              "language_code": "yor",
              "words": [{"text": "Ẹ", "start": 0.0, "end": 0.2, "type": "word"},
                        {"text": "n", "start": 0.2, "end": 0.3, "type": "word"},
                        {"text": "lẹ.", "start": 0.3, "end": 0.6, "type": "word"},
                        {"text": "Ẹ", "start": 1.5, "end": 1.7, "type": "word"},
                        {"text": "ṣé.", "start": 1.7, "end": 2.0, "type": "word"}]}
        out = asr.parse_elevenlabs(js, 4.0)
        self.assertEqual(out["timing"], "word")
        self.assertEqual(out["language"], "yor")
        self.assertEqual(len(out["segments"]), 2)
        self.assertEqual(out["segments"][0]["text"], "Ẹ n lẹ.")

    def test_parse_elevenlabs_without_words_estimates(self):
        out = asr.parse_elevenlabs({"text": "Ẹ n lẹ. Ẹ ṣé."}, 4.0)
        self.assertEqual(out["timing"], "estimated")
        self.assertEqual(len(out["segments"]), 2)

    def test_parse_speechyou_segments(self):
        result = {"model": "multilingual_pro", "language": "yo", "duration_seconds": 12,
                  "text": "Ẹ n lẹ. Ẹ ṣé.",
                  "segments": [{"start": 0.0, "end": 2.0, "text": "Ẹ n lẹ."},
                               {"start": 2.5, "end": 4.0, "text": "Ẹ ṣé."}]}
        out = asr.parse_speechyou(result, 12.0, "yo")
        self.assertEqual(out["timing"], "segment")
        self.assertEqual(out["segments"][1]["start"], 2.5)
        self.assertEqual(out["model"], "multilingual_pro")

    def test_parse_speechyou_without_segments_estimates(self):
        out = asr.parse_speechyou({"text": "Ẹ n lẹ. Ẹ ṣé."}, 6.0, "yo")
        self.assertEqual(out["timing"], "estimated")
        self.assertGreaterEqual(len(out["segments"]), 1)

    def test_parse_9jalingo_text_only(self):
        out = asr.parse_9jalingo({"text": "Ẹ n lẹ. Ẹ ṣé.", "duration": 8}, 8.0, "yo")
        self.assertEqual(out["timing"], "estimated")
        self.assertAlmostEqual(out["segments"][-1]["end"], 8.0, places=2)

    def test_hf_token_from_env(self):
        with mock.patch.dict(os.environ, {"HF_TOKEN": "hf_test"}, clear=False):
            self.assertEqual(asr.hf_token(), "hf_test")

    def test_hf_token_from_login_file(self):
        with tempfile.TemporaryDirectory() as temp:
            cache = Path(temp) / ".cache" / "huggingface"
            cache.mkdir(parents=True)
            (cache / "token").write_text("hf_login\n", encoding="utf-8")
            env = {k: v for k, v in os.environ.items() if k not in
                   ("HF_TOKEN", "HUGGINGFACE_API_KEY", "HF_API_TOKEN")}
            with mock.patch.dict(os.environ, env, clear=True), \
                    mock.patch.object(asr.Path, "home", return_value=Path(temp)):
                self.assertEqual(asr.hf_token(), "hf_login")


class PlaceholderTests(unittest.TestCase):
    def test_pure_tags_are_placeholders(self):
        for text in ("[mumbles]", "[speaking in foreign language]", "[inaudible] [foreign language]", "", "  "):
            self.assertTrue(asr.is_placeholder(text), text)

    def test_real_text_with_a_tag_is_not_a_placeholder(self):
        self.assertFalse(asr.is_placeholder("[inaudible] akekale, tinta ni one thousand."))
        self.assertFalse(asr.is_placeholder("Ẹ n lẹ"))

    def test_short_placeholder_falls_through(self):
        report = []
        with mock.patch.object(asr, "_call_engine",
                               return_value={"text": "[mumbles]", "segments": []}):
            got, used, timings = asr._transcribe_span(
                Path("/tmp/x.wav"), 0.0, 10.0, ["elevenlabs"], "p", "yo", [], None,
                Path("/tmp"), report, "0")
        self.assertIsNone(got)
        self.assertEqual(used, set())
        self.assertTrue(any("NoSpeech" in r.get("error", "") for r in report))

    def test_long_placeholder_splits_and_merges(self):
        report = []
        calls = []

        def fake_call(name, path, prompt, language, duration, keyterms, public_url):
            calls.append((str(path), duration))
            if str(path).endswith("orig.wav"):
                return {"text": "[speaking in foreign language]", "segments": []}
            return {"text": "Ẹ n lẹ.", "segments": [
                {"start": 0.0, "end": 1.0, "text": "Ẹ n lẹ."}], "timing": "word",
                "model": "scribe_v2"}

        pieces = [(Path("/tmp/a.wav"), 0.0, 15.0), (Path("/tmp/b.wav"), 15.0, 15.0)]
        with mock.patch.object(asr, "_call_engine", side_effect=fake_call), \
                mock.patch.object(asr, "_split_chunk", return_value=pieces):
            got, used, timings = asr._transcribe_span(
                Path("/tmp/orig.wav"), 0.0, 30.0, ["elevenlabs"], "p", "yo", [], None,
                Path("/tmp"), report, "0")
        self.assertIsNotNone(got)
        self.assertEqual(used, {"elevenlabs"})
        self.assertEqual(len(got["segments"]), 2)
        self.assertEqual(got["segments"][1]["start"], 15.0)
        self.assertIn("Ẹ n lẹ.", got["text"])


class ReviewAndSafetyTests(unittest.TestCase):
    def test_write_review_csv_with_chapter_map(self):
        with tempfile.TemporaryDirectory() as temp:
            path = Path(temp) / "transcript.review.csv"
            result = {"engine": "elevenlabs", "segments": [
                {"start": 0.0, "end": 2.0, "text": "Ẹ n lẹ."},
                {"start": 5.0, "end": 7.0, "text": "Ẹ ṣé."},
            ]}
            asr.write_review(result, path, [{"id": "c1", "start": 0.0, "end": 3.0},
                                            {"id": "c2", "start": 4.0, "end": 8.0}])
            text = path.read_text(encoding="utf-8")
        self.assertIn("line,start,end,chapter,engine,text,correction", text)
        self.assertIn(",c1,elevenlabs,Ẹ n lẹ.,", text)
        self.assertIn(",c2,elevenlabs,Ẹ ṣé.,", text)

    def test_transcribe_refuses_to_overwrite_raw(self):
        with tempfile.TemporaryDirectory() as temp:
            source = Path(temp) / "source.mp4"
            source.write_bytes(b"x")
            out = Path(temp) / "transcript.raw.json"
            out.write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(SystemExit, "refusing to overwrite raw transcript"):
                asr.transcribe(source, out_path=out)

    def test_failed_chunks_are_recorded(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)
            source = root / "source.mp4"
            source.write_bytes(b"x")
            out = root / "transcript.raw.json"
            with mock.patch.object(asr, "duration_of", return_value=10.0), \
                    mock.patch.object(asr, "extract_audio"), \
                    mock.patch.object(asr, "_transcribe_span", return_value=(None, set(), set())):
                result = asr.transcribe(source, out_path=out, no_chunk=True)
            self.assertEqual(len(result["failed_chunks"]), 1)
            self.assertEqual(result["failed_chunks"][0]["chunk"], 0)
            self.assertTrue(out.is_file())


class BenchmarkMetricTests(unittest.TestCase):
    def test_normalize_strips_punctuation_and_case(self):
        self.assertEqual(bench.normalize("Ẹ Kú Ìrọ̀lẹ́, o!"), "ẹ kú ìrọ̀lẹ́ o")

    def test_wer_identical_is_zero(self):
        wer, cer = bench.error_rates("Ẹ n lẹ", "Ẹ n lẹ")
        self.assertEqual(wer, 0.0)
        self.assertEqual(cer, 0.0)

    def test_wer_counts_one_substitution(self):
        wer, _ = bench.error_rates("Ẹ n lẹ", "Ẹ n lọ")
        self.assertAlmostEqual(wer, 1 / 3, places=4)

    def test_diacritics_option(self):
        wer_raw, _ = bench.error_rates("Ẹ n lẹ", "E n le")
        wer_nod, _ = bench.error_rates("Ẹ n lẹ", "E n le", strip_diacritics=True)
        self.assertGreater(wer_raw, wer_nod)
        self.assertEqual(wer_nod, 0.0)


if __name__ == "__main__":
    unittest.main()
