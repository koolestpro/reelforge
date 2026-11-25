#!/usr/bin/env python3
"""Map source-time captions onto a rendered clip timeline through a timemap.

    python3 map_captions.py library/<video>/subtitles/c2.en.source.json \
        --out library/<video>/subtitles/c2.en.json

The source JSON has `chapter_start`, `timemap` and `captions` with `source_start` and `source_end`.
The timemap has `keep` segments in chapter-local seconds. The output adds `start` and `end` in clip
seconds. The first cue is held until 0.9 s so it clears the opening thumbnail.
"""
import argparse
import json
from pathlib import Path

MIN_FIRST_START = 0.9
MIN_FIRST_LENGTH = 0.9


def map_time(local, keep, speed=1.0):
    offset = 0.0
    speed = speed or 1.0
    for start, end in keep:
        if local < start:
            return offset / speed
        if local <= end:
            return (offset + local - start) / speed
        offset += end - start
    return offset / speed


def resolve(value):
    path = Path(value).expanduser()
    return path if path.is_absolute() else Path.cwd() / path


def map_spec(spec, timemap):
    keep = timemap.get("keep") or []
    speed = float(timemap.get("speed") or 1.0)
    chapter_start = float(spec["chapter_start"])
    out = []
    for index, item in enumerate(spec.get("captions") or [], 1):
        local_start = float(item["source_start"]) - chapter_start
        local_end = float(item["source_end"]) - chapter_start
        start = round(map_time(local_start, keep, speed), 3)
        end = round(map_time(local_end, keep, speed), 3)
        if index == 1:
            start = max(start, MIN_FIRST_START)
            end = max(end, start + MIN_FIRST_LENGTH)
        if end <= start:
            end = round(start + 0.8, 3)
        out.append({
            "id": item.get("id", index),
            "source_start": item["source_start"],
            "source_end": item["source_end"],
            "start": start,
            "end": end,
            "text": item["text"],
            "flag": item.get("flag", ""),
        })
    for current, following in zip(out, out[1:]):
        if current["end"] > following["start"] - 0.08:
            current["end"] = round(max(current["start"] + 0.2, following["start"] - 0.08), 3)
    result = {key: value for key, value in spec.items() if key not in ("captions", "style")}
    result.update(spec.get("style") or {})
    result["captions"] = out
    return result


def main(argv=None):
    parser = argparse.ArgumentParser(description="Map source captions to clip time.")
    parser.add_argument("source", help="source-time captions JSON")
    parser.add_argument("--out", required=True)
    parser.add_argument("--timemap", help="override the timemap path")
    args = parser.parse_args(argv)

    source = resolve(args.source)
    spec = json.loads(source.read_text(encoding="utf-8"))
    timemap_path = resolve(args.timemap or spec["timemap"])
    timemap = json.loads(timemap_path.read_text(encoding="utf-8"))
    result = map_spec(spec, timemap)
    out = resolve(args.out)
    out.write_text(json.dumps(result, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"wrote {out} ({len(result['captions'])} captions)")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
