#!/usr/bin/env python3
"""Join Adobe-enhanced WAV chunks from <folder>/enhanced/ using <folder>/audio_chunks.json.

    python3 audio_join.py library/<video>/adobe_audio_chunks

Adobe names the download <stem>-enhanced-v2.wav, so each manifest file is resolved by its stem
first, then by the exact name. Each part is resampled to 48 kHz mono and trimmed or padded to the
manifest duration, then the parts are concatenated. The output is <folder>/enhanced_full.wav.
"""
import argparse
import json
import subprocess
from pathlib import Path


def duration(path):
    return float(subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True, text=True, check=True,
    ).stdout.strip())


def find_enhanced(enhanced, name, suffix):
    """Resolve the Adobe download for one manifest entry."""
    exact = enhanced / name
    if exact.exists():
        return exact
    stem, ext = Path(name).stem, Path(name).suffix
    named = enhanced / f"{stem}{suffix}{ext}"
    if named.exists():
        return named
    matches = sorted(enhanced.glob(f"{stem}*{ext}"))
    if len(matches) == 1:
        return matches[0]
    if not matches:
        raise SystemExit(f"missing enhanced chunk for {name}: expected {named} or {exact}")
    raise SystemExit(f"ambiguous enhanced chunk for {name}: {', '.join(p.name for p in matches)}")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("folder")
    ap.add_argument("--suffix", default="-enhanced-v2",
                    help="Adobe filename suffix, default -enhanced-v2")
    a = ap.parse_args()
    folder = Path(a.folder).expanduser().resolve()
    manifest = json.loads((folder / "audio_chunks.json").read_text())
    enhanced = folder / "enhanced"
    parts = []
    for item in manifest:
        src = find_enhanced(enhanced, item["file"], a.suffix)
        part = folder / f"norm_{item['file']}"
        dur = float(item["duration"])
        subprocess.run([
            "ffmpeg", "-nostdin", "-v", "error", "-y", "-i", str(src),
            "-af", f"aresample=48000,apad=pad_dur={dur:.3f},atrim=duration={dur:.3f}",
            "-ac", "1", "-c:a", "pcm_s16le", str(part),
        ], check=True)
        parts.append(part)
        print(f"ok {src.name} -> {item['file']} {duration(part):.3f}s")
    concat_list = folder / "concat.txt"
    concat_list.write_text("".join(f"file '{p.name}'\n" for p in parts), encoding="utf-8")
    out = folder / "enhanced_full.wav"
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "concat", "-safe", "0",
        "-i", str(concat_list), "-c", "copy", str(out),
    ], check=True)
    expected = sum(float(item["duration"]) for item in manifest)
    actual = duration(out)
    print(f"wrote {out} ({actual:.3f}s, manifest {expected:.3f}s)")
    if abs(actual - expected) > 0.05:
        raise SystemExit(f"joined duration {actual:.3f}s does not match the manifest {expected:.3f}s")


if __name__ == "__main__":
    main()
