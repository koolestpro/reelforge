#!/usr/bin/env python3
"""Plan and run ASR in parts below a time limit, cutting at a long silence.

The source video is never modified. The tool finds a long pause near the allowed split window
(the Imam sitting, a second-khutbah pause, or any very long silence), extracts one audio part per
side, transcribes each part, and merges the results onto the original source timeline.

Default is plan-only: it finds the split and writes `split.json` without spending any API credit.
Add `--transcribe` for Stage 2 after the user approves.

    python3 split_for_asr.py SOURCE --out-dir library/<video>/asr_split --plan-only
    python3 split_for_asr.py SOURCE --out-dir library/<video>/asr_split --transcribe \
      --engines elevenlabs,9jalingo,groq --out library/<video>/transcript.raw.elevenlabs.json

Existing part transcripts are reused, so a rerun does not pay for the same audio twice.
"""
import argparse
import json
import re
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import asr

DEFAULT_MAX_PART = 1740.0      # 29 minutes, comfortably below the 30 minute target
DEFAULT_MIN_PAUSE = 2.5
DEFAULT_NOISE = "-35dB"
DEFAULT_CHUNK = 300
SILENCE_START = re.compile(r"silence_start:\s*([0-9.]+)")
SILENCE_END = re.compile(r"silence_end:\s*([0-9.]+)\s*\|\s*silence_duration:\s*([0-9.]+)")


class SplitError(Exception):
    pass


def _run(command, capture=True):
    return subprocess.run([str(part) for part in command], check=True,
                          stdin=subprocess.DEVNULL, capture_output=capture, text=True)


def duration_of(source):
    result = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                   "-of", "csv=p=0", source])
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise SplitError(f"cannot read duration from {source}") from exc


def parse_silences(text, duration=None):
    silences = []
    current = None
    for line in text.splitlines():
        start = SILENCE_START.search(line)
        end = SILENCE_END.search(line)
        if start:
            current = float(start.group(1))
        if end:
            end_time = float(end.group(1))
            length = float(end.group(2))
            if current is not None:
                silences.append({"start": round(current, 3), "end": round(end_time, 3),
                                 "duration": round(length, 3)})
            current = None
    if current is not None and duration and duration > current:
        silences.append({"start": round(current, 3), "end": round(duration, 3),
                         "duration": round(duration - current, 3)})
    return silences


def detect_silences(source, duration, noise=DEFAULT_NOISE, min_pause=DEFAULT_MIN_PAUSE):
    result = subprocess.run(
        ["ffmpeg", "-nostdin", "-hide_banner", "-nostats", "-i", str(source), "-vn",
         "-af", f"silencedetect=noise={noise}:d={min_pause}", "-f", "null", "-"],
        stdin=subprocess.DEVNULL, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise SplitError(f"ffmpeg silencedetect failed: {result.stderr[-400:]}")
    silences = parse_silences(result.stderr, duration)
    return [item for item in silences if item["duration"] >= min_pause]


def choose_splits(duration, silences, max_part=DEFAULT_MAX_PART, min_pause=DEFAULT_MIN_PAUSE):
    """Return split points that cut inside long pauses and keep every part below max_part."""
    if duration <= max_part:
        return []
    splits = []
    start = 0.0
    guard = 0
    while duration - start > max_part:
        guard += 1
        if guard > 20:
            raise SplitError("too many splits; check the source duration and max part length")
        lo = start + max_part * 0.35
        hi = min(start + max_part, duration - 1.0)
        if hi <= lo:
            raise SplitError(f"no room for another split after {start:.1f}s")
        candidates = [item for item in silences
                      if item["duration"] >= min_pause
                      and item["end"] > lo and item["start"] < hi]
        if not candidates:
            raise SplitError(
                f"no pause of at least {min_pause}s between {lo:.1f}s and {hi:.1f}s; "
                "choose --split-at manually or add a pause map"
            )
        chosen = sorted(candidates, key=lambda item: (-item["duration"], item["start"]))[0]
        split = round((chosen["start"] + chosen["end"]) / 2.0, 3)
        split = min(max(split, lo), hi)
        if split <= start + 1.0:
            raise SplitError("chosen split does not advance the source")
        if split - start > max_part + 0.01:
            raise SplitError("chosen split leaves a part above the limit")
        splits.append({"at": split, "pause": chosen,
                       "reason": "longest pause in the allowed window"})
        start = split
    return splits


def find_silence_at(silences, at):
    for item in silences:
        if item["start"] - 0.05 <= at <= item["end"] + 0.05:
            return item
    return None


def plan_parts(source, out_dir, max_part=DEFAULT_MAX_PART, min_pause=DEFAULT_MIN_PAUSE,
               noise=DEFAULT_NOISE, split_at=None, force=False):
    source = Path(source).expanduser().resolve()
    out_dir = Path(out_dir).expanduser().resolve()
    if not source.is_file():
        raise SplitError(f"source not found: {source}")
    out_dir.mkdir(parents=True, exist_ok=True)
    duration = duration_of(source)
    silences = detect_silences(source, duration, noise, min_pause)
    if split_at is not None:
        split_at = float(split_at)
        if not 0 < split_at < duration:
            raise SplitError("--split-at must be inside the source")
        pause = find_silence_at(silences, split_at)
        if not pause:
            raise SplitError("--split-at is not inside a detected pause; move it or lower --min-pause")
        splits = [{"at": round(split_at, 3), "pause": pause, "reason": "user supplied --split-at"}]
        if duration - split_at > max_part + 0.01 or split_at > max_part + 0.01:
            raise SplitError("--split-at leaves a part above the limit")
    else:
        splits = choose_splits(duration, silences, max_part, min_pause)

    parts = []
    bounds = [0.0] + [item["at"] for item in splits] + [duration]
    for index, (start, end) in enumerate(zip(bounds, bounds[1:]), start=1):
        parts.append({
            "index": index,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
            "audio": str(out_dir / f"part-{index:02d}.mp3"),
        })
    pauses_path = out_dir / "pauses.json"
    if not pauses_path.exists() or force:
        pauses_path.write_text(json.dumps(
            [[item["start"], item["end"]] for item in silences], ensure_ascii=False, indent=2) + "\n",
            encoding="utf-8")
    payload = {
        "source": str(source),
        "pauses": str(pauses_path),
        "duration": round(duration, 3),
        "max_part_seconds": max_part,
        "min_pause_seconds": min_pause,
        "noise": noise,
        "splits": splits,
        "parts": parts,
        "candidates": sorted(silences, key=lambda item: -item["duration"])[:12],
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "note": "Source video is not modified. Audio parts are cut for ASR only.",
    }
    split_path = out_dir / "split.json"
    if split_path.exists() and not force:
        existing = json.loads(split_path.read_text(encoding="utf-8"))
        same = (existing.get("source") == payload["source"]
                and abs(float(existing.get("duration", 0)) - duration) < 0.01
                and abs(float(existing.get("max_part_seconds", 0)) - max_part) < 0.01
                and abs(float(existing.get("min_pause_seconds", 0)) - min_pause) < 0.01
                and (split_at is None or any(abs(item["at"] - split_at) < 0.01
                                             for item in existing.get("splits", []))))
        if not same:
            raise SplitError(f"{split_path} exists for a different plan; pass --force to replace it")
        return existing, split_path
    split_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    return payload, split_path


def write_audio_parts(plan, force=False):
    written = []
    for part in plan["parts"]:
        path = Path(part["audio"])
        if path.exists() and not force:
            written.append(path)
            continue
        _run(["ffmpeg", "-nostdin", "-v", "error", "-y",
              "-ss", f"{part['start']:.3f}", "-t", f"{part['duration']:.3f}",
              "-i", plan["source"], "-vn", "-ac", "1", "-ar", "16000",
              "-b:a", "64k", path])
        written.append(path)
    return written


def part_paths(plan):
    return [
        {"index": part["index"], "start": part["start"], "end": part["end"],
         "audio": Path(part["audio"]),
         "raw": Path(part["audio"]).with_name(f"part-{part['index']:02d}.raw.json"),
         "txt": Path(part["audio"]).with_name(f"part-{part['index']:02d}.txt"),
         "chunks": Path(part["audio"]).with_name(f"part-{part['index']:02d}_chunks")}
        for part in plan["parts"]
    ]


def run_asr(parts, engines=None, chunk_seconds=DEFAULT_CHUNK, language="yo",
            prompt=None, force_parts=False, review=False):
    command_base = [sys.executable, HERE / "asr.py"]
    results = []
    for part in parts:
        raw = part["raw"]
        if raw.exists() and not force_parts:
            print(f"part {part['index']}: reusing {raw}", flush=True)
            results.append(part)
            continue
        command = command_base + [
            part["audio"], "--out", raw, "--txt", part["txt"],
            "--workdir", part["chunks"], "--chunk-seconds", str(chunk_seconds),
            "--language", language,
        ]
        if engines:
            command += ["--engines", engines]
        if prompt:
            command += ["--prompt", prompt]
        if review:
            command += ["--review", raw.with_name(f"part-{part['index']:02d}.review.csv")]
        if force_parts:
            command += ["--force"]
        print(f"part {part['index']}: transcribing {part['start']:.1f}-{part['end']:.1f}s",
              flush=True)
        subprocess.run([str(item) for item in command], check=True)
        results.append(part)
    return results


def _shift_words(words, offset):
    if not isinstance(words, list):
        return words
    shifted = []
    for word in words:
        if not isinstance(word, dict):
            continue
        item = dict(word)
        if isinstance(item.get("start"), (int, float)):
            item["start"] = round(float(item["start"]) + offset, 3)
        if isinstance(item.get("end"), (int, float)):
            item["end"] = round(float(item["end"]) + offset, 3)
        shifted.append(item)
    return shifted


def merge_parts(plan, parts, out_path, split_path, force=False, language="yo",
                prompt=None, chunk_seconds=None, review_path=None, chapter_map=None):
    out_path = Path(out_path).expanduser().resolve()
    review_path = Path(review_path).expanduser().resolve() if review_path else None
    if out_path.exists() and not force:
        raise SplitError(f"refusing to overwrite {out_path}; pass --force")
    if review_path and review_path.exists() and not force:
        raise SplitError(f"refusing to overwrite {review_path}; pass --force")
    segments = []
    tier_report = []
    failed_chunks = []
    engines = set()
    timings = set()
    texts = []
    for part in parts:
        data = json.loads(part["raw"].read_text(encoding="utf-8"))
        offset = float(part["start"])
        for segment in data.get("segments") or []:
            item = dict(segment)
            item["start"] = round(float(item["start"]) + offset, 3)
            item["end"] = round(float(item["end"]) + offset, 3)
            item["part"] = part["index"]
            if "words" in item:
                item["words"] = _shift_words(item["words"], offset)
            segments.append(item)
        for entry in data.get("tier_report") or []:
            item = dict(entry)
            item["part"] = part["index"]
            item["part_offset"] = round(offset, 3)
            if isinstance(item.get("offset"), (int, float)):
                item["offset"] = round(offset + float(item["offset"]), 3)
            else:
                item["offset"] = round(offset, 3)
            tier_report.append(item)
        for entry in data.get("failed_chunks") or []:
            item = dict(entry)
            item["part"] = part["index"]
            item["part_offset"] = round(offset, 3)
            if isinstance(item.get("offset"), (int, float)):
                item["offset"] = round(offset + float(item["offset"]), 3)
            else:
                item["offset"] = round(offset, 3)
            failed_chunks.append(item)
        if data.get("engine"):
            engines.update(part_name for part_name in str(data["engine"]).split("+") if part_name)
        if data.get("engines_used"):
            engines.update(data["engines_used"])
        if data.get("timing"):
            timings.add(data["timing"])
        if data.get("text"):
            texts.append(str(data["text"]).strip())
    segments.sort(key=lambda item: (item["start"], item["end"]))
    timing = "word" if "word" in timings else ("segment" if "segment" in timings else "estimated")
    result = {
        "task": "transcribe",
        "engine": "+".join(sorted(engines)) or "none",
        "timing": timing,
        "timing_parts": sorted(timings),
        "engines_used": sorted(engines),
        "language": language,
        "source": plan["source"],
        "duration": plan["duration"],
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "prompt_hint": prompt,
        "chunk_seconds": chunk_seconds,
        "split": {
            "parts": plan["parts"],
            "splits": plan["splits"],
            "split_plan": str(Path(split_path).resolve()),
        },
        "part_reports": [{"index": part["index"], "start": part["start"],
                          "end": part["end"], "raw": str(part["raw"])} for part in parts],
        "tier_report": tier_report,
        "failed_chunks": failed_chunks,
        "segments": segments,
        "text": " ".join(texts),
    }
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    txt_path = out_path.with_suffix(".txt")
    lines = []
    for segment in segments:
        minutes, seconds = divmod(int(segment["start"]), 60)
        hours, minutes = divmod(minutes, 60)
        stamp = f"{hours:d}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes:02d}:{seconds:02d}"
        lines.append(f"[{stamp}] {segment['text']}")
    txt_path.write_text("\n".join(lines) + "\n", encoding="utf-8")
    if review_path:
        ranges = asr.load_chapter_map(chapter_map) if chapter_map else []
        asr.write_review(result, review_path, ranges)
    return result, out_path, txt_path


def _print_plan(plan, split_path):
    print(f"source {Path(plan['source']).name}  duration {plan['duration']:.1f}s  "
          f"parts {len(plan['parts'])}  split plan {split_path}")
    for part in plan["parts"]:
        print(f"  part {part['index']}: {part['start']:.1f}-{part['end']:.1f}s "
              f"({part['duration'] / 60:.1f} min) -> {part['audio']}")
    for split in plan["splits"]:
        pause = split["pause"]
        print(f"  split at {split['at']:.1f}s inside silence {pause['start']:.1f}-{pause['end']:.1f}s "
              f"({pause['duration']:.1f}s)")
    if plan["candidates"]:
        print("  longest pauses:")
        for candidate in plan["candidates"][:5]:
            print(f"    {candidate['start']:.1f}-{candidate['end']:.1f}s "
                  f"({candidate['duration']:.1f}s)")


def main(argv=None):
    parser = argparse.ArgumentParser(description="Split a long source at a pause for ASR.")
    parser.add_argument("source", help="video or audio source")
    parser.add_argument("--out-dir", required=True, help="folder for split.json and audio parts")
    parser.add_argument("--max-part-seconds", type=float, default=DEFAULT_MAX_PART,
                        help="maximum seconds per part (default 1740, 29 min)")
    parser.add_argument("--min-pause", type=float, default=DEFAULT_MIN_PAUSE,
                        help="minimum silence length for a split (default 2.5 s)")
    parser.add_argument("--noise", default=DEFAULT_NOISE, help="silencedetect noise floor")
    parser.add_argument("--split-at", type=float, help="use this pause-relative split time")
    parser.add_argument("--transcribe", action="store_true",
                        help="run ASR on each part and merge (spends API credits)")
    parser.add_argument("--plan-only", action="store_true",
                        help="find the split and write split.json only (default)")
    parser.add_argument("--engines", help="ordered ASR engines, e.g. elevenlabs,9jalingo,groq")
    parser.add_argument("--chunk-seconds", type=int, default=DEFAULT_CHUNK)
    parser.add_argument("--language", default="yo")
    parser.add_argument("--prompt", help="ASR prompt hint")
    parser.add_argument("--out", help="merged raw transcript JSON")
    parser.add_argument("--review", nargs="?", const="", default=None,
                        help="write a merged source-timeline review CSV (optional path)")
    parser.add_argument("--chapter-map", help="job or cut-plan JSON used to fill the review chapter column")
    parser.add_argument("--force", action="store_true", help="overwrite split.json and merged output")
    parser.add_argument("--force-parts", action="store_true", help="re-transcribe existing part JSON")
    args = parser.parse_args(argv)

    try:
        if args.plan_only and args.transcribe:
            raise SplitError("use either --plan-only or --transcribe, not both")
        plan, split_path = plan_parts(
            args.source, args.out_dir, args.max_part_seconds, args.min_pause,
            args.noise, args.split_at, args.force)
        _print_plan(plan, split_path)
        if not args.transcribe:
            print("plan only: no API call made. Add --transcribe after approval.")
            return 0
        first_engine = (args.engines or "split").split(",")[0]
        out = Path(args.out) if args.out else Path(args.out_dir) / f"transcript.raw.{first_engine}.json"
        if out.exists() and not args.force:
            raise SplitError(f"refusing to overwrite {out}; pass --force")
        review_path = None
        if args.review is not None:
            review_path = Path(args.review) if args.review else Path(args.out_dir) / "transcript.review.csv"
        write_audio_parts(plan, args.force)
        parts = part_paths(plan)
        run_asr(parts, args.engines, args.chunk_seconds, args.language, args.prompt,
                args.force_parts, bool(review_path))
        result, out_path, txt_path = merge_parts(
            plan, parts, out, split_path, args.force, args.language, args.prompt,
            args.chunk_seconds, review_path, args.chapter_map)
        print(f"merged {len(result['segments'])} segments -> {out_path} and {txt_path}")
        return 0
    except SplitError as exc:
        print(f"split_for_asr: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"split_for_asr: command failed: {exc}", file=sys.stderr)
        return 2
    except (OSError, json.JSONDecodeError) as exc:
        print(f"split_for_asr: cannot read or write a file: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
