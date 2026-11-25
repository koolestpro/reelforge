#!/usr/bin/env python3
"""Guard Qur'an citations against a local, human-verified table.

Usage:
  python verify_scripture.py check-job JOB.json
  python verify_scripture.py check-file library/2026-10-30/writeups.md
  python verify_scripture.py check-text "..." --label caption
  python verify_scripture.py selftest

Exit 1 on an error. A warning is a citation without Arabic (a paraphrase) and
still needs a human review; --strict turns warnings into errors too.

This is a mechanical check, not tafsir. It cannot judge a paraphrase. It does
block an unverified reference, Arabic that is not in the cited ayah, and the
known 3:146 mistake (3:146 is about patience, not gratitude).
"""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
DEFAULT_TABLE = HERE / "scripture_verified.json"

REF_RE = re.compile(
    r"(?:Al[\s-]*Qur'?an|Qur'?an)\s*(\d{1,3})\s*:\s*(\d{1,3})(?:\s*-\s*(\d{1,3}))?",
    re.IGNORECASE,
)
ARABIC_RUN = re.compile(
    r"[\u0600-\u06FF][\u0600-\u06FF\s\u064B-\u065F\u0670\u06D6-\u06ED\u0640…\.,:;!?()\-]*"
)
DIACRITICS = re.compile(r"[\u0610-\u061A\u064B-\u065F\u06D6-\u06ED\u08D3-\u08FF]")


def normalize_arabic(text: str) -> str:
    """Reduce Arabic to letters so diacritics and Uthmani marks do not matter."""
    text = text.replace("\u0640", "")
    text = text.replace("\u0670", "\u0627")  # superscript alef -> alef
    text = DIACRITICS.sub("", text)
    text = re.sub("[\u0623\u0625\u0622\u0671]", "\u0627", text)
    text = re.sub("[\u0649\u06CC]", "\u064A", text)
    text = text.replace("\u0629", "\u0647")
    text = re.sub("[\u0626]", "\u064A", text)
    text = re.sub("[\u0624]", "\u0648", text)
    text = text.replace("\u0621", "")
    return re.sub(r"[^\u0621-\u064A]", "", text)


def load_table(path=None):
    p = Path(path) if path else DEFAULT_TABLE
    data = json.loads(p.read_text(encoding="utf-8"))
    return data.get("quran", data)


def _key(start, ayah, end=None):
    return f"{start}:{ayah}" + (f"-{end}" if end else "")


def lookup(table, start, ayah, end=None):
    key = _key(start, ayah, end)
    if key in table:
        return key, table[key]
    if end is not None:
        for name, entry in table.items():
            m = re.fullmatch(r"(\d+):(\d+)-(\d+)", name)
            if not m:
                continue
            s, a, b = int(m.group(1)), int(m.group(2)), int(m.group(3))
            if s == start and a <= ayah and end <= b:
                return name, entry
    return None, None


def _line(text, pos):
    return text.count("\n", 0, pos) + 1


def check_text(text, table, label="text", strict=False):
    errors, warnings = [], []
    seen = set()

    def add(bucket, message):
        if message not in seen:
            seen.add(message)
            bucket.append(message)

    for m in REF_RE.finditer(text):
        start, ayah = int(m.group(1)), int(m.group(2))
        end = int(m.group(3)) if m.group(3) else None
        ref = _key(start, ayah, end)
        where = f"{label}:{_line(text, m.start())}" if label else f"line {_line(text, m.start())}"
        key, entry = lookup(table, start, ayah, end)
        if entry is None:
            add(errors, f"{where}: unverified Qur'an reference {ref}")
            continue

        # Use the nearest Arabic run, not the longest, so a quote from the
        # previous paragraph cannot be blamed on this reference.
        lo = max(0, m.start() - 200)
        hi = min(len(text), m.end() + 200)
        window = text[lo:hi]
        runs = []
        for rm in ARABIC_RUN.finditer(window):
            run = rm.group(0)
            if len(normalize_arabic(run)) >= 3:
                runs.append((abs((rm.start() + lo) - m.start()), run))
        verified = normalize_arabic(entry["arabic"])
        if runs:
            _, run = min(runs, key=lambda item: item[0])
            if normalize_arabic(run) not in verified:
                add(errors, f"{where}: Arabic near {ref} is not in the verified ayah: {run.strip()[:80]}")
        else:
            add(warnings, f"{where}: {ref} has no Arabic quote; check the paraphrase by hand")

        if start == 3 and ayah == 146 and re.search(r"grateful|thank", window, re.IGNORECASE):
            add(errors, f"{where}: 3:146 is about patience, not gratitude")

    if strict:
        errors.extend(warnings)
        warnings = []
    return errors, warnings


def _cards(node):
    if isinstance(node, dict):
        if ("arabic" in node or "english" in node) and ("source" in node or "heading" in node):
            yield node
        for value in node.values():
            yield from _cards(value)
    elif isinstance(node, list):
        for value in node:
            yield from _cards(value)


def _card_text(card):
    def join(value):
        return " ".join(value) if isinstance(value, list) else str(value or "")
    return "\n".join(
        part for part in (
            join(card.get("arabic")),
            join(card.get("english")),
            str(card.get("source") or ""),
            str(card.get("heading") or ""),
        ) if part
    )


def check_job(job, table=None, strict=False):
    table = table or load_table()
    errors, warnings = [], []
    for card in _cards(job):
        label = str(card.get("heading") or card.get("source") or "card")
        e, w = check_text(_card_text(card), table, label=label, strict=strict)
        errors.extend(e)
        warnings.extend(w)
    return errors, warnings


def check_file(path, table=None, strict=False):
    p = Path(path)
    text = p.read_text(encoding="utf-8")
    if p.suffix.lower() == ".json":
        try:
            data = json.loads(text)
        except json.JSONDecodeError:
            pass
        else:
            return check_job(data, table=table, strict=strict)
    return check_text(text, table or load_table(), label=str(p), strict=strict)


def _report(errors, warnings):
    for message in errors:
        print(f"ERROR {message}")
    for message in warnings:
        print(f"WARN  {message}")
    if not errors and not warnings:
        print("OK: no scripture problems found")
    return 1 if errors else 0


def selftest():
    table = load_table()
    wrong = 'He says, وَٱللَّهُ يُحِبُّ ٱلشَّـٰكِرِينَ, "And Allah loves those who are grateful." (Qur\'an 3:146)'
    right = 'He says, وَسَيَجْزِي ٱللَّهُ ٱلشَّاكِرِينَ, "And Allah will reward those who are grateful." (Qur\'an 3:144)'
    e1, _ = check_text(wrong, table)
    e2, _ = check_text(right, table)
    if not e1 or e2:
        print("FAIL selftest")
        return 1
    print("OK selftest")
    return 0


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    sub = parser.add_subparsers(dest="command", required=True)

    job = sub.add_parser("check-job")
    job.add_argument("job")
    job.add_argument("--strict", action="store_true")

    file_p = sub.add_parser("check-file")
    file_p.add_argument("file")
    file_p.add_argument("--strict", action="store_true")

    text_p = sub.add_parser("check-text")
    text_p.add_argument("text")
    text_p.add_argument("--label", default="text")
    text_p.add_argument("--strict", action="store_true")

    sub.add_parser("selftest")

    args = parser.parse_args()
    if args.command == "selftest":
        return selftest()
    if args.command == "check-job":
        data = json.loads(Path(args.job).read_text(encoding="utf-8"))
        return _report(*check_job(data, strict=args.strict))
    if args.command == "check-file":
        return _report(*check_file(args.file, strict=args.strict))
    return _report(*check_text(args.text, load_table(), label=args.label, strict=args.strict))


if __name__ == "__main__":
    sys.exit(main())
