"""Collect rendered clips into a dated deliver folder with a manifest and queue CSV."""
from __future__ import annotations

import csv
import hashlib
import json
import shutil
from pathlib import Path


def sha256(path):
    digest = hashlib.sha256()
    with open(path, "rb") as handle:
        for chunk in iter(lambda: handle.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def collect(target, job, profile_name):
    target = Path(target)
    out = Path(job["workdir"]) / "out"
    clips_dir = target / "clips"
    clips_dir.mkdir(parents=True, exist_ok=True)

    records = []
    for chapter in job["chapters"]:
        clip_id = chapter["id"]
        video = out / f"short_{clip_id}.mp4"
        thumb = out / f"thumb_{clip_id}.jpg"
        if not video.exists():
            raise RuntimeError(f"renderer did not produce {video}")
        name = chapter["deliver_name"]
        video_dest = clips_dir / f"{name}.mp4"
        thumb_dest = clips_dir / f"{name}.jpg"
        shutil.copy2(video, video_dest)
        if thumb.exists():
            shutil.copy2(thumb, thumb_dest)
        records.append(
            {
                "id": chapter["id"],
                "title": chapter["yo"],
                "start": chapter["start"],
                "end": chapter["end"],
                "file": video_dest.relative_to(target).as_posix(),
                "thumbnail": thumb_dest.relative_to(target).as_posix() if thumb.exists() else None,
                "sha256": sha256(video_dest),
            }
        )

    manifest = {
        "profile": profile_name,
        "date": job.get("id"),
        "source": job.get("source"),
        "aspect": job.get("aspect"),
        "speed": job.get("speed"),
        "clips": records,
    }
    (target / "manifest.json").write_text(json.dumps(manifest, indent=2) + "\n")

    with (target / "queue.csv").open("w", newline="") as handle:
        writer = csv.DictWriter(handle, fieldnames=["id", "title", "file", "thumbnail", "caption"])
        writer.writeheader()
        for record in records:
            writer.writerow(
                {
                    "id": record["id"],
                    "title": record["title"],
                    "file": record["file"],
                    "thumbnail": record["thumbnail"] or "",
                    "caption": record["title"],
                }
            )
    return manifest, target
