#!/usr/bin/env python3
"""Word error rate from a reviewed transcript worksheet.

`asr.py` writes `transcript.review.csv` with one row per segment and an empty `correction`
column. A native speaker fills that column (blank means "not reviewed yet"), then:

    python3 wer_review.py transcript.review.csv
    python3 wer_review.py transcript.review.csv --out reviewed/wer_report.md

Only reviewed rows count. Text is normalised the same way as `asr_benchmark.py`: lowercase,
punctuation removed, spaces collapsed, Yoruba diacritics kept. `--strip-diacritics` adds a
tone-insensitive view.

The report gives WER and CER overall, per chapter and per engine, plus the worst rows with the
hypothesis and the correction side by side, so the next engine or prompt change has a target.
"""
import argparse
import csv
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import asr_benchmark as bench


def counts(hyp, ref, strip_diacritics=False):
    h = bench.normalize(hyp, strip_diacritics).split()
    r = bench.normalize(ref, strip_diacritics).split()
    if not r:
        return 0, 0, 0, 0
    edits = bench._levenshtein(h, r)
    hc = bench.normalize(hyp, strip_diacritics).replace(" ", "")
    rc = bench.normalize(ref, strip_diacritics).replace(" ", "")
    cedits = bench._levenshtein(hc, rc)
    return edits, len(r), cedits, max(1, len(rc))


def rate(edits, total):
    return round(edits / total, 4) if total else None


def collect(path, strip_diacritics=False, column="correction"):
    rows = []
    with open(path, newline="", encoding="utf-8") as fh:
        for row in csv.DictReader(fh):
            ref = (row.get(column) or "").strip()
            if not ref:
                continue
            hyp = (row.get("text") or "").strip()
            e, r, ce, cr = counts(hyp, ref, strip_diacritics)
            rows.append({"line": row.get("line"), "chapter": row.get("chapter", ""),
                         "engine": row.get("engine", ""), "start": row.get("start", ""),
                         "hyp": hyp, "ref": ref, "edits": e, "ref_words": r,
                         "cer_edits": ce, "ref_chars": cr,
                         "wer": rate(e, r), "cer": rate(ce, cr)})
    return rows


def summarize(rows):
    groups = {}
    for r in rows:
        for key in (("all", ""), ("chapter", r["chapter"]), ("engine", r["engine"])):
            g = groups.setdefault(key, {"edits": 0, "words": 0, "cedits": 0, "chars": 0, "rows": 0})
            g["edits"] += r["edits"]
            g["words"] += r["ref_words"]
            g["cedits"] += r["cer_edits"]
            g["chars"] += r["ref_chars"]
            g["rows"] += 1
    return {k: {**v, "wer": rate(v["edits"], v["words"]), "cer": rate(v["cedits"], v["chars"])}
            for k, v in groups.items()}


def report(rows, groups, source, strip_diacritics=False):
    lines = [f"# WER review: {Path(source).name}", ""]
    if not rows:
        lines += ["No reviewed rows yet: fill the `correction` column (blank means not reviewed).", ""]
        return "\n".join(lines)
    lines += [f"- reviewed rows: {len(rows)}",
              f"- normalisation: lowercase, punctuation removed, diacritics "
              f"{'stripped' if strip_diacritics else 'kept'}", "",
              "| group | key | rows | WER | CER |", "|---|---|---|---|---|"]
    order = ["all", "chapter", "engine"]
    for kind in order:
        for (k, key), g in sorted(groups.items()):
            if k != kind:
                continue
            lines.append(f"| {kind} | {key} | {g['rows']} | {g['wer']} | {g['cer']} |")
    lines += ["", "## Worst rows", "", "| line | start | chapter | WER | engine said | correction |",
              "|---|---|---|---|---|---|"]
    for r in sorted(rows, key=lambda x: -(x["wer"] or 0))[:15]:
        lines.append(f"| {r['line']} | {r['start']} | {r['chapter']} | {r['wer']} | "
                     f"{r['hyp']} | {r['ref']} |")
    lines.append("")
    return "\n".join(lines)


def main():
    ap = argparse.ArgumentParser(description="Compute WER from a reviewed transcript worksheet.")
    ap.add_argument("csv", help="transcript.review.csv from asr.py")
    ap.add_argument("--out", help="write the markdown report here")
    ap.add_argument("--column", default="correction", help="correction column name")
    ap.add_argument("--strip-diacritics", action="store_true")
    a = ap.parse_args()
    rows = collect(a.csv, a.strip_diacritics, a.column)
    groups = summarize(rows)
    text = report(rows, groups, a.csv, a.strip_diacritics)
    out = Path(a.out) if a.out else Path(a.csv).with_name("wer_report.md")
    out.write_text(text, encoding="utf-8")
    print(text)
    print(f"wrote {out}")


if __name__ == "__main__":
    main()
