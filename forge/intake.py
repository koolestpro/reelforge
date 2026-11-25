#!/usr/bin/env python3
"""Read-only intake for a new lecture source.

Stage 1 of the controlled workflow. It probes the source and checks the video's library folder
for existing work. It never transcribes, renders, uploads or calls an API.

    python3 intake.py SOURCE --library library/2026-11-06_lecture_scholar
    python3 intake.py SOURCE --library ... --probe-json 0-analysis/2026-11-06/probe.json

The report is written to `library/<video>/intake.md` unless `--out` says otherwise. An existing
report is never replaced without `--force`.
"""
import argparse
import json
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

ARTIFACTS = (
    ("raw transcript", ("transcript.raw.*.json", "transcript.raw.*.txt")),
    ("corrected Yoruba transcript", ("transcript.yo.md",)),
    ("English translation", ("transcript.en.md",)),
    ("chapter plan", ("chapters.md",)),
    ("structured cut plan", ("cut_plan.json",)),
    ("cut plan review", ("cut_plan.verified.json", "cut_plan.review.md")),
    ("approval log", ("approvals.md",)),
    ("job", ("job*.json",)),
)


def _number(value):
    try:
        number = float(value)
    except (TypeError, ValueError):
        return None
    return number if number == number and abs(number) != float("inf") else None


def probe(source):
    command = [
        "ffprobe", "-v", "error", "-show_streams", "-show_format", "-of", "json", str(source),
    ]
    result = subprocess.run(command, check=True, capture_output=True, text=True)
    data = json.loads(result.stdout)
    streams = data.get("streams") or []
    video = next((item for item in streams if item.get("codec_type") == "video"), None)
    audio = next((item for item in streams if item.get("codec_type") == "audio"), None)
    fmt = data.get("format") or {}
    duration = _number(fmt.get("duration"))
    size = _number(fmt.get("size")) or (source.stat().st_size if source.is_file() else None)
    video_info = None
    if video:
        rate = video.get("avg_frame_rate") or video.get("r_frame_rate") or "0/1"
        try:
            numerator, denominator = rate.split("/")
            fps = float(numerator) / float(denominator) if float(denominator) else None
        except (AttributeError, ValueError, ZeroDivisionError):
            fps = None
        video_info = {
            "codec": video.get("codec_name"),
            "width": video.get("width"),
            "height": video.get("height"),
            "fps": round(fps, 3) if fps else None,
            "pix_fmt": video.get("pix_fmt"),
        }
    audio_info = None
    if audio:
        audio_info = {
            "codec": audio.get("codec_name"),
            "sample_rate": _number(audio.get("sample_rate")),
            "channels": audio.get("channels"),
            "bit_rate": _number(audio.get("bit_rate")),
        }
    return {
        "source": str(source.resolve()),
        "size_bytes": int(size) if size is not None else None,
        "duration": round(duration, 3) if duration is not None else None,
        "format": fmt.get("format_name"),
        "video": video_info,
        "audio": audio_info,
        "probed_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }


def find_artifacts(library):
    library = Path(library)
    found = {}
    for label, patterns in ARTIFACTS:
        paths = []
        for pattern in patterns:
            paths.extend(sorted(str(path.relative_to(library)) for path in library.glob(pattern)))
        if paths:
            found[label] = paths
    return found


def proposed_next_action(artifacts):
    if "raw transcript" not in artifacts:
        return "Stage 2: split the source below 30 minutes if needed, then transcribe the parts."
    if "corrected Yoruba transcript" not in artifacts:
        return "Stage 3: review and correct the transcript; keep the raw file untouched."
    if "chapter plan" not in artifacts or "structured cut plan" not in artifacts:
        return "Stage 4: read the full transcript, propose topics, and create chapters.md and cut_plan.json."
    if "cut plan review" not in artifacts:
        return "Stage 5: run cut_plan.py verify, listen to the boundary previews, and fix the draft."
    if "approval log" not in artifacts:
        return "Stage 5: ask the user to approve the final cut list before any render."
    return "Stage 6: render only the approved chapters, one or a small batch at a time."


def report_text(meta, library, artifacts, next_action):
    lines = [
        "# Intake",
        "",
        f"- source: `{meta['source']}`",
        f"- library: `{library}`",
        f"- duration: {meta['duration']}s" if meta["duration"] is not None else "- duration: unknown",
        f"- size: {meta['size_bytes']} bytes" if meta["size_bytes"] else "- size: unknown",
        f"- format: {meta.get('format') or 'unknown'}",
        "",
        "## Streams",
        "",
    ]
    video = meta.get("video")
    audio = meta.get("audio")
    lines.append(f"- video: {video}" if video else "- video: missing")
    lines.append(f"- audio: {audio}" if audio else "- audio: missing")
    lines += ["", "## Existing work", ""]
    if artifacts:
        for label, paths in artifacts.items():
            lines.append(f"- {label}: {', '.join(paths)}")
    else:
        lines.append("- none")
    lines += ["", "## Proposed next action", "", next_action, "",
              "## Gate", "",
              "Stop here. Show this report to the user and wait for approval before Stage 2.",
              ""]
    return "\n".join(lines)


def main(argv=None):
    parser = argparse.ArgumentParser(description="Read-only intake for a lecture source.")
    parser.add_argument("source", help="video or audio source")
    parser.add_argument("--library", required=True, help="video library folder")
    parser.add_argument("--out", help="intake report (default: <library>/intake.md)")
    parser.add_argument("--probe-json", help="also write the raw probe JSON here")
    parser.add_argument("--force", action="store_true", help="overwrite an existing report")
    args = parser.parse_args(argv)

    source = Path(args.source).expanduser().resolve()
    library = Path(args.library).expanduser().resolve()
    if not source.is_file():
        print(f"intake: source not found: {source}", file=sys.stderr)
        return 2
    if not library.is_dir():
        print(f"intake: library folder does not exist: {library}", file=sys.stderr)
        return 2
    out = Path(args.out).expanduser().resolve() if args.out else library / "intake.md"
    if out.exists() and not args.force:
        print(f"intake: refusing to overwrite {out}; pass --force", file=sys.stderr)
        return 2
    try:
        meta = probe(source)
    except (OSError, subprocess.CalledProcessError, json.JSONDecodeError, ValueError) as exc:
        print(f"intake: cannot probe source: {exc}", file=sys.stderr)
        return 2
    artifacts = find_artifacts(library)
    next_action = proposed_next_action(artifacts)
    text = report_text(meta, library, artifacts, next_action)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8")
    if args.probe_json:
        probe_path = Path(args.probe_json).expanduser().resolve()
        if probe_path.exists() and not args.force:
            print(f"intake: refusing to overwrite {probe_path}; pass --force", file=sys.stderr)
            return 2
        probe_path.parent.mkdir(parents=True, exist_ok=True)
        probe_path.write_text(json.dumps(meta, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(text)
    print(f"wrote {out}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
