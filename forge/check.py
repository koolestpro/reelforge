#!/usr/bin/env python3
"""Validate a render job before starting an expensive render."""
import argparse
import json
import math
import os
import shutil
import subprocess
import sys
from pathlib import Path

PIPELINE_DIR = Path(__file__).resolve().parent
if str(PIPELINE_DIR) not in sys.path:
    sys.path.insert(0, str(PIPELINE_DIR))

import cut_plan
try:
    import verify_scripture
except ImportError:
    verify_scripture = None


REQUIRED_SFX = ("title", "whoosh", "hit", "riser")
ASPECTS = {"9:16", "4:5", "3:4", "1:1"}


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _resolve(base, value):
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def _probe_source(source):
    result = subprocess.run(
        [
            "ffprobe", "-v", "error", "-show_entries",
            "format=duration:stream=codec_type", "-of", "json", str(source),
        ],
        check=True, capture_output=True, text=True,
    )
    info = json.loads(result.stdout)
    duration = float(info["format"]["duration"])
    streams = {stream.get("codec_type") for stream in info.get("streams", [])}
    return duration, streams


def validate_job(job_path):
    """Return (errors, warnings) without creating or modifying pipeline files."""
    path = Path(job_path).expanduser().resolve()
    errors, warnings = [], []

    try:
        job = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        return [f"cannot read job JSON: {exc}"], warnings

    if not isinstance(job, dict):
        return ["job JSON root must be an object"], warnings

    if verify_scripture is not None:
        scripture_errors, scripture_warnings = verify_scripture.check_job(job)
        errors.extend(scripture_errors)
        warnings.extend(scripture_warnings)

    base = path.parent
    source_value = job.get("source")
    source = None
    duration = None
    if not isinstance(source_value, str) or not source_value.strip():
        errors.append("source must be a non-empty path")
    else:
        source = _resolve(base, source_value)
        if not source.is_file():
            errors.append(f"source file does not exist: {source}")
        elif shutil.which("ffprobe") is None:
            errors.append("ffprobe is required but was not found on PATH")
        else:
            try:
                duration, streams = _probe_source(source)
                if "video" not in streams:
                    errors.append("source has no video stream")
                if "audio" not in streams:
                    errors.append("source has no audio stream")
                if duration <= 0:
                    errors.append("source duration must be greater than zero")
            except (OSError, subprocess.CalledProcessError, KeyError, ValueError, json.JSONDecodeError) as exc:
                errors.append(f"cannot probe source media: {exc}")

    if shutil.which("ffmpeg") is None:
        errors.append("ffmpeg is required but was not found on PATH")

    aspect = job.get("aspect", "9:16")
    if not isinstance(aspect, str) or aspect not in ASPECTS:
        errors.append(f"aspect must be one of: {', '.join(sorted(ASPECTS))}")
    if "label" in job and not isinstance(job["label"], str):
        errors.append("label must be a string")
    headline = job.get("intro_headline")
    if headline is not None and (
        not isinstance(headline, list)
        or len(headline) != 2
        or any(not isinstance(line, str) or not line.strip() for line in headline)
    ):
        errors.append("intro_headline must be an array of two non-empty strings")

    workdir_value = job.get("workdir", "work")
    workdir = None
    if not isinstance(workdir_value, str) or not workdir_value.strip():
        errors.append("workdir must be a non-empty path")
    else:
        workdir = _resolve(base, workdir_value)
        existing_parent = workdir
        while not existing_parent.exists() and existing_parent != existing_parent.parent:
            existing_parent = existing_parent.parent
        if existing_parent.exists() and not existing_parent.is_dir():
            errors.append(f"workdir parent is not a directory: {existing_parent}")
        elif existing_parent.exists() and not os.access(existing_parent, os.W_OK):
            errors.append(f"workdir parent is not writable: {existing_parent}")
        if workdir.exists() and not workdir.is_dir():
            errors.append(f"workdir is not a directory: {workdir}")
        if workdir.exists() and not os.access(workdir, os.W_OK):
            errors.append(f"workdir is not writable: {workdir}")
        aspect_file = workdir / "aspect.txt"
        if aspect_file.is_file() and aspect in ASPECTS:
            cached_aspect = aspect_file.read_text(encoding="utf-8").strip()
            if cached_aspect != aspect:
                errors.append(
                    f"workdir {workdir} is cached for aspect {cached_aspect!r}, not {aspect!r}; use another workdir"
                )

    for group, required in (("music", ("intro", "outro")), ("sfx", REQUIRED_SFX)):
        values = job.get(group)
        if not values:
            continue
        if not isinstance(values, dict):
            errors.append(f"{group} must be an object")
            continue
        for key in required:
            value = values.get(key)
            if not isinstance(value, str) or not value.strip():
                errors.append(f"{group}.{key} must be a non-empty file path")
                continue
            asset = _resolve(base, value)
            if not asset.is_file():
                errors.append(f"{group}.{key} file does not exist: {asset}")

    audio_source = job.get("audio_source")
    if audio_source is not None:
        if not isinstance(audio_source, str) or not audio_source.strip():
            errors.append("audio_source must be a non-empty path")
        elif not _resolve(base, audio_source).is_file():
            errors.append(f"audio_source file does not exist: {_resolve(base, audio_source)}")
    if "audio_enhanced" in job and not isinstance(job["audio_enhanced"], bool):
        errors.append("audio_enhanced must be true or false")
    title_anim = job.get("title_anim")
    if title_anim is not None:
        if not isinstance(title_anim, dict):
            errors.append("title_anim must be an object")
        elif "enabled" in title_anim and not isinstance(title_anim["enabled"], bool):
            errors.append("title_anim.enabled must be true or false")

    models = job.get("models", {})
    if not isinstance(models, dict):
        errors.append("models must be an object")
    else:
        yunet_value = models.get("yunet")
        if yunet_value is not None and not isinstance(yunet_value, str):
            errors.append("models.yunet must be a file path")
        yunet = (
            _resolve(base, yunet_value) if isinstance(yunet_value, str)
            else PIPELINE_DIR / "models" / "face_detection_yunet_2023mar.onnx"
        )
        if not yunet.is_file():
            warnings.append("optional face detector missing; exact-aspect sources render without it")
        for key in ("dfn3",):
            value = models.get(key)
            if value is not None and (
                not isinstance(value, str) or not _resolve(base, value).is_dir()
            ):
                errors.append(f"models.{key} must point to an existing directory")
        pydeps = models.get("pydeps")
        if pydeps is not None:
            if not isinstance(pydeps, list) or any(
                not isinstance(value, str) or not _resolve(base, value).is_dir()
                for value in pydeps
            ):
                errors.append("models.pydeps must be an array of existing directories")

    speed = job.get("speed", 1.0)
    if not _number(speed) or not 0.5 <= speed <= 2.0:
        errors.append("speed must be a finite number between 0.5 and 2.0")
    grade = job.get("grade", "warm")
    if grade not in ("warm", "clean", "v2"):
        errors.append("grade must be 'warm', 'clean' or 'v2'")
    overwrite_deliver = job.get("overwrite_deliver", False)
    if not isinstance(overwrite_deliver, bool):
        errors.append("overwrite_deliver must be true or false")

    chapters = job.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        errors.append("chapters must be a non-empty array")
        chapters = []

    ids = set()
    chapter_ranges = []
    required_chapter_fields = ("id", "start", "end", "yo", "en")

    def validate_card(card, where, chapter_duration):
        if not isinstance(card, dict):
            errors.append(f"{where} must be an object")
            return
        kind = card.get("kind", "quote")
        if kind not in ("quote", "list"):
            errors.append(f"{where}.kind must be 'quote' or 'list'")
        if "heading" in card and not isinstance(card["heading"], str):
            errors.append(f"{where}.heading must be a string")
        if card.get("source") is not None and not isinstance(card["source"], str):
            errors.append(f"{where}.source must be a string")
        card_start, card_end = card.get("start"), card.get("end")
        if (
            not _number(card_start) or not _number(card_end)
            or not 0 <= card_start < card_end
        ):
            errors.append(f"{where}.start and .end must be finite numbers with 0 <= start < end")
        elif chapter_duration is not None and card_end > chapter_duration:
            errors.append(f"{where}.end exceeds the chapter duration")
        if "y0" in card and not _number(card["y0"]):
            errors.append(f"{where}.y0 must be a finite number")
        if "bottom" in card and not _number(card["bottom"]):
            errors.append(f"{where}.bottom must be a finite number")
        if kind == "list":
            items = card.get("items")
            if not isinstance(items, list) or not items:
                errors.append(f"{where}.items must be a non-empty array for list cards")
            elif any(
                not (
                    isinstance(item, str) and item.strip()
                    or isinstance(item, dict)
                    and isinstance(item.get("text"), str)
                    and item["text"].strip()
                    and item.get("mark") in (None, "do", "dont")
                )
                for item in items
            ):
                errors.append(f"{where}.items must be non-empty strings or valid text/mark objects")
            style = card.get("style", "numbered")
            if style not in ("numbered", "dos", "donts", "bullets"):
                errors.append(f"{where}.style must be numbered, dos, donts, or bullets")
            item_times = card.get("item_times")
            if item_times is not None and (
                not isinstance(item_times, list)
                or not isinstance(items, list)
                or len(item_times) != len(items)
                or not all(_number(value) for value in item_times)
            ):
                errors.append(f"{where}.item_times must contain one finite number per list item")
            elif item_times is not None and _number(card_start) and _number(card_end):
                if any(value < card_start or value > card_end for value in item_times):
                    errors.append(f"{where}.item_times must lie inside the card time range")
        else:
            for lines_key in ("arabic", "english"):
                lines = card.get(lines_key, [])
                if not isinstance(lines, list) or any(
                    not isinstance(line, str) or not line.strip() for line in lines
                ):
                    errors.append(f"{where}.{lines_key} must be an array of non-empty strings")

    for index, chapter in enumerate(chapters):
        label = f"chapters[{index}]"
        if not isinstance(chapter, dict):
            errors.append(f"{label} must be an object")
            continue
        for key in required_chapter_fields:
            if key not in chapter:
                errors.append(f"{label}.{key} is required")

        chapter_id = chapter.get("id")
        if not isinstance(chapter_id, str) or not chapter_id.strip():
            errors.append(f"{label}.id must be a non-empty string")
        elif not all(char.isalnum() or char in "_-" for char in chapter_id):
            errors.append(f"{label}.id may contain only letters, numbers, '_' and '-'")
        elif chapter_id in ids:
            errors.append(f"{label}.id duplicates {chapter_id!r}")
        else:
            ids.add(chapter_id)

        for title_key in ("yo", "en"):
            if title_key in chapter and (
                not isinstance(chapter[title_key], str) or not chapter[title_key].strip()
            ):
                errors.append(f"{label}.{title_key} must be a non-empty string")

        start, end = chapter.get("start"), chapter.get("end")
        valid_times = _number(start) and _number(end) and 0 <= start < end
        if not valid_times:
            errors.append(f"{label}.start and .end must be finite numbers with 0 <= start < end")
        elif duration is not None and end > duration:
            errors.append(f"{label}.end ({end}) exceeds source duration ({duration:.3f}s)")
        chapter_duration = end - start if valid_times else None
        if valid_times:
            chapter_ranges.append((start, end, chapter_id or label))

        delay = chapter.get("audio_delay_ms", 0)
        if not _number(delay):
            errors.append(f"{label}.audio_delay_ms must be a finite number")

        skips = chapter.get("skip", [])
        if not isinstance(skips, list):
            errors.append(f"{label}.skip must be an array of [start, end] pairs")
        else:
            for skip_index, interval in enumerate(skips):
                if (
                    not isinstance(interval, list) or len(interval) != 2
                    or not all(_number(point) for point in interval)
                    or interval[0] >= interval[1]
                ):
                    errors.append(f"{label}.skip[{skip_index}] must be a [start, end] pair with start < end")
                elif valid_times and (interval[0] < start or interval[1] > end):
                    errors.append(f"{label}.skip[{skip_index}] must lie inside the chapter time range")

        teaser = chapter.get("teaser")
        if teaser is not None:
            if not isinstance(teaser, dict):
                errors.append(f"{label}.teaser must be an object")
            else:
                at, teaser_duration = teaser.get("at"), teaser.get("dur", 4.6)
                lines = teaser.get("lines")
                if (
                    not _number(at) or not _number(teaser_duration)
                    or at < 0 or teaser_duration <= 0
                ):
                    errors.append(f"{label}.teaser.at and .dur must be finite non-negative/positive numbers")
                elif chapter_duration is not None and at + teaser_duration > chapter_duration:
                    errors.append(f"{label}.teaser extends beyond the chapter duration")
                if not isinstance(lines, list) or not lines or any(
                    not isinstance(line, str) or not line.strip() for line in lines
                ):
                    errors.append(f"{label}.teaser.lines must be a non-empty array of non-empty strings")

        card = chapter.get("card")
        if card is not None:
            validate_card(card, f"{label}.card", chapter_duration)
        cards_list = chapter.get("cards")
        if cards_list is not None:
            if not isinstance(cards_list, list) or not cards_list:
                errors.append(f"{label}.cards must be a non-empty array of card objects")
            else:
                for ci, item in enumerate(cards_list):
                    validate_card(item, f"{label}.cards[{ci}]", chapter_duration)

    allow_overlap = job.get("allow_overlap", False)
    if not isinstance(allow_overlap, bool):
        errors.append("allow_overlap must be true or false")
        allow_overlap = False
    chapter_ranges.sort()
    for (a_start, a_end, a_id), (b_start, b_end, b_id) in zip(chapter_ranges, chapter_ranges[1:]):
        if b_start < a_end - 0.01 and not allow_overlap:
            errors.append(
                f"chapters {a_id!r} ({a_start}-{a_end}) and {b_id!r} ({b_start}-{b_end}) overlap; "
                "set allow_overlap only if the user approved it"
            )

    cut_plan_value = job.get("cut_plan")
    if cut_plan_value is not None:
        if not isinstance(cut_plan_value, str) or not cut_plan_value.strip():
            errors.append("cut_plan must be a non-empty path")
        else:
            plan_path = _resolve(base, cut_plan_value)
            if not plan_path.is_file():
                errors.append(f"cut_plan file does not exist: {plan_path}")
            else:
                try:
                    plan = json.loads(plan_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    errors.append(f"cannot read cut_plan: {exc}")
                    plan = None
                if not isinstance(plan, dict):
                    errors.append("cut_plan root must be an object")
                if isinstance(plan, dict):
                    approved = plan.get("approved_chapters") or []
                    if not isinstance(approved, list) or not approved:
                        errors.append("cut_plan has no approved_chapters; the user has not approved a render")
                        approved = []
                    expected_hash = cut_plan.approval_hash(plan)
                    if approved and not plan.get("approval_hash"):
                        errors.append("cut_plan has approved chapters but no approval_hash")
                    elif plan.get("approval_hash") and expected_hash != plan.get("approval_hash"):
                        errors.append("cut_plan approval_hash does not match the approved chapters")
                    plan_chapters = {item.get("id"): item for item in plan.get("chapters") or []
                                     if isinstance(item, dict)}
                    for chapter in chapters:
                        if not isinstance(chapter, dict):
                            continue
                        chapter_id = chapter.get("id")
                        if chapter_id not in approved:
                            errors.append(f"chapter {chapter_id!r} is not in cut_plan.approved_chapters")
                        planned = plan_chapters.get(chapter_id)
                        if not planned:
                            errors.append(f"chapter {chapter_id!r} is missing from cut_plan.chapters")
                            continue
                        for key in ("start", "end"):
                            if not _number(chapter.get(key)) or not _number(planned.get(key)):
                                continue
                            if abs(float(chapter[key]) - float(planned[key])) > 0.01:
                                errors.append(
                                    f"chapter {chapter_id!r} {key} does not match the approved cut plan "
                                    f"({chapter[key]} vs {planned[key]})"
                                )
                        for key in ("yo", "en"):
                            if key in chapter and key in planned and chapter[key] != planned[key]:
                                errors.append(f"chapter {chapter_id!r} {key} does not match the approved cut plan")

    if workdir is not None and workdir.is_dir():
        missing_manifest = []
        for chapter in chapters:
            if not isinstance(chapter, dict):
                continue
            chapter_id = chapter.get("id")
            manifest_path = workdir / f"{chapter_id}_prep.json"
            if not manifest_path.is_file():
                missing_manifest.append(str(chapter_id))
                continue
            try:
                manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                errors.append(f"cannot read {manifest_path}: {exc}")
                continue
            for key in ("start", "end"):
                if not _number(manifest.get(key)) or not _number(chapter.get(key)):
                    continue
                if abs(float(manifest[key]) - float(chapter[key])) > 0.01:
                    errors.append(
                        f"workdir {workdir} was prepped for {chapter_id} {key}={manifest[key]}, "
                        f"not {chapter[key]}; use a fresh workdir or remove the stale cache after approval"
                    )
            if isinstance(source, Path) and manifest.get("source"):
                if Path(manifest["source"]).resolve() != source.resolve():
                    errors.append(
                        f"workdir {workdir} was prepped from {manifest['source']}, not {source}"
                    )
                if manifest.get("source_size") is not None:
                    try:
                        if int(manifest["source_size"]) != source.stat().st_size:
                            errors.append(
                                f"workdir {workdir} was prepped from a different source size; use a fresh workdir"
                            )
                    except OSError:
                        pass
        if missing_manifest:
            warnings.append(
                f"workdir {workdir} has no prep manifest for {', '.join(missing_manifest)}; "
                "cache provenance is unknown"
            )

    outro = job.get("outro")
    if outro is not None and not isinstance(outro, dict):
        errors.append("outro must be an object")
    elif isinstance(outro, dict):
        lines = outro.get("lines")
        if not isinstance(outro.get("small", "THANK YOU"), str):
            errors.append("outro.small must be a string")
        if not isinstance(lines, list) or not lines or any(
            not isinstance(line, str) or not line.strip() for line in lines
        ):
            errors.append("outro.lines must be a non-empty array of non-empty strings")
        if outro.get("sub") is not None and not isinstance(outro["sub"], str):
            errors.append("outro.sub must be a string")

    if not errors:
        warnings.append("job passed preflight; a card is optional when no scriptural Arabic is spoken")
    return errors, warnings


def main(argv=None):
    parser = argparse.ArgumentParser(description="Preflight a render job without rendering.")
    parser.add_argument("job", help="path to job.json")
    args = parser.parse_args(argv)
    errors, warnings = validate_job(args.job)
    for warning in warnings:
        print(f"NOTE: {warning}")
    if errors:
        for error in errors:
            print(f"ERROR: {error}", file=sys.stderr)
        print(f"Doctor found {len(errors)} error(s).", file=sys.stderr)
        return 1
    print("Doctor passed.")
    return 0


if __name__ == "__main__":
    sys.exit(main())
