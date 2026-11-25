#!/usr/bin/env python3
"""Compare Yoruba ASR engines on the same audio slice, side by side.

    python3 asr_benchmark.py SOURCE --start 621.5 --end 651.5
    python3 asr_benchmark.py SOURCE --start 2.85 --end 40 --engines groq,elevenlabs,9jalingo
    python3 asr_benchmark.py SOURCE --reference transcript.raw.json   # adds WER/CER

It cuts one slice, runs each requested engine on it, and writes:
  <outdir>/slice.wav
  <outdir>/<engine>.json        (the uniform ASR output for that engine)
  <outdir>/<engine>.txt         ([MM:SS] lines)
  <outdir>/benchmark.md         (table, transcripts, cost, WER when a reference is given)

Engines without a key are reported as skipped, not fatal. Cost is an estimate from the
provider's published rate at the time of writing (see docs/asr.md); billing may
differ. There is no charge from this script itself.

WER is computed after lowercasing, removing punctuation and collapsing spaces. Diacritics are
kept for `wer`; `wer_nod` strips them, which is a fairer look at word identity when the key
engine drops Yoruba tone marks.
"""
import argparse
import json
import os
import re
import subprocess
import sys
import time
import unicodedata
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

import asr

COST_USD_PER_HOUR = {
    "deepinfra": 0.11,
    "speechyou": 2.00,
    "ncair": 0.0,
    "mms": 0.0,
}
SPEECHYOU_MODEL_COST = {"whisper": 0.50, "multilingual_pro": 2.00}


def cut_slice(source, dst, start=0.0, end=None):
    cmd = ["ffmpeg", "-y", "-v", "error"]
    if start:
        cmd += ["-ss", f"{start:.3f}"]
    cmd += ["-i", str(source)]
    if end is not None:
        cmd += ["-t", f"{max(0.05, end - start):.3f}"]
    cmd += ["-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)]
    subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL)
    return dst


def normalize(text, strip_diacritics=False):
    text = unicodedata.normalize("NFC", text or "").lower()
    if strip_diacritics:
        text = "".join(c for c in unicodedata.normalize("NFD", text)
                       if unicodedata.category(c) != "Mn")
        text = unicodedata.normalize("NFC", text)
    text = re.sub(r"[^\w\s'’\u0300-\u036f]", " ", text, flags=re.UNICODE)
    text = text.replace("’", "'")
    return re.sub(r"\s+", " ", text).strip()


def _levenshtein(a, b):
    if len(a) < len(b):
        a, b = b, a
    prev = list(range(len(b) + 1))
    for i, ca in enumerate(a, 1):
        cur = [i]
        for j, cb in enumerate(b, 1):
            cur.append(min(prev[j] + 1, cur[j - 1] + 1, prev[j - 1] + (ca != cb)))
        prev = cur
    return prev[-1]


def error_rates(hyp, ref, strip_diacritics=False):
    h = normalize(hyp, strip_diacritics).split()
    r = normalize(ref, strip_diacritics).split()
    if not r:
        return None, None
    wer = _levenshtein(h, r) / len(r)
    hc = normalize(hyp, strip_diacritics).replace(" ", "")
    rc = normalize(ref, strip_diacritics).replace(" ", "")
    cer = _levenshtein(hc, rc) / max(1, len(rc))
    return round(wer, 4), round(cer, 4)


def reference_text(path):
    p = Path(path)
    if not p.exists():
        raise SystemExit(f"reference not found: {p}")
    if p.suffix.lower() == ".json":
        js = json.loads(p.read_text(encoding="utf-8"))
        return js.get("text") or " ".join(s.get("text", "") for s in js.get("segments", []))
    return p.read_text(encoding="utf-8")


def cost_usd(engine, seconds, model=None):
    if engine == "speechyou":
        rate = SPEECHYOU_MODEL_COST.get(model or asr.SPEECHYOU_MODEL,
                                        COST_USD_PER_HOUR["speechyou"])
    elif engine == "9jalingo":
        return None
    else:
        rate = COST_USD_PER_HOUR.get(engine)
    if rate is None:
        return None
    return round(rate * seconds / 3600.0, 4)


def run_one(engine, slice_path, outdir, seconds, args, keyterms):
    t0 = time.time()
    try:
        result = asr.transcribe(
            slice_path, out_path=outdir / f"{engine}.json", workdir=outdir / f"_{engine}",
            chunk_seconds=10 ** 9, prompt=args.prompt, language=args.language,
            keep_going=False, no_chunk=True, txt_path=outdir / f"{engine}.txt",
            engines=[engine], keyterms=keyterms, public_url=args.public_url)
    except SystemExit as e:
        return {"engine": engine, "ok": False, "error": str(e), "elapsed": round(time.time() - t0, 1)}
    except Exception as e:
        return {"engine": engine, "ok": False, "error": f"{type(e).__name__}: {e}",
                "elapsed": round(time.time() - t0, 1)}
    entry = {"engine": engine, "ok": True, "elapsed": round(time.time() - t0, 1),
             "timing": result.get("timing"), "text": result.get("text", ""),
             "segments": len(result.get("segments", [])),
             "chars": len(result.get("text", "")),
             "model": (result.get("tier_report") or [{}])[0].get("model"),
             "cost_usd": cost_usd(engine, seconds,
                                  (result.get("tier_report") or [{}])[0].get("model"))}
    return entry


def main():
    ap = argparse.ArgumentParser(description="Compare Yoruba ASR engines on one audio slice.")
    ap.add_argument("source", nargs="?", help="video or audio file")
    ap.add_argument("--start", type=float, default=0.0, help="slice start in seconds")
    ap.add_argument("--end", type=float, default=None, help="slice end in seconds")
    ap.add_argument("--outdir", default="work/asr_bench", help="output folder")
    ap.add_argument("--engines", default=None,
                    help="comma list (default: every engine that has a key, plus local ones)")
    ap.add_argument("--reference", help="reference transcript (.json uniform, or plain text)")
    ap.add_argument("--strip-diacritics", action="store_true",
                    help="also report WER/CER without diacritics")
    ap.add_argument("--prompt", default=asr.DEFAULT_PROMPT)
    ap.add_argument("--keyterms", default=os.environ.get("ASR_KEYTERMS"))
    ap.add_argument("--language", default="yo")
    ap.add_argument("--public-url", help="public audio URL for engines that need one")
    ap.add_argument("--list-engines", action="store_true")
    args = ap.parse_args()

    if args.list_engines:
        asr.print_engines()
        return
    if not args.source:
        ap.error("source is required (or use --list-engines)")

    source = Path(args.source).resolve()
    if not source.exists():
        raise SystemExit(f"source not found: {source}")
    outdir = Path(args.outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    slice_path = outdir / "slice.wav"
    cut_slice(source, slice_path, args.start, args.end)
    seconds = asr.duration_of(slice_path)
    print(f"slice {args.start:.2f}-{args.end if args.end is not None else 'end'} "
          f"({seconds:.2f}s) -> {slice_path}", flush=True)

    if args.engines:
        names = [e.strip() for e in args.engines.split(",") if e.strip()]
    else:
        names = list(asr.PROVIDERS)
    unknown = [n for n in names if n not in asr.PROVIDERS]
    if unknown:
        raise SystemExit(f"unknown engine(s): {', '.join(unknown)}")

    keyterms = [k.strip() for k in (args.keyterms or "").split(",") if k.strip()]
    ref = reference_text(args.reference) if args.reference else None
    rows = []
    for name in names:
        if not asr.available(name):
            rows.append({"engine": name, "ok": False,
                         "error": f"skipped: {asr.PROVIDERS[name].get('key')} not set"})
            print(f"  - {name}: skipped (no key)", flush=True)
            continue
        print(f"  > {name}", flush=True)
        row = run_one(name, slice_path, outdir, seconds, args, keyterms)
        if row.get("ok") and ref is not None:
            row["wer"], row["cer"] = error_rates(row["text"], ref)
            if args.strip_diacritics:
                row["wer_nod"], row["cer_nod"] = error_rates(row["text"], ref, True)
        rows.append(row)

    lines = [f"# ASR benchmark: {source.name}", "",
             f"- slice: {args.start:.2f} to {args.end if args.end is not None else 'end'} s "
             f"({seconds:.2f} s)",
             f"- language hint: `{args.language}`",
             f"- prompt: `{args.prompt}`",
             f"- reference: `{args.reference or 'none'}`", "",
             "| engine | status | chars | segments | timings | elapsed s | cost USD | WER | CER |",
             "|---|---|---|---|---|---|---|---|---|"]
    for r in rows:
        if not r.get("ok"):
            lines.append(f"| {r['engine']} | {r.get('error', 'failed')} | | | | | | | |")
            continue
        lines.append("| {engine} | ok | {chars} | {segments} | {timing} | {elapsed} | {cost} | {wer} | {cer} |".format(
            engine=r["engine"], chars=r["chars"], segments=r["segments"], timing=r.get("timing"),
            elapsed=r["elapsed"], cost=r.get("cost_usd", ""),
            wer=r.get("wer", ""), cer=r.get("cer", "")))
    if args.strip_diacritics:
        lines += ["", "Diacritic-insensitive:", "",
                  "| engine | WER (no diacritics) | CER (no diacritics) |", "|---|---|---|"]
        for r in rows:
            if r.get("ok") and "wer_nod" in r:
                lines.append(f"| {r['engine']} | {r['wer_nod']} | {r['cer_nod']} |")
    lines += ["", "## Transcripts", ""]
    for r in rows:
        lines.append(f"### {r['engine']}")
        lines.append("")
        if r.get("ok"):
            lines.append("```")
            lines.append(r["text"])
            lines.append("```")
        else:
            lines.append(f"`{r.get('error', 'failed')}`")
        lines.append("")

    report = outdir / "benchmark.md"
    report.write_text("\n".join(lines), encoding="utf-8")
    print("\n".join(lines[:12 + len(rows)]))
    print(f"\nwrote {report}", flush=True)


if __name__ == "__main__":
    main()
