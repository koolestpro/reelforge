#!/usr/bin/env python3
"""Burn reviewed captions into delivered clips.

Input is a source-time SRT. Each clip already has a timemap written by the renderer, so cues are
mapped through the real cuts and speed rather than divided by speed alone.

    ./forge.sh captions --profile ahmed
    ./forge.sh captions --profile ahmed --id c1
"""
from __future__ import annotations

import json
import re
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))

import config as cfg  # noqa: E402
import map_captions  # noqa: E402

ASPECTS = {
    "9:16": (1080, 1920),
    "4:5": (1080, 1350),
    "3:4": (1080, 1440),
    "1:1": (1080, 1080),
}
TIME_RE = re.compile(r"(\d+):(\d{2}):(\d{2})[,.](\d{1,3})")


def parse_timestamp(value):
    match = TIME_RE.search(value)
    if not match:
        raise ValueError(f"bad SRT timestamp: {value}")
    hours, minutes, seconds, millis = match.groups()
    return (
        int(hours) * 3600
        + int(minutes) * 60
        + int(seconds)
        + int(millis.ljust(3, "0")[:3]) / 1000
    )


def parse_srt(text):
    text = text.replace("\r\n", "\n").replace("\r", "\n").strip()
    if not text:
        return []
    cues = []
    for block in re.split(r"\n\s*\n", text):
        lines = [line for line in block.split("\n") if line.strip()]
        if not lines:
            continue
        index = 1 if lines[0].strip().isdigit() else 0
        if index >= len(lines) or "-->" not in lines[index]:
            continue
        start_text, end_text = lines[index].split("-->")[:2]
        body = " ".join(lines[index + 1:]).strip()
        if not body:
            continue
        start = parse_timestamp(start_text)
        end = parse_timestamp(end_text)
        if end <= start:
            continue
        cues.append({"start": start, "end": end, "text": body})
    return cues


def format_timestamp(seconds):
    seconds = max(0.0, float(seconds))
    millis = int(round(seconds * 1000))
    hours, millis = divmod(millis, 3_600_000)
    minutes, millis = divmod(millis, 60_000)
    secs, millis = divmod(millis, 1000)
    return f"{hours:02d}:{minutes:02d}:{secs:02d},{millis:03d}"


def to_srt(captions):
    blocks = []
    for index, cue in enumerate(captions, 1):
        blocks.append(
            f"{index}\n{format_timestamp(cue['start'])} --> {format_timestamp(cue['end'])}\n{cue['text']}\n"
        )
    return "\n".join(blocks)


def build_caption_doc(cues, chapter, timemap, height):
    start = float(chapter["start"])
    end = float(chapter["end"])
    clip_duration = float(timemap.get("clip_duration") or 0)

    selected = []
    for cue in cues:
        if cue["end"] <= start or cue["start"] >= end:
            continue
        selected.append(
            {
                "source_start": max(cue["start"], start),
                "source_end": min(cue["end"], end),
                "text": cue["text"],
            }
        )

    spec = {"chapter_start": start, "captions": selected}
    doc = map_captions.map_spec(spec, timemap)
    for cue in doc["captions"]:
        cue["start"] = max(0.0, min(float(cue["start"]), clip_duration or cue["start"]))
        cue["end"] = max(cue["start"] + 0.2, min(float(cue["end"]), clip_duration or cue["end"]))
    doc["y_bottom"] = int(height * 0.88)
    doc["font_size"] = max(28, int(height * 0.023))
    return doc


def latest_job(profile):
    jobs = sorted((BASE / "work" / profile).glob("*/job.json"))
    if not jobs:
        raise cfg.ConfigError(f"no rendered job for {profile}; run ./forge.sh render first")
    return json.loads(jobs[-1].read_text())


def run(profile, srt_path=None, clip_id=None):
    pdir, _profile, _style = cfg.load_profile(profile)
    job = latest_job(profile)

    srt = Path(srt_path) if srt_path else pdir / "captions.srt"
    if not srt.is_file():
        raise cfg.ConfigError(f"missing captions file: {srt}")
    cues = parse_srt(srt.read_text())
    if not cues:
        raise cfg.ConfigError(f"no usable cues in {srt}")

    width, height = ASPECTS.get(job.get("aspect", "9:16"), ASPECTS["9:16"])
    workdir = Path(job["workdir"])
    deliver = BASE / "deliver" / profile / job["id"]
    clips_dir = deliver / "clips"
    captions_dir = deliver / "captions"
    captions_dir.mkdir(parents=True, exist_ok=True)

    manifest_path = deliver / "manifest.json"
    manifest = json.loads(manifest_path.read_text()) if manifest_path.is_file() else {"clips": []}
    by_id = {item.get("id"): item for item in manifest.get("clips", [])}

    done = 0
    for chapter in job["chapters"]:
        if clip_id and chapter["id"] != clip_id:
            continue
        timemap_path = workdir / f"{chapter['id']}_timemap.json"
        clean = workdir / "out" / f"short_{chapter['id']}.mp4"
        if not timemap_path.is_file() or not clean.is_file():
            raise cfg.ConfigError(
                f"no clean render or timemap for {chapter['id']}; run ./forge.sh render first"
            )
        timemap = json.loads(timemap_path.read_text())
        doc = build_caption_doc(cues, chapter, timemap, height)
        if not doc["captions"]:
            print(f"{chapter['id']}: no cues in range, skipped")
            continue

        name = chapter["deliver_name"]
        caption_json = workdir / f"{chapter['id']}.captions.json"
        caption_json.write_text(json.dumps(doc, ensure_ascii=False, indent=2) + "\n")

        target = clips_dir / f"{name}.mp4"
        temp = clips_dir / f"{name}.captioned.tmp.mp4"
        subprocess.run(
            [sys.executable, str(HERE / "subtitles.py"), str(clean), str(caption_json), str(temp)],
            check=True,
        )
        temp.replace(target)

        sidecar = captions_dir / f"{name}.srt"
        sidecar.write_text(to_srt(doc["captions"]))

        record = by_id.setdefault(chapter["id"], {"id": chapter["id"], "title": chapter["yo"]})
        record["file"] = str(target.relative_to(deliver).as_posix())
        record["captions"] = True
        record["caption_file"] = str(sidecar.relative_to(deliver).as_posix())
        done += 1
        print(f"{chapter['id']}: {len(doc['captions'])} cues -> {target.name}")

    if not done:
        raise cfg.ConfigError("no clips were captioned")
    manifest["clips"] = list(by_id.values())
    manifest["captions"] = True
    manifest_path.write_text(json.dumps(manifest, indent=2) + "\n")
    print(f"captioned {done} clip(s) from {srt}")
    return 0


def main(argv=None):
    parser = argparse.ArgumentParser(description="Burn source-time captions into delivered clips.")
    parser.add_argument("--profile", required=True)
    parser.add_argument("--srt", default=None)
    parser.add_argument("--id", default=None)
    args = parser.parse_args(argv)
    try:
        return run(args.profile, args.srt, args.id)
    except (cfg.ConfigError, ValueError) as error:
        print(f"error: {error}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
