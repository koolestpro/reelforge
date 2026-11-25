#!/usr/bin/env python3
"""Cut a lecture master into pause-aligned WAV chunks for Adobe Podcast Enhance.

The source files are never modified. With more than one source, the tool joins them in the
given order into a canonical master, detects silences, and cuts N mono 48 kHz WAV chunks at
the longest pause near each equal target. `audio_join.py` puts the enhanced chunks back on the
same timeline.

    python3 adobe_chunks.py SOURCE [SOURCE ...] --out-dir library/<video>/adobe_audio_chunks \
        --chunks 3 --min-pause 1.5 --noise -30dB
"""
import argparse
import json
import os
import subprocess
import sys
from datetime import datetime, timezone
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from split_for_asr import SplitError, detect_silences

DEFAULT_CHUNKS = 3
DEFAULT_MIN_PAUSE = 1.5
DEFAULT_NOISE = "-30dB"
DEFAULT_WINDOW = 120.0
DEFAULT_MASTER_NAME = "master.mp4"


class ChunkError(Exception):
    pass


def _run(command, capture=True):
    return subprocess.run(
        [str(part) for part in command],
        check=True,
        stdin=subprocess.DEVNULL,
        capture_output=capture,
        text=True,
    )


def duration_of(source):
    result = _run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                   "-of", "csv=p=0", source])
    try:
        return float(result.stdout.strip())
    except ValueError as exc:
        raise ChunkError(f"cannot read duration from {source}") from exc


def join_sources(sources, master, reencode=False, force=False):
    sources = [Path(item).expanduser().resolve() for item in sources]
    for source in sources:
        if not source.is_file():
            raise ChunkError(f"source not found: {source}")
    if len(sources) == 1:
        return sources[0], duration_of(sources[0])
    master = Path(master).expanduser().resolve()
    expected = sum(duration_of(source) for source in sources)
    if master.exists() and not force:
        actual = duration_of(master)
        if abs(actual - expected) <= 1.0:
            return master, actual
        raise ChunkError(
            f"{master} is {actual:.3f}s, expected {expected:.3f}s; pass --force to rebuild")
    master.parent.mkdir(parents=True, exist_ok=True)
    concat = master.with_name(f"{master.stem}_concat.txt")
    concat.write_text("".join(f"file '{source}'\n" for source in sources), encoding="utf-8")
    command = ["ffmpeg", "-nostdin", "-v", "error", "-y",
               "-f", "concat", "-safe", "0", "-i", concat]
    if reencode:
        command += ["-c:v", "libx264", "-preset", "veryfast", "-crf", "18",
                    "-c:a", "aac", "-b:a", "256k", "-movflags", "+faststart", master]
    else:
        command += ["-c", "copy", "-movflags", "+faststart", master]
    try:
        _run(command, capture=False)
    except subprocess.CalledProcessError as exc:
        if not reencode:
            raise ChunkError("stream-copy join failed; rerun with --reencode") from exc
        raise
    actual = duration_of(master)
    if abs(actual - expected) > 1.0:
        raise ChunkError(
            f"joined duration {actual:.3f}s does not match the source total {expected:.3f}s")
    return master, actual


def choose_chunk_points(duration, silences, chunks, window=DEFAULT_WINDOW):
    if chunks < 1:
        raise ChunkError("--chunks must be at least 1")
    if chunks == 1:
        return []
    targets = [duration * index / chunks for index in range(1, chunks)]
    splits = []
    used = set()
    previous = 0.0
    for target in targets:
        candidates = [
            item for item in silences
            if item["duration"] > 0
            and abs((item["start"] + item["end"]) / 2.0 - target) <= window
            and (item["start"], item["end"]) not in used
        ]
        if not candidates:
            raise ChunkError(
                f"no pause within {window:.0f}s of {target:.1f}s; "
                "try a lower --min-pause, a different --noise, or fewer --chunks")
        chosen = sorted(
            candidates,
            key=lambda item: (
                -item["duration"],
                abs((item["start"] + item["end"]) / 2.0 - target),
            ),
        )[0]
        at = round((chosen["start"] + chosen["end"]) / 2.0, 3)
        if at <= previous + 1.0 or at >= duration - 1.0:
            raise ChunkError(f"chosen split {at:.3f}s is not usable")
        used.add((chosen["start"], chosen["end"]))
        splits.append({"at": at, "target": round(target, 3), "pause": chosen})
        previous = at
    return splits


def plan_chunks(duration, splits, chunks):
    bounds = [0.0] + [item["at"] for item in splits] + [duration]
    if len(bounds) != chunks + 1:
        raise ChunkError(f"planned {len(bounds) - 1} chunks, expected {chunks}")
    items = []
    for index, (start, end) in enumerate(zip(bounds, bounds[1:]), 1):
        if end - start < 30.0:
            raise ChunkError(f"chunk {index} is shorter than 30s")
        items.append({
            "index": index,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(end - start, 3),
        })
    return items


def chunk_name(item):
    return f"chunk_{item['index']:02d}_{int(item['start']):04d}-{int(item['end']):04d}.wav"


def write_chunks(master, items, out_dir, force=False):
    written = []
    for item in items:
        dest = out_dir / chunk_name(item)
        item["file"] = dest.name
        if dest.exists() and not force:
            raise ChunkError(f"refusing to overwrite {dest}; pass --force")
        _run(["ffmpeg", "-nostdin", "-v", "error", "-y",
              "-ss", f"{item['start']:.3f}", "-t", f"{item['duration']:.3f}",
              "-i", master, "-vn", "-ac", "1", "-ar", "48000",
              "-c:a", "pcm_s16le", dest], capture=False)
        actual = duration_of(dest)
        if abs(actual - item["duration"]) > 0.3:
            raise ChunkError(
                f"{dest.name} is {actual:.3f}s, expected {item['duration']:.3f}s")
        written.append(dest)
    return written


def write_outputs(out_dir, master, sources, duration, chunks, min_pause, noise, items, splits):
    manifest = [
        {"index": item["index"], "start": item["start"], "end": item["end"],
         "duration": item["duration"], "file": item["file"]}
        for item in items
    ]
    (out_dir / "audio_chunks.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    cwd = Path.cwd()
    master_display = os.path.relpath(master, cwd)
    sources_display = [os.path.relpath(item, cwd) for item in sources]
    plan = {
        "master": master_display,
        "sources": sources_display,
        "duration": round(duration, 3),
        "chunks": chunks,
        "min_pause": min_pause,
        "noise": noise,
        "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "splits": splits,
        "items": manifest,
    }
    (out_dir / "chunks_plan.json").write_text(
        json.dumps(plan, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    join_display = os.path.relpath(out_dir, cwd)
    lines = [
        "# Adobe audio chunks",
        "",
        f"Master: `{master_display}`",
        f"Duration: {duration:.3f} s",
        f"Chunks: {chunks}, cut in pauses, mono 48 kHz PCM WAV.",
        "",
        "1. Upload each WAV to Adobe Podcast Enhance.",
        "2. Download the enhanced WAV with the same name into `enhanced/` in this folder.",
        "3. Join the enhanced parts:",
        "",
        f"    python3 forge/audio_join.py {join_display}",
        "",
        "The joined file is `enhanced_full.wav`, on the same timeline as the master.",
        "Add `--suffix` to `audio_join.py` if Adobe uses a different filename suffix.",
        "",
        "To rebuild the chunks, rerun `adobe_chunks.py` with `--force`.",
        "",
    ]
    (out_dir / "README.md").write_text("\n".join(lines), encoding="utf-8")
    return manifest


def main(argv=None):
    parser = argparse.ArgumentParser(
        description="Cut a lecture master into pause-aligned WAV chunks for Adobe Podcast.")
    parser.add_argument("sources", nargs="+", help="one or more source parts, in order")
    parser.add_argument("--out-dir", required=True)
    parser.add_argument("--chunks", type=int, default=DEFAULT_CHUNKS)
    parser.add_argument("--min-pause", type=float, default=DEFAULT_MIN_PAUSE)
    parser.add_argument("--noise", default=DEFAULT_NOISE)
    parser.add_argument("--window", type=float, default=DEFAULT_WINDOW)
    parser.add_argument("--master", help="joined master path (default: <out-dir>/master.mp4)")
    parser.add_argument("--reencode", action="store_true",
                        help="re-encode the join instead of stream copy")
    parser.add_argument("--plan-only", action="store_true",
                        help="write the plan and README without creating WAVs")
    parser.add_argument("--rebuild-master", action="store_true",
                        help="rebuild an existing joined master")
    parser.add_argument("--force", action="store_true",
                        help="overwrite existing WAV chunks and plan files")
    args = parser.parse_args(argv)

    out_dir = Path(args.out_dir).expanduser().resolve()
    out_dir.mkdir(parents=True, exist_ok=True)
    master = (Path(args.master).expanduser().resolve() if args.master
              else out_dir / DEFAULT_MASTER_NAME)

    try:
        sources = [Path(item).expanduser().resolve() for item in args.sources]
        master, duration = join_sources(
            sources, master, reencode=args.reencode, force=args.rebuild_master)
        silences = detect_silences(master, duration, noise=args.noise,
                                   min_pause=args.min_pause)
        splits = choose_chunk_points(duration, silences, args.chunks, args.window)
        items = plan_chunks(duration, splits, args.chunks)
        if args.plan_only:
            for item in items:
                item["file"] = chunk_name(item)
        else:
            write_chunks(master, items, out_dir, force=args.force)
        manifest = write_outputs(out_dir, master, sources, duration, args.chunks,
                                 args.min_pause, args.noise, items, splits)
    except (ChunkError, SplitError) as exc:
        print(f"adobe_chunks: {exc}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as exc:
        print(f"adobe_chunks: command failed: {exc}", file=sys.stderr)
        return 2

    print(f"master: {master} ({duration:.3f}s)")
    print(f"silences: {len(silences)}")
    for item in manifest:
        print(f"  chunk {item['index']}: {item['start']:.1f}-{item['end']:.1f}s "
              f"({item['duration'] / 60:.1f} min) -> {item['file']}")
    print(f"wrote {out_dir / 'audio_chunks.json'}, "
          f"{out_dir / 'chunks_plan.json'} and README")
    return 0


if __name__ == "__main__":
    sys.exit(main())
