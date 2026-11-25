#!/usr/bin/env python3
"""Verify an editorial cut plan before any render.

This tool does not call an LLM and does not render. It reads a structured cut plan, the raw
ASR JSON and an optional pause map, then reports what the timings actually support:

  - word or segment boundary evidence at every proposed start and end
  - whether a boundary falls inside a word
  - the nearest pause before and after each boundary
  - opening and closing words
  - boundary confidence and review flags
  - overlaps, invalid ranges and source-duration violations
  - declared Qur'an/hadith ranges that a cut would break
  - teaser and short-post length checks

The plan is a draft until the user approves it. This tool never marks a plan approved.

    python3 cut_plan.py verify library/<video>/cut_plan.json \
      --transcript library/<video>/transcript.raw.elevenlabs.json \
      --pauses 0-analysis/<date>/pauses.json \
      --json-out library/<video>/cut_plan.verified.json \
      --md-out library/<video>/cut_plan.review.md

    python3 cut_plan.py verify ... --previews work/<video>_boundary_previews
    python3 cut_plan.py verify ... --strict --require-approved
"""
import argparse
import hashlib
import json
import math
import re
import subprocess
import sys
from pathlib import Path

WORD_EPS = 0.02
PAUSE_EDGE = 0.08
MIN_CHAPTER_SECONDS = 5.0
DEFAULT_TEASER_MAX = 10.0
DEFAULT_SHORT_MIN = 20.0
DEFAULT_SHORT_MAX = 35.0
SENTENCE_END = re.compile(r"[.!?。！？]\s*$")


class PlanError(Exception):
    pass


def _number(value):
    return isinstance(value, (int, float)) and not isinstance(value, bool) and math.isfinite(value)


def _load(path):
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise PlanError(f"cannot read {path}: {exc}") from exc


def _resolve(base, value):
    path = Path(value).expanduser()
    return path.resolve() if path.is_absolute() else (base / path).resolve()


def canonical_approved(plan):
    """Canonical JSON for the chapters the user has approved."""
    approved = plan.get("approved_chapters") or []
    if not isinstance(approved, list) or any(not isinstance(x, str) for x in approved):
        return None
    if not approved:
        return None
    ids = set(approved)
    items = []
    for chapter in plan.get("chapters") or []:
        if isinstance(chapter, dict) and chapter.get("id") in ids:
            items.append(_hashable_chapter(chapter))
    for key in ("teasers", "short_posts"):
        for entry in plan.get(key) or []:
            if isinstance(entry, dict) and entry.get("approved") is True:
                items.append({"kind": key, **_hashable_entry(entry)})
    return json.dumps(items, sort_keys=True, ensure_ascii=False, separators=(",", ":"))


def _hashable_chapter(chapter):
    keys = ("id", "start", "end", "yo", "en", "topic", "why", "confidence",
            "opening_complete", "closing_complete", "boundary_confidence",
            "review_required", "uncertainties", "scripture", "card", "cards",
            "title_style", "title_small")
    return {key: chapter[key] for key in keys if key in chapter}


def _hashable_entry(entry):
    keys = ("id", "chapter_id", "start", "end", "kind", "why", "scripture",
            "opening_complete", "closing_complete", "approved")
    return {key: entry[key] for key in keys if key in entry}


def approval_hash(plan):
    canonical = canonical_approved(plan)
    if canonical is None:
        return None
    return hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def check_approval(plan, require=False):
    errors = []
    approved = plan.get("approved_chapters")
    if not isinstance(approved, list) or any(not isinstance(x, str) for x in approved):
        approved = []
    if require and not approved:
        errors.append("cut plan has no approved_chapters")
    expected = approval_hash(plan)
    stored = plan.get("approval_hash")
    if stored and expected != stored:
        errors.append("approval_hash does not match the approved chapter content; the plan changed after approval")
    if require and approved and not stored:
        errors.append("approved cut plan has no approval_hash")
    return approved, expected, errors


def word_stream(segments):
    """Flatten word timings where present, else use whole segments as boundary units."""
    words = []
    for index, segment in enumerate(segments):
        if not isinstance(segment, dict):
            continue
        raw_words = segment.get("words") or []
        timed = []
        if isinstance(raw_words, list):
            for word in raw_words:
                if not isinstance(word, dict):
                    continue
                word_text = word.get("text") or word.get("word")
                if (isinstance(word_text, str) and word_text.strip()
                        and _number(word.get("start")) and _number(word.get("end"))
                        and float(word["end"]) > float(word["start"])):
                    timed.append({
                        "text": word_text.strip(),
                        "start": round(float(word["start"]), 3),
                        "end": round(float(word["end"]), 3),
                        "segment": index,
                        "word_timed": True,
                    })
        if timed:
            words.extend(timed)
        else:
            text = segment.get("text")
            if (isinstance(text, str) and text.strip()
                    and _number(segment.get("start")) and _number(segment.get("end"))
                    and float(segment["end"]) > float(segment["start"])):
                words.append({
                    "text": text.strip(),
                    "start": round(float(segment["start"]), 3),
                    "end": round(float(segment["end"]), 3),
                    "segment": index,
                    "word_timed": False,
                })
    words.sort(key=lambda item: (item["start"], item["end"]))
    return words


def word_gaps(words, min_pause=0.4):
    """Pause candidates from gaps between timed words, when no pause map is available."""
    gaps = []
    for previous, following in zip(words, words[1:]):
        gap = following["start"] - previous["end"]
        if gap >= min_pause:
            gaps.append((previous["end"], following["start"]))
    return gaps


def merge_pauses(pauses, gaps):
    merged = sorted({(round(float(a), 3), round(float(b), 3))
                     for a, b in list(pauses) + list(gaps) if b > a})
    out = []
    for start, end in merged:
        if out and start <= out[-1][1] + 0.01:
            out[-1] = (out[-1][0], max(out[-1][1], end))
        else:
            out.append((start, end))
    return out


def _nearest_pause(pauses, at):
    inside = None
    before = None
    after = None
    for start, end in pauses:
        if start - PAUSE_EDGE <= at <= end + PAUSE_EDGE:
            if inside is None or (end - start) > (inside[1] - inside[0]):
                inside = (start, end)
        if end <= at + PAUSE_EDGE:
            if before is None or end > before[1]:
                before = (start, end)
        if start >= at - PAUSE_EDGE:
            if after is None or start < after[0]:
                after = (start, end)
    return inside, before, after


def boundary_evidence(at, side, words, pauses, overall_timing):
    """Return the evidence at one proposed start or end."""
    inside = next((word for word in words
                   if word["word_timed"]
                   and word["start"] + WORD_EPS < at < word["end"] - WORD_EPS), None)
    at_start = any(word["word_timed"] and abs(word["start"] - at) <= 0.03 for word in words)
    at_end = any(word["word_timed"] and abs(word["end"] - at) <= 0.03 for word in words)
    pause_in, pause_before, pause_after = _nearest_pause(pauses, at)

    if inside:
        opening = [word["text"] for word in words if word["start"] >= inside["start"]][:10]
        closing = [word["text"] for word in words if word["end"] <= inside["end"]][-10:]
        complete_open = "no"
        complete_close = "no"
        reason = f"boundary falls inside word {inside['text']!r}"
    else:
        if side == "start":
            opening = [word["text"] for word in words if word["end"] > at + WORD_EPS][:10]
            previous = [word for word in words if word["end"] <= at + WORD_EPS][-1:]
            closing = [word["text"] for word in previous]
            complete_open = _complete_open(at, opening, previous, pause_in)
            complete_close = "n/a"
        else:
            closing = [word["text"] for word in words if word["start"] < at - WORD_EPS][-10:]
            following = [word for word in words if word["start"] >= at - WORD_EPS][:1]
            opening = [word["text"] for word in following]
            complete_open = "n/a"
            complete_close = _complete_close(at, closing, following, pause_in)
        reason = "on a pause" if pause_in else ("on a word edge" if at_start or at_end else "not on a word edge or pause")

    if inside:
        confidence = "needs_review"
    elif overall_timing == "estimated":
        confidence = "approximate"
    elif at_start or at_end:
        confidence = "exact" if (pause_in and pause_in[1] - pause_in[0] >= 0.6) else "approximate"
    elif pause_in:
        confidence = "approximate"
    else:
        confidence = "needs_review"

    if overall_timing == "estimated" and confidence == "exact":
        confidence = "approximate"

    return {
        "at": round(at, 3),
        "side": side,
        "inside_word": bool(inside),
        "inside_word_text": inside["text"] if inside else None,
        "at_word_start": at_start,
        "at_word_end": at_end,
        "in_pause": bool(pause_in),
        "pause_before": _pause_dict(pause_before),
        "pause_after": _pause_dict(pause_after),
        "pause_at_boundary": _pause_dict(pause_in),
        "opening_words": opening,
        "closing_words": closing,
        "opening_complete": complete_open,
        "closing_complete": complete_close,
        "confidence": confidence,
        "reason": reason,
    }


def _pause_dict(pause):
    if not pause:
        return None
    return {"start": round(pause[0], 3), "end": round(pause[1], 3),
            "duration": round(pause[1] - pause[0], 3)}


def _complete_open(at, opening, previous, pause_in):
    if not opening:
        return "unknown"
    if previous and SENTENCE_END.search(previous[-1]["text"]):
        return "likely"
    if pause_in and pause_in[1] - pause_in[0] >= 0.4:
        return "likely"
    return "unknown"


def _complete_close(at, closing, following, pause_in):
    if not closing:
        return "unknown"
    if SENTENCE_END.search(closing[-1]):
        return "likely"
    if pause_in and pause_in[1] - pause_in[0] >= 0.8:
        return "likely"
    if following and SENTENCE_END.search(closing[-1]):
        return "likely"
    return "unknown"


def _add_issue(issues, severity, message):
    issues.append({"severity": severity, "message": message})


def verify_chapter(chapter, words, pauses, duration, timing, strict):
    label = str(chapter.get("id") or "chapter")
    issues = []
    start, end = chapter.get("start"), chapter.get("end")
    out = {"id": label}
    if not _number(start) or not _number(end):
        _add_issue(issues, "error", f"{label}: start and end must be finite numbers")
        out["issues"] = issues
        out["review_required"] = True
        return out
    start, end = float(start), float(end)
    if start < 0:
        _add_issue(issues, "error", f"{label}: start is negative")
    if end <= start:
        _add_issue(issues, "error", f"{label}: end must be after start")
    if duration and end > duration + 0.05:
        _add_issue(issues, "error", f"{label}: end {end:.2f}s exceeds source duration {duration:.2f}s")
    if end > start and end - start < MIN_CHAPTER_SECONDS:
        _add_issue(issues, "warning", f"{label}: chapter is only {end - start:.2f}s")

    start_ev = boundary_evidence(start, "start", words, pauses, timing)
    end_ev = boundary_evidence(end, "end", words, pauses, timing)
    out.update({
        "start": round(start, 3),
        "end": round(end, 3),
        "duration": round(max(0.0, end - start), 3),
        "start_evidence": start_ev,
        "end_evidence": end_ev,
        "opening_words": start_ev["opening_words"][:8],
        "closing_words": end_ev["closing_words"][-8:],
        "opening_complete": start_ev["opening_complete"],
        "closing_complete": end_ev["closing_complete"],
        "boundary_confidence": _combine_confidence(start_ev, end_ev),
        "review_required": False,
    })

    for evidence, side in ((start_ev, "start"), (end_ev, "end")):
        if evidence["inside_word"]:
            _add_issue(issues, "error" if strict else "warning",
                       f"{label}: {side} falls inside the word {evidence['inside_word_text']!r}")
        if evidence["confidence"] == "needs_review":
            _add_issue(issues, "warning", f"{label}: {side} boundary needs review ({evidence['reason']})")

    declared_open = chapter.get("opening_complete")
    declared_close = chapter.get("closing_complete")
    if declared_open is True and start_ev["opening_complete"] == "no":
        _add_issue(issues, "warning", f"{label}: plan says opening is complete, but evidence says it is not")
    if declared_close is True and end_ev["closing_complete"] == "no":
        _add_issue(issues, "warning", f"{label}: plan says closing is complete, but evidence says it is not")

    uncertainties = chapter.get("uncertainties") or []
    if uncertainties and not isinstance(uncertainties, list):
        _add_issue(issues, "error", f"{label}: uncertainties must be a list")
    else:
        for uncertainty in uncertainties:
            if isinstance(uncertainty, str) and uncertainty.strip():
                _add_issue(issues, "warning", f"{label}: uncertainty: {uncertainty.strip()}")

    _check_scripture(chapter, label, issues, strict)
    out["issues"] = issues
    out["review_required"] = bool(issues) or out["boundary_confidence"] != "exact"
    return out


def _combine_confidence(start_ev, end_ev):
    order = {"needs_review": 2, "approximate": 1, "exact": 0}
    return max((start_ev["confidence"], end_ev["confidence"]), key=lambda x: order[x])


def _check_scripture(entry, label, issues, strict):
    scripture = entry.get("scripture")
    card = entry.get("card")
    if card and not scripture:
        _add_issue(issues, "warning", f"{label}: card declared without a scripture range")
        return
    if not scripture:
        return
    if not isinstance(scripture, dict):
        _add_issue(issues, "error", f"{label}: scripture must be an object")
        return
    if scripture.get("present") is False:
        return
    start, end = scripture.get("start"), scripture.get("end")
    if not _number(start) or not _number(end) or float(end) <= float(start):
        _add_issue(issues, "error", f"{label}: scripture.start and .end must be finite numbers with start < end")
        return
    if _number(entry.get("start")) and _number(entry.get("end")):
        if float(entry["start"]) > float(start) + 0.01 or float(entry["end"]) < float(end) - 0.01:
            _add_issue(issues, "error",
                       f"{label}: cut {entry['start']}-{entry['end']} does not cover the declared scripture {start}-{end}")
    if scripture.get("keep_whole") is False:
        _add_issue(issues, "warning", f"{label}: scripture is declared but keep_whole is false")
    if not scripture.get("source"):
        _add_issue(issues, "warning", f"{label}: scripture has no source")
    if not scripture.get("verified_by"):
        _add_issue(issues, "error" if strict else "warning",
                   f"{label}: scripture has not been verified by a human")
    translation_end = scripture.get("translation_end")
    if translation_end is not None:
        if not _number(translation_end):
            _add_issue(issues, "error", f"{label}: scripture.translation_end must be a number")
        elif _number(entry.get("end")) and float(entry["end"]) < float(translation_end) - 0.01:
            _add_issue(issues, "error",
                       f"{label}: cut ends before the declared translation/explanation at {translation_end}")


def verify_entries(entries, kind, words, pauses, duration, timing, strict, allow_overlap):
    verified = []
    errors, warnings = [], []
    ids = set()
    for entry in entries:
        if not isinstance(entry, dict):
            errors.append(f"{kind}: every entry must be an object")
            continue
        entry_id = entry.get("id")
        if not isinstance(entry_id, str) or not entry_id.strip():
            errors.append(f"{kind}: every entry needs a non-empty id")
        elif entry_id in ids:
            errors.append(f"{kind}: duplicate id {entry_id!r}")
        else:
            ids.add(entry_id)
        verified.append(verify_chapter(entry, words, pauses, duration, timing, strict))
    verified_originals = [entry for entry in entries if isinstance(entry, dict)]

    for item in verified:
        for issue in item["issues"]:
            (errors if issue["severity"] == "error" else warnings).append(issue["message"])

    ranges = [(item["start"], item["end"], item["id"]) for item in verified
              if _number(item.get("start")) and _number(item.get("end")) and item["end"] > item["start"]]
    ranges.sort()
    for (a_start, a_end, a_id), (b_start, b_end, b_id) in zip(ranges, ranges[1:]):
        if b_start < a_end - 0.01:
            message = f"{kind}: {a_id} ({a_start:.2f}-{a_end:.2f}) overlaps {b_id} ({b_start:.2f}-{b_end:.2f})"
            if allow_overlap:
                warnings.append(message)
            else:
                errors.append(message)

    if kind == "teaser":
        for item, original in zip(verified, verified_originals):
            length = item.get("duration") or 0.0
            max_seconds = original.get("max_seconds", DEFAULT_TEASER_MAX)
            scripture = original.get("scripture") or {}
            if length > float(max_seconds) and not scripture.get("present"):
                message = f"teaser {item['id']}: {length:.2f}s exceeds {max_seconds}s and has no scripture exception"
                (errors if strict else warnings).append(message)
            if length > 90:
                warnings.append(f"teaser {item['id']}: {length:.2f}s is unusually long")
    elif kind == "short_post":
        for item, original in zip(verified, verified_originals):
            length = item.get("duration") or 0.0
            if length < DEFAULT_SHORT_MIN or length > DEFAULT_SHORT_MAX:
                warnings.append(
                    f"short_post {item['id']}: {length:.2f}s is outside the usual "
                    f"{DEFAULT_SHORT_MIN:.0f}-{DEFAULT_SHORT_MAX:.0f}s range"
                )

    return verified, errors, warnings


def verify_plan(plan, transcript, pauses, source_duration=None, strict=False,
                allow_overlap=False, require_approved=False):
    if not isinstance(plan, dict):
        raise PlanError("cut plan root must be an object")
    chapters = plan.get("chapters")
    if not isinstance(chapters, list) or not chapters:
        raise PlanError("cut plan needs a non-empty chapters array")
    if not isinstance(transcript, dict) or not isinstance(transcript.get("segments"), list):
        raise PlanError("transcript must be the uniform ASR JSON with a segments array")

    timing = transcript.get("timing") or "estimated"
    words = word_stream(transcript["segments"])
    gaps = word_gaps(words)
    supplied_pauses = list(pauses or [])
    pauses = merge_pauses(supplied_pauses, gaps)
    duration = source_duration
    if duration is None:
        duration = transcript.get("duration") or plan.get("duration")
    if not _number(duration) or float(duration) <= 0:
        duration = None
    else:
        duration = float(duration)

    verified, errors, warnings = verify_entries(
        chapters, "chapter", words, pauses, duration, timing, strict, allow_overlap)
    teasers, teaser_errors, teaser_warnings = verify_entries(
        plan.get("teasers") or [], "teaser", words, pauses, duration, timing, strict, True)
    shorts, short_errors, short_warnings = verify_entries(
        plan.get("short_posts") or [], "short_post", words, pauses, duration, timing, strict, True)
    errors.extend(teaser_errors + short_errors)
    warnings.extend(teaser_warnings + short_warnings)

    approved, expected_hash, approval_errors = check_approval(plan, require_approved)
    errors.extend(approval_errors)
    chapter_ids = {item.get("id") for item in chapters if isinstance(item, dict)}
    unknown_approved = [item for item in approved if item not in chapter_ids]
    if unknown_approved:
        errors.append("approved_chapters contains unknown chapter ids: "
                      + ", ".join(unknown_approved))

    if timing == "estimated":
        warnings.append("transcript timing is estimated; every boundary is approximate, not exact")
    if not supplied_pauses and not gaps:
        warnings.append("no pause map and no word gaps; pause evidence is missing")
    elif not supplied_pauses:
        warnings.append("no pause map supplied; using transcript word gaps")
    if not words:
        errors.append("transcript has no usable word or segment text")

    report = {
        "ok": not errors,
        "source": plan.get("media_source") or plan.get("source"),
        "duration": round(duration, 3) if duration else None,
        "transcript": {
            "path": plan.get("transcript"),
            "engine": transcript.get("engine"),
            "timing": timing,
            "segments": len(transcript.get("segments") or []),
            "word_units": len(words),
            "failed_chunks": transcript.get("failed_chunks") or [],
        },
        "pause_source": ("pause map and transcript gaps" if supplied_pauses and gaps
                         else "pause map" if supplied_pauses
                         else "transcript word gaps" if gaps else "none"),
        "approval": {
            "approved_chapters": approved,
            "approval_hash": expected_hash,
            "stored_hash": plan.get("approval_hash"),
            "required": bool(require_approved),
        },
        "chapters": verified,
        "teasers": teasers,
        "short_posts": shorts,
        "errors": errors,
        "warnings": warnings,
    }
    report["ok"] = not errors and not (strict and warnings)
    return report


def _md_escape(value):
    return str(value).replace("|", "\\|").replace("\n", " ")


def to_markdown(report):
    lines = ["# Cut plan review", ""]
    lines.append(f"- source: `{report.get('source') or 'unknown'}`")
    lines.append(f"- duration: {report.get('duration')}s")
    transcript = report["transcript"]
    lines.append(f"- transcript: {transcript.get('engine')} / {transcript.get('timing')} timing / "
                 f"{transcript.get('segments')} segments")
    lines.append(f"- approved chapters: {', '.join(report['approval']['approved_chapters']) or 'none'}")
    lines.append("")
    lines.append("| id | start | end | dur | opening | closing | open | close | confidence | review | issues |")
    lines.append("|---|---|---|---|---|---|---|---|---|---|---|")
    for chapter in report["chapters"]:
        issues = "; ".join(item["message"] for item in chapter.get("issues", [])) or ""
        lines.append(
            f"| {_md_escape(chapter.get('id', ''))} | {chapter.get('start', '')} | "
            f"{chapter.get('end', '')} | {chapter.get('duration', '')} | "
            f"{_md_escape(' '.join(chapter.get('opening_words', [])))} | "
            f"{_md_escape(' '.join(chapter.get('closing_words', [])))} | "
            f"{chapter.get('opening_complete', '')} | {chapter.get('closing_complete', '')} | "
            f"{chapter.get('boundary_confidence', '')} | "
            f"{'YES' if chapter.get('review_required') else 'no'} | {_md_escape(issues)} |"
        )
    for key, title in (("teasers", "Teaser picks"), ("short_posts", "Short posts")):
        entries = report.get(key) or []
        if not entries:
            continue
        lines += ["", f"## {title}", "",
                  "| id | start | end | dur | opening | closing | confidence | review |",
                  "|---|---|---|---|---|---|---|---|"]
        for entry in entries:
            lines.append(
                f"| {_md_escape(entry.get('id', ''))} | {entry.get('start', '')} | "
                f"{entry.get('end', '')} | {entry.get('duration', '')} | "
                f"{_md_escape(' '.join(entry.get('opening_words', [])))} | "
                f"{_md_escape(' '.join(entry.get('closing_words', [])))} | "
                f"{entry.get('boundary_confidence', '')} | "
                f"{'YES' if entry.get('review_required') else 'no'} |"
            )
    lines += ["", "## Errors", ""]
    lines += [f"- {item}" for item in report["errors"]] or ["- none"]
    lines += ["", "## Warnings", ""]
    lines += [f"- {item}" for item in report["warnings"]] or ["- none"]
    lines.append("")
    return "\n".join(lines)


def _ffmpeg_run(command):
    subprocess.run([str(part) for part in command], check=True, stdin=subprocess.DEVNULL,
                   capture_output=True)


def write_previews(report, source, out_dir, seconds=6.0):
    source = Path(source).resolve()
    if not source.is_file():
        raise PlanError(f"source not found for previews: {source}")
    out = Path(out_dir).resolve()
    out.mkdir(parents=True, exist_ok=True)
    written = []
    for chapter in report["chapters"]:
        start, end = chapter.get("start"), chapter.get("end")
        if not _number(start) or not _number(end):
            continue
        for side, at in (("start", float(start)), ("end", float(end))):
            clip_start = max(0.0, at - seconds / 2.0)
            audio = out / f"{chapter['id']}_{side}.wav"
            frame = out / f"{chapter['id']}_{side}.jpg"
            _ffmpeg_run(["ffmpeg", "-nostdin", "-v", "error", "-y",
                         "-ss", f"{clip_start:.3f}", "-t", f"{seconds:.3f}", "-i", source,
                         "-vn", "-ac", "1", "-ar", "16000", audio])
            _ffmpeg_run(["ffmpeg", "-nostdin", "-v", "error", "-y",
                         "-i", source, "-ss", f"{at:.3f}", "-frames:v", "1",
                         "-q:v", "2", frame])
            written.append({"chapter": chapter["id"], "side": side, "at": at,
                            "audio": str(audio), "frame": str(frame)})
    manifest = out / "previews.json"
    manifest.write_text(json.dumps(written, ensure_ascii=False, indent=2), encoding="utf-8")
    return written, manifest


def main(argv=None):
    if argv is None:
        argv = sys.argv[1:]
    if argv and argv[0] == "verify":
        argv = argv[1:]
    parser = argparse.ArgumentParser(description="Verify a cut plan against ASR timings and pauses.")
    parser.add_argument("plan", help="cut_plan.json")
    parser.add_argument("--transcript", help="raw ASR JSON (default: plan.transcript)")
    parser.add_argument("--pauses", help="source-level pauses.json")
    parser.add_argument("--source", help="source video (for duration and previews)")
    parser.add_argument("--json-out", help="write the verified report JSON")
    parser.add_argument("--md-out", help="write the Markdown review")
    parser.add_argument("--previews", help="write short audio and frame previews around boundaries")
    parser.add_argument("--strict", action="store_true", help="treat warnings as failures")
    parser.add_argument("--allow-overlap", action="store_true",
                        help="allow chapters to overlap (off by default)")
    parser.add_argument("--require-approved", action="store_true",
                        help="fail unless the approved chapters and approval hash are valid")
    parser.add_argument("--force", action="store_true", help="overwrite output reports and previews")
    args = parser.parse_args(argv)

    try:
        plan_path = Path(args.plan).expanduser().resolve()
        plan = _load(plan_path)
        base = plan_path.parent
        transcript_path = Path(args.transcript).expanduser().resolve() if args.transcript else (
            _resolve(base, plan["transcript"]) if plan.get("transcript") else None)
        pauses_path = Path(args.pauses).expanduser().resolve() if args.pauses else (
            _resolve(base, plan["pauses"]) if plan.get("pauses") else None)
        source_path = Path(args.source).expanduser().resolve() if args.source else (
            _resolve(base, plan.get("media_source") or plan["source"])
            if (plan.get("media_source") or plan.get("source")) else None)
        if transcript_path is None:
            raise PlanError("no transcript path: pass --transcript or set transcript in the plan")
        transcript = _load(transcript_path)
        pauses = []
        if pauses_path and pauses_path.is_file():
            pauses = _load(pauses_path)
            if not isinstance(pauses, list):
                raise PlanError("pause map must be a JSON array of [start, end] pairs")
            pauses = [(float(a), float(b)) for a, b in pauses if _number(a) and _number(b) and b > a]
        else:
            pauses_path = None
        duration = None
        if source_path and source_path.is_file():
            import subprocess as _sp
            probe = _sp.run(["ffprobe", "-v", "error", "-show_entries", "format=duration",
                             "-of", "csv=p=0", source_path], capture_output=True, text=True)
            try:
                duration = float(probe.stdout.strip())
            except ValueError:
                duration = None
        report = verify_plan(plan, transcript, pauses, duration, args.strict,
                             args.allow_overlap, args.require_approved)
        report["transcript"]["path"] = str(transcript_path)
        report["pause_map"] = str(pauses_path) if pauses_path else None

        for path, payload in ((args.json_out, json.dumps(report, ensure_ascii=False, indent=2)),
                              (args.md_out, to_markdown(report))):
            if not path:
                continue
            target = Path(path).expanduser().resolve()
            if target.exists() and not args.force:
                raise PlanError(f"refusing to overwrite {target}; pass --force")
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(payload + ("" if payload.endswith("\n") else "\n"), encoding="utf-8")

        if args.previews:
            if not source_path or not source_path.is_file():
                raise PlanError("--previews needs a valid source video")
            if Path(args.previews).exists() and not args.force:
                raise PlanError(f"preview directory already exists: {args.previews}; pass --force")
            write_previews(report, source_path, args.previews)

        print(to_markdown(report))
        return 0 if report["ok"] else 1
    except PlanError as exc:
        print(f"cut_plan: {exc}", file=sys.stderr)
        return 2


if __name__ == "__main__":
    sys.exit(main())
