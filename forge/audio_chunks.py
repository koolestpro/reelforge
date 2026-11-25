#!/usr/bin/env python3
"""Export the full khutbah audio as mono 48 kHz WAV chunks for manual Adobe enhancement.

    python3 audio_chunks.py SOURCE --out-dir library/<video>/adobe_audio_chunks \
        --transcript library/<video>/transcript.raw.elevenlabs.json

The boundaries are chosen in long word gaps near 10:00, the second-khutbah pause at 23:44, and
33:00. A manifest and a short README are written beside the WAVs. The chunks have no overlap.
"""
import argparse
import json
import subprocess
from pathlib import Path


def duration(source):
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(source)],
        capture_output=True, text=True, check=True,
    ).stdout.strip())


def word_gaps(transcript):
    words = []
    for segment in transcript.get("segments") or []:
        for word in segment.get("words") or []:
            if isinstance(word.get("start"), (int, float)) and isinstance(word.get("end"), (int, float)):
                words.append((float(word["start"]), float(word["end"])))
    words.sort()
    gaps = []
    for (s1, e1), (s2, e2) in zip(words, words[1:]):
        if s2 > e1 and s2 - e1 >= 0.5:
            gaps.append((e1, s2))
    return gaps


def best_gap(gaps, target, window=40.0):
    candidates = [g for g in gaps if abs((g[0] + g[1]) / 2 - target) <= window]
    return max(candidates, key=lambda g: g[1] - g[0]) if candidates else None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("source")
    ap.add_argument("--out-dir", required=True)
    ap.add_argument("--transcript", required=True)
    ap.add_argument("--split", type=float, default=1424.814,
                    help="second-khutbah split inside the known 2.7 s pause")
    a = ap.parse_args()

    source = Path(a.source).expanduser().resolve()
    out = Path(a.out_dir).expanduser().resolve()
    out.mkdir(parents=True, exist_ok=True)
    transcript = json.loads(Path(a.transcript).expanduser().read_text())
    total = duration(source)
    gaps = word_gaps(transcript)
    targets = (600.0, 1200.0, 1980.0)
    cuts = [0.0]
    for target in targets:
        gap = best_gap(gaps, target)
        cuts.append((gap[0] + gap[1]) / 2 if gap else target)
    cuts.append(a.split)
    cuts.append(total)
    cuts = sorted(set(round(x, 3) for x in cuts))
    manifest = []
    for index, (start, end) in enumerate(zip(cuts, cuts[1:]), 1):
        dest = out / f"chunk_{index:02d}_{int(start):04d}-{int(end):04d}.wav"
        length = end - start
        subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-ss", f"{start:.3f}", "-t", f"{length:.3f}", "-i", str(source),
            "-vn", "-ac", "1", "-ar", "48000", "-c:a", "pcm_s16le", str(dest),
        ], check=True)
        manifest.append({
            "index": index,
            "start": round(start, 3),
            "end": round(end, 3),
            "duration": round(length, 3),
            "file": dest.name,
        })
        print(f"chunk {index}: {start:.1f}-{end:.1f}s ({length/60:.1f} min) -> {dest.name}")

    (out / "audio_chunks.json").write_text(
        json.dumps(manifest, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    (out / "README.md").write_text(
        "# Adobe audio chunks\n\n"
        "Upload each WAV to Adobe Podcast Enhance. Download the enhanced WAV with the same name\n"
        "into an `enhanced/` subfolder inside this folder, keeping the same order. Then run:\n\n"
        "    python3 khutba/pipeline/audio_join.py <this folder>\n\n"
        "The chunks have no overlap and are cut in pauses, so the joined master keeps source time.\n",
        encoding="utf-8",
    )
    print(f"wrote {out / 'audio_chunks.json'} and README")


if __name__ == "__main__":
    main()
