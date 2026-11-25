"""Profile, clip list and job building. No network, no API keys."""
from __future__ import annotations

import csv
import json
import subprocess
from datetime import date
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
PROFILES = BASE / "profiles"
ALLOWED_ASPECTS = {"9:16", "4:5", "3:4", "1:1"}
SOURCE_SUFFIXES = {".mp4", ".mov", ".mkv", ".m4v", ".mp3", ".wav", ".m4a", ".aac"}
RATIOS = {"9:16": 9 / 16, "4:5": 4 / 5, "3:4": 3 / 4, "1:1": 1.0}


class ConfigError(Exception):
    pass


def load_json(path):
    try:
        return json.loads(Path(path).read_text())
    except OSError as error:
        raise ConfigError(f"cannot read {path}: {error}")
    except json.JSONDecodeError as error:
        raise ConfigError(f"{path} is not valid JSON: {error}")


def profile_dir(name):
    if not name or "/" in name or name.startswith("."):
        raise ConfigError(f"bad profile name: {name!r}")
    path = PROFILES / name
    if not path.is_dir():
        raise ConfigError(f"unknown profile: {name}. Run ./forge.sh init {name}")
    return path


def load_profile(name):
    path = profile_dir(name)
    profile = load_json(path / "profile.json")
    style = load_json(path / "style.json")
    profile.setdefault("output", {})
    profile["output"].setdefault("aspect", "9:16")
    profile["output"].setdefault("speed", 1.0)
    return path, profile, style


def parse_time(value):
    text = str(value).strip()
    if not text:
        raise ConfigError("empty time value")
    if ":" in text:
        parts = text.split(":")
        if len(parts) == 2:
            hours, minutes, seconds = 0, parts[0], parts[1]
        elif len(parts) == 3:
            hours, minutes, seconds = parts
        else:
            raise ConfigError(f"bad time: {value}")
        try:
            return int(hours) * 3600 + int(minutes) * 60 + float(seconds)
        except ValueError:
            raise ConfigError(f"bad time: {value}")
    try:
        return float(text)
    except ValueError:
        raise ConfigError(f"bad time: {value}")


def slugify(text):
    out = []
    for char in str(text).lower():
        if char.isalnum():
            out.append(char)
        elif out and out[-1] != "-":
            out.append("-")
    return "".join(out).strip("-") or "clip"


def read_clips(path):
    path = Path(path)
    if not path.is_file():
        raise ConfigError(f"missing clips file: {path}")
    with path.open(newline="") as handle:
        rows = list(csv.DictReader(handle))
    if not rows:
        raise ConfigError("clips.csv is empty")
    required = {"id", "start", "end", "title"}
    missing = required - set(rows[0].keys())
    if missing:
        raise ConfigError(f"clips.csv is missing columns: {sorted(missing)}")

    clips, seen, spans = [], set(), []
    for row in rows:
        clip_id = (row.get("id") or "").strip()
        if not clip_id:
            raise ConfigError("clips.csv has a row without id")
        if clip_id in seen:
            raise ConfigError(f"duplicate clip id: {clip_id}")
        seen.add(clip_id)

        start = parse_time(row["start"])
        end = parse_time(row["end"])
        if end <= start:
            raise ConfigError(f"{clip_id}: end must be after start")

        title = (row.get("title") or "").strip()
        if not title:
            raise ConfigError(f"{clip_id}: title is required")

        aspect = (row.get("aspect") or "source").strip()
        if aspect not in ALLOWED_ASPECTS | {"source"}:
            raise ConfigError(f"{clip_id}: aspect must be source or one of {sorted(ALLOWED_ASPECTS)}")

        for other_start, other_end, other_id in spans:
            if start < other_end and other_start < end:
                raise ConfigError(f"{clip_id} overlaps {other_id}")
        spans.append((start, end, clip_id))

        clips.append(
            {
                "id": clip_id,
                "start": start,
                "end": end,
                "title": title,
                "subtitle": (row.get("subtitle") or "").strip(),
                "slug": (row.get("slug") or "").strip() or slugify(title),
                "aspect": aspect,
            }
        )
    return clips


def find_source(pdir, profile):
    configured = profile.get("source")
    if configured:
        path = Path(configured)
        if not path.is_absolute():
            path = pdir / path
        if not path.is_file():
            raise ConfigError(f"source not found: {path}")
        return path

    inbox = BASE / "inbox"
    files = []
    if inbox.is_dir():
        files = [item for item in sorted(inbox.iterdir()) if item.suffix.lower() in SOURCE_SUFFIXES]
    if not files:
        raise ConfigError("no source file in inbox/ and profile.source is empty")
    if len(files) > 1:
        raise ConfigError("more than one source in inbox/; set profile.source")
    return files[0]


def probe_duration(path):
    result = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0:
        raise ConfigError(f"ffprobe failed: {result.stderr.strip()}")
    try:
        return float(result.stdout.strip())
    except ValueError:
        raise ConfigError(f"cannot read duration of {path}")


def probe_size(path):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-select_streams", "v:0",
            "-show_entries", "stream=width,height", "-of", "csv=p=0", str(path),
        ],
        capture_output=True,
        text=True,
    )
    if result.returncode != 0 or not result.stdout.strip():
        raise ConfigError(f"cannot read size of {path}")
    width, height = result.stdout.strip().splitlines()[0].split(",")[:2]
    return int(width), int(height)


def resolve_aspect(profile, clips, source):
    explicit = {clip["aspect"] for clip in clips if clip["aspect"] != "source"}
    if len(explicit) > 1:
        raise ConfigError("clips.csv mixes aspects; render one aspect per run")
    if explicit:
        return explicit.pop()
    configured = profile.get("output", {}).get("aspect", "9:16")
    if configured != "source":
        if configured not in ALLOWED_ASPECTS:
            raise ConfigError(f"profile output.aspect must be source or {sorted(ALLOWED_ASPECTS)}")
        return configured
    width, height = probe_size(source)
    target = width / height
    return min(RATIOS, key=lambda key: abs(RATIOS[key] - target))


def ensure_sfx(pdir):
    folder = pdir / "assets" / "sfx"
    folder.mkdir(parents=True, exist_ok=True)
    specs = {
        "title": ["sine=frequency=880:duration=0.25", "afade=t=out:st=0.05:d=0.20"],
        "whoosh": ["anoisesrc=d=0.40:c=pink", "highpass=f=1200,afade=t=out:st=0.10:d=0.30"],
        "hit": ["sine=frequency=120:duration=0.18", "afade=t=out:st=0.02:d=0.15"],
        "riser": ["sine=frequency=300:duration=0.70", "afade=t=in:d=0.60"],
    }
    paths = {}
    for name, (source, effect) in specs.items():
        out = folder / f"{name}.wav"
        if not out.exists():
            subprocess.run(
                ["ffmpeg", "-nostdin", "-v", "error", "-y", "-f", "lavfi", "-i", source,
                 "-af", effect, "-ar", "48000", "-ac", "2", str(out)],
                check=True,
            )
        paths[name] = str(out)
    return paths


def build_job(profile_name, clip_id=None):
    pdir, profile, style = load_profile(profile_name)
    clips = read_clips(pdir / "clips.csv")
    if clip_id:
        clips = [clip for clip in clips if clip["id"] == clip_id]
        if not clips:
            raise ConfigError(f"no clip with id {clip_id}")

    source = find_source(pdir, profile)
    duration = probe_duration(source)
    for clip in clips:
        if clip["end"] > duration + 0.05:
            raise ConfigError(f"{clip['id']}: end {clip['end']:.2f}s is past the source duration {duration:.2f}s")

    aspect = resolve_aspect(profile, clips, source)
    aspect_key = aspect.replace(":", "x")
    today = date.today().isoformat()
    work = BASE / "work" / profile_name / today
    work.mkdir(parents=True, exist_ok=True)

    chapters = []
    for clip in clips:
        chapters.append(
            {
                "id": clip["id"],
                "start": clip["start"],
                "end": clip["end"],
                "yo": clip["title"],
                "en": clip["subtitle"] or clip["title"],
                "slug": clip["slug"],
                "kind": "clips",
                "deliver_name": f"{today}_{clip['id']}_{clip['slug']}_{aspect_key}",
            }
        )

    job = {
        "id": today,
        "source": str(source),
        "workdir": str(work),
        "aspect": aspect,
        "speed": float(profile.get("output", {}).get("speed", 1.0)),
        "grade": "clean",
        "label": (profile.get("series") or "").upper(),
        "outro_seconds": 0.0,
        "sfx": ensure_sfx(pdir),
        "chapters": chapters,
        "overwrite_deliver": False,
    }
    job_path = work / "job.json"
    job_path.write_text(json.dumps(job, indent=2) + "\n")
    return job_path, job, clips, profile, style
