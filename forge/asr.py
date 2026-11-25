#!/usr/bin/env python3
"""Tiered ASR for code-switched speech.

    video/audio -> ffmpeg chunks (with global offsets) -> engine cascade -> uniform JSON

Engines are pluggable and picked with `--engines` (ordered, first success wins):
  groq         Groq whisper-large-v3          word timings, prompt hint, free tier
  deepinfra    DeepInfra whisper-large-v3     word timings, prompt hint, cheap
  elevenlabs   ElevenLabs Scribe v2           word timings, keyterms
  speechyou    Speechyou whisper / ml_pro     segment timings, signed upload
  9jalingo     9jaLingo Yoruba STT            text only, needs a public URL
  hf           HF Inference Endpoint / router text only, any ASR endpoint
  ncair        NCAIR1/Yoruba-ASR local        segment timings, needs torch + HF token
  mms          facebook/mms-1b-all local      segment timings, offline

    python3 asr.py SOURCE --out transcript.raw.json
    python3 asr.py SOURCE --engines elevenlabs,groq --prompt "Ẹ n lẹ, loni a fẹ sọrọ nipa..."
    python3 asr.py SOURCE --engines 9jalingo --chunk-seconds 900
    python3 asr.py SOURCE --tier 2                 # start at the 2nd engine of the default chain
    python3 asr.py SOURCE --txt transcript.txt     # also write [MM:SS] lines
    python3 asr.py --list-engines                  # show keys, language, timings, cost

Keys come from the environment, else .env in the repo root or the current folder.
See .env.example for the full list.

Output schema (uniform, ready for clip cutting and report writing):
  {task, engine, timing, engines_used, language, source, duration, created, prompt_hint,
   chunk_seconds, tier_report:[{chunk, engine, ok, error?, timing?, model?}],
   failed_chunks:[{chunk, offset, duration}], segments:[{start,end,text,words?}], text}
Timestamps are realigned to the master timeline: global = chunk_offset + local.
Text-only engines get "timing": "estimated": sentences are spread across the chunk by
character weight, which is good enough for chapter drafting but not for burned-in captions.
"""
import argparse
import csv
import hashlib
import json
import os
import re
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path

DEFAULT_PROMPT = "Ẹ n lẹ, loni a fẹ sọrọ nipa... Bismillah, Alhamdullilah."
DEFAULT_ENGINES = ["groq", "deepinfra", "mms"]
MIN_SPLIT_SECONDS = 15.0
MAX_SPLIT_DEPTH = 4
PLACEHOLDER_RE = re.compile(r"^\s*(?:\[[^\]\n]{1,80}\]\s*)+$")

GROQ_URL = "https://api.groq.com/openai/v1/audio/transcriptions"
DEEPINFRA_URL = "https://api.deepinfra.com/v1/openai/audio/transcriptions"
ELEVENLABS_URL = "https://api.elevenlabs.io/v1/speech-to-text"
SPEECHYOU_BASE = "https://app.speechyou.com/api/v1"
NINEJA_BASE = "https://api.9jalingo.org"
HF_ROUTER = "https://router.huggingface.co/hf-inference/models"

GROQ_MODEL = os.environ.get("GROQ_MODEL", "whisper-large-v3")
DEEPINFRA_MODEL = os.environ.get("DEEPINFRA_MODEL", "openai/whisper-large-v3")
ELEVENLABS_MODEL = os.environ.get("ELEVENLABS_STT_MODEL", "scribe_v2")
SPEECHYOU_MODEL = os.environ.get("SPEECHYOU_STT_MODEL", "multilingual_pro")
NCAIR_MODEL = os.environ.get("NCAIR_MODEL", "NCAIR1/Yoruba-ASR")
NCAIR_LOCAL_DIR = Path(__file__).resolve().parent / "models" / "ncair_yoruba"
HF_MODEL = os.environ.get("HF_ASR_MODEL", NCAIR_MODEL)


class TranscribeError(Exception):
    retryable = False


class TierUnavailable(TranscribeError):
    pass


class RateLimited(TranscribeError):
    retryable = True


class TooLarge(TranscribeError):
    pass


class Transient(TranscribeError):
    retryable = True


class NoSpeech(TranscribeError):
    """The engine answered but produced only a tag or nothing: re-chunk or fall through."""


def load_env():
    root = Path(__file__).resolve().parents[2]
    for p in (root / ".env", Path.cwd() / ".env"):
        if not p.exists():
            continue
        for line in p.read_text(encoding="utf-8", errors="ignore").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            os.environ.setdefault(k.strip(), v.strip().strip('"').strip("'"))


load_env()


def hf_token():
    """Token from the environment, else from `hf auth login` (~/.cache/huggingface/token)."""
    for name in ("HF_TOKEN", "HUGGINGFACE_API_KEY", "HF_API_TOKEN"):
        value = os.environ.get(name)
        if value:
            return value.strip()
    token_file = Path.home() / ".cache" / "huggingface" / "token"
    if token_file.exists():
        try:
            return token_file.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None
    return None


def sh(cmd):
    cmd = [str(c) for c in cmd]
    print("$", " ".join(cmd)[:180], flush=True)
    return subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL)


def duration_of(path):
    out = subprocess.run(
        ["ffprobe", "-v", "error", "-show_entries", "format=duration", "-of", "csv=p=0", str(path)],
        check=True, capture_output=True, text=True).stdout.strip()
    try:
        return float(out.split(",")[0])
    except (ValueError, IndexError):
        return 0.0


def extract_audio(source, dst, ar=16000, br="64k"):
    sh(["ffmpeg", "-y", "-i", source, "-vn", "-ac", "1", "-ar", str(ar), "-b:a", br, dst])
    return dst


def chunk_audio(source, outdir, chunk_seconds, prefix="chunk"):
    outdir = Path(outdir)
    outdir.mkdir(parents=True, exist_ok=True)
    pattern = str(outdir / f"{prefix}_%03d.mp3")
    csv = outdir / f"{prefix}_list.csv"
    sh(["ffmpeg", "-y", "-i", source, "-vn", "-ac", "1", "-ar", "16000", "-b:a", "64k",
        "-f", "segment", "-segment_time", str(chunk_seconds), "-reset_timestamps", "1",
        "-segment_list", str(csv), "-segment_list_type", "csv", pattern])
    chunks = sorted(outdir.glob(f"{prefix}_*.mp3"))
    offsets = {}
    if csv.exists():
        for line in csv.read_text(encoding="utf-8", errors="ignore").splitlines():
            parts = [p.strip() for p in line.split(",")]
            if len(parts) >= 2:
                try:
                    offsets[Path(parts[0]).name] = float(parts[1])
                except ValueError:
                    pass
    return [(c, offsets.get(c.name, i * chunk_seconds)) for i, c in enumerate(chunks)]


def _http(method, url, timeout, **kwargs):
    import requests
    try:
        r = requests.request(method, url, timeout=timeout, **kwargs)
    except requests.Timeout as e:
        raise Transient(f"timeout: {e}")
    except requests.RequestException as e:
        raise Transient(f"network: {e}")
    if r.status_code == 429:
        raise RateLimited(r.text[:200])
    if r.status_code == 413:
        raise TooLarge(r.text[:200])
    if r.status_code in (401, 403):
        raise TierUnavailable(f"auth {r.status_code}: {r.text[:200]}")
    if r.status_code >= 500:
        raise Transient(f"server {r.status_code}: {r.text[:160]}")
    if r.status_code >= 400:
        raise TierUnavailable(f"http {r.status_code}: {r.text[:200]}")
    return r


def _json(r):
    try:
        return r.json()
    except ValueError:
        raise Transient("non-json response")


def _post(url, headers, data, files, timeout=600):
    return _json(_http("POST", url, timeout, headers=headers, data=data, files=files))


def _segments_from_words(words, fallback_duration, max_chars=220, gap=0.8):
    """Group word objects (text/start/end) into readable segments and keep word timings."""
    out, cur, cur_text = [], [], ""
    for w in words:
        if w.get("type", "word") not in ("word", "spacing"):
            continue
        text = (w.get("text") or "").strip()
        if not text:
            continue
        start, end = float(w.get("start", 0.0)), float(w.get("end", 0.0))
        if cur and (start - cur[-1]["end"] > gap or len(cur_text) >= max_chars):
            out.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                        "text": cur_text.strip(), "words": cur})
            cur, cur_text = [], ""
        cur.append({"word": text, "start": start, "end": end})
        cur_text = f"{cur_text} {text}".strip()
        if text.endswith((".", "?", "!", ";", ":")) and len(cur_text) > 40:
            out.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                        "text": cur_text.strip(), "words": cur})
            cur, cur_text = [], ""
    if cur:
        out.append({"start": cur[0]["start"], "end": cur[-1]["end"],
                    "text": cur_text.strip(), "words": cur})
    if not out:
        return [{"start": 0.0, "end": fallback_duration, "text": " ".join(
            (w.get("text") or "").strip() for w in words).strip()}]
    return out


def is_placeholder(text):
    """True when an engine returned only tags such as [speaking in foreign language]."""
    value = (text or "").strip()
    return not value or bool(PLACEHOLDER_RE.match(value))


def estimate_segments(text, duration, max_chars=160):
    """Spread sentences over the chunk by character weight (for text-only engines)."""
    text = (text or "").strip()
    if not text:
        return []
    parts = [p.strip() for p in re.split(r"(?<=[.!?])\s+|\n+", text) if p.strip()]
    split = []
    for p in parts:
        while len(p) > max_chars:
            cut = p.rfind(",", 0, max_chars)
            cut = cut + 1 if cut > 40 else max_chars
            split.append(p[:cut].strip())
            p = p[cut:].strip()
        if p:
            split.append(p)
    if not split:
        return []
    total = sum(len(p) for p in split) or 1
    if duration <= 0:
        duration = max(1.0, total / 14.0)
    out, t = [], 0.0
    for p in split:
        span = duration * len(p) / total
        out.append({"start": round(t, 3), "end": round(min(duration, t + span), 3), "text": p})
        t += span
    return out


def _segments_from(js, end_fallback):
    segs = js.get("segments")
    if segs:
        out = []
        for s in segs:
            out.append({"start": float(s.get("start", 0.0)), "end": float(s.get("end", end_fallback)),
                        "text": (s.get("text") or "").strip(),
                        "words": [{"word": w.get("word") or w.get("text", ""),
                                   "start": float(w.get("start", 0.0)),
                                   "end": float(w.get("end", 0.0))} for w in (s.get("words") or [])]})
        return out
    text = (js.get("text") or "").strip()
    return estimate_segments(text, end_fallback)


def _mime(path):
    return {".wav": "audio/wav", ".mp3": "audio/mpeg", ".m4a": "audio/x-m4a",
            ".mp4": "video/mp4", ".webm": "audio/webm", ".ogg": "audio/ogg",
            ".flac": "audio/flac"}.get(Path(path).suffix.lower(), "audio/mpeg")


def _openai_style(url, key, model, chunk_path, prompt, language):
    data = [("model", model), ("response_format", "verbose_json"), ("temperature", "0"),
            ("timestamp_granularities[]", "segment"), ("timestamp_granularities[]", "word")]
    if prompt:
        data.append(("prompt", prompt))
    if language:
        data.append(("language", language))
    with open(chunk_path, "rb") as fh:
        js = _post(url, {"Authorization": f"Bearer {key}"},
                   data, {"file": (Path(chunk_path).name, fh, _mime(chunk_path))})
    return js, float(js.get("duration") or 0.0)


def transcribe_groq(chunk_path, prompt, language, duration):
    key = os.environ.get("GROQ_API_KEY")
    if not key:
        raise TierUnavailable("GROQ_API_KEY not set")
    js, dur = _openai_style(GROQ_URL, key, GROQ_MODEL, chunk_path, prompt, language)
    return {"text": (js.get("text") or "").strip(), "language": js.get("language"),
            "duration": dur, "segments": _segments_from(js, dur), "timing": "word",
            "model": GROQ_MODEL}


def transcribe_deepinfra(chunk_path, prompt, language, duration):
    key = os.environ.get("DEEPINFRA_API_KEY")
    if not key:
        raise TierUnavailable("DEEPINFRA_API_KEY not set")
    js, dur = _openai_style(DEEPINFRA_URL, key, DEEPINFRA_MODEL, chunk_path, prompt, language)
    return {"text": (js.get("text") or "").strip(), "language": js.get("language"),
            "duration": dur, "segments": _segments_from(js, dur), "timing": "word",
            "model": DEEPINFRA_MODEL}


def _eleven_code(language):
    if not language:
        return None
    return {"yo": "yor", "ha": "hau", "ig": "ibo", "en": "eng"}.get(language, language)


def parse_elevenlabs(js, duration):
    words = js.get("words") or []
    segs = _segments_from_words(words, duration) if words else estimate_segments(js.get("text", ""), duration)
    return {"text": (js.get("text") or "").strip(), "language": js.get("language_code"),
            "duration": duration, "segments": segs,
            "timing": "word" if words else "estimated", "model": ELEVENLABS_MODEL}


def transcribe_elevenlabs(chunk_path, prompt, language, duration, keyterms=None):
    key = os.environ.get("ELEVENLABS_API_KEY")
    if not key:
        raise TierUnavailable("ELEVENLABS_API_KEY not set")
    data = [("model_id", ELEVENLABS_MODEL), ("timestamps_granularity", "word")]
    code = _eleven_code(language)
    if code:
        data.append(("language_code", code))
    for term in keyterms or []:
        data.append(("keyterms", term))
    with open(chunk_path, "rb") as fh:
        js = _json(_http("POST", ELEVENLABS_URL, 900, headers={"xi-api-key": key},
                         data=data, files={"file": (Path(chunk_path).name, fh, _mime(chunk_path))}))
    return parse_elevenlabs(js, duration)


def _cloudinary_publish(path):
    import requests
    url = os.environ.get("CLOUDINARY_URL")
    if not url or not url.startswith("cloudinary://"):
        raise TierUnavailable("9jalingo needs a public URL: set CLOUDINARY_URL or --public-url")
    from urllib.parse import urlparse
    u = urlparse(url)
    key, secret, cloud = u.username, u.password, u.hostname
    if not (key and secret and cloud):
        raise TierUnavailable("CLOUDINARY_URL is malformed")
    ts = int(time.time())
    signature = hashlib.sha1(f"timestamp={ts}{secret}".encode()).hexdigest()
    try:
        with open(path, "rb") as fh:
            r = requests.post(
                f"https://api.cloudinary.com/v1_1/{cloud}/auto/upload",
                data={"api_key": key, "timestamp": ts, "signature": signature},
                files={"file": (Path(path).name, fh, _mime(path))}, timeout=300)
    except requests.RequestException as e:
        raise Transient(f"cloudinary upload: {e}")
    if r.status_code >= 400:
        raise Transient(f"cloudinary http {r.status_code}: {r.text[:160]}")
    secure = (_json(r) or {}).get("secure_url")
    if not secure:
        raise Transient("cloudinary returned no secure_url")
    return secure


def transcribe_9jalingo(chunk_path, prompt, language, duration, public_url=None):
    key = os.environ.get("NAIJALINGO_API_KEY")
    if not key:
        raise TierUnavailable("NAIJALINGO_API_KEY not set")
    url = public_url or _cloudinary_publish(chunk_path)
    body = {"file_url": url}
    if language:
        body["language"] = language
    headers = {"X-API-Key": key, "Prefer": "respond-async-on-cold-start"}
    r = _http("POST", f"{NINEJA_BASE}/v1/audio/transcriptions", 1800, headers=headers, json=body)
    js = _json(r)
    if r.status_code == 202:
        job = js.get("job_id") if isinstance(js, dict) else None
        if not job:
            raise Transient(f"9jalingo queued no job id: {js}")
        for _ in range(900):
            time.sleep(max(2.0, float(js.get("retry_after") or 5)))
            js = _json(_http("GET", f"{NINEJA_BASE}/v1/audio/transcriptions/jobs/{job}", 60, headers=headers))
            state = str(js.get("status") or "").lower() if isinstance(js, dict) else ""
            if state == "completed":
                js = js.get("response") or {}
                break
            if state == "failed":
                raise Transient(f"9jalingo job failed: {js.get('error')}")
        else:
            raise Transient("9jalingo job did not finish in time")
    while isinstance(js, dict) and isinstance(js.get("result"), dict):
        js = js["result"]
    if isinstance(js, dict) and js.get("error"):
        raise Transient(f"9jalingo: {js['error']}")
    if not isinstance(js, dict) or not isinstance(js.get("text"), str):
        raise Transient("9jalingo returned no text")
    return parse_9jalingo(js, duration, language)


def parse_9jalingo(js, duration, language=None):
    dur = float(js.get("duration") or duration or 0.0)
    text = js["text"]
    return {"text": text.strip(), "language": js.get("language") or language,
            "duration": dur, "segments": estimate_segments(text, dur),
            "timing": "estimated", "model": js.get("model") or "9jalingo-stt"}


def _speechyou_upload(path, key):
    import requests
    size = Path(path).stat().st_size
    js = _json(_http("POST", f"{SPEECHYOU_BASE}/uploads", 120,
                     headers={"Authorization": f"Bearer {key}"},
                     json={"content_type": _mime(path), "bytes": size}))
    try:
        with open(path, "rb") as fh:
            r = requests.put(js["upload_url"], data=fh, headers=js.get("headers") or {}, timeout=600)
    except requests.RequestException as e:
        raise Transient(f"speechyou upload: {e}")
    if r.status_code >= 400:
        raise Transient(f"speechyou upload http {r.status_code}: {r.text[:160]}")
    return js["audio_url"]


def transcribe_speechyou(chunk_path, prompt, language, duration, public_url=None):
    key = os.environ.get("SPEECHYOU_API_KEY")
    if not key:
        raise TierUnavailable("SPEECHYOU_API_KEY not set")
    audio_url = public_url or _speechyou_upload(chunk_path, key)
    body = {"audio_url": audio_url, "model": SPEECHYOU_MODEL,
            "max_duration_seconds": max(1, int(duration or duration_of(chunk_path) or 60)),
            "storage_mode": "ephemeral", "timestamp_granularity": "segment",
            "title": Path(chunk_path).name}
    if language:
        body["language"] = language
    js = _json(_http("POST", f"{SPEECHYOU_BASE}/transcriptions", 120,
                     headers={"Authorization": f"Bearer {key}", "Idempotency-Key": hashlib.sha1(
                         f"{Path(chunk_path).name}:{os.path.getsize(chunk_path)}:{body['max_duration_seconds']}".encode()
                     ).hexdigest()}, json=body))
    status_url = js.get("status_url") or f"{SPEECHYOU_BASE}/transcriptions/{js.get('id')}"
    for _ in range(600):
        if js.get("status") == "succeeded":
            break
        if js.get("status") in ("failed", "canceled"):
            raise Transient(f"speechyou {js.get('status')}: {js.get('error') or 'no detail'}")
        time.sleep(2)
        js = _json(_http("GET", status_url, 60, headers={"Authorization": f"Bearer {key}"}))
    return parse_speechyou(js.get("result") or {}, duration, language)


def parse_speechyou(result, duration, language=None):
    segs = [{"start": float(s.get("start", 0)), "end": float(s.get("end", 0)),
             "text": (s.get("text") or "").strip()} for s in (result.get("segments") or [])]
    if not segs:
        segs = estimate_segments(result.get("text", ""), duration)
    return {"text": (result.get("text") or " ".join(s["text"] for s in segs)).strip(),
            "language": result.get("language") or language,
            "duration": float(result.get("duration_seconds") or duration or 0.0),
            "segments": segs, "timing": "segment" if result.get("segments") else "estimated",
            "model": result.get("model") or SPEECHYOU_MODEL}


def _hf_endpoint(model):
    explicit = os.environ.get("HF_ENDPOINT_URL")
    if explicit:
        return explicit
    return f"{HF_ROUTER}/{model}"


def transcribe_hf(chunk_path, prompt, language, duration):
    key = hf_token()
    if not key:
        raise TierUnavailable("HF token not set (HF_TOKEN, or run `hf auth login`)")
    payload = Path(chunk_path).read_bytes()
    js = _json(_http("POST", _hf_endpoint(HF_MODEL), 900,
                     headers={"Authorization": f"Bearer {key}", "Content-Type": "audio/mpeg"},
                     data=payload))
    if isinstance(js, list):
        js = js[0] if js else {}
    text = js.get("text") if isinstance(js, dict) else ""
    if not isinstance(text, str):
        text = ""
    return {"text": text.strip(), "language": language, "duration": duration,
            "segments": estimate_segments(text, duration), "timing": "estimated",
            "model": os.environ.get("HF_ASR_MODEL", HF_MODEL)}


def _read_16k(chunk_path):
    import numpy as np
    from scipy.io import wavfile
    wav = Path(tempfile.gettempdir()) / (Path(chunk_path).stem + "_16k.wav")
    sh(["ffmpeg", "-y", "-i", chunk_path, "-ac", "1", "-ar", "16000", wav])
    sr, x = wavfile.read(str(wav))
    if x.dtype != np.int16:
        x = (x * 32768.0).astype("int16") if x.dtype.kind == "f" else x.astype("int16")
    return x.astype("float32") / 32768.0


def transcribe_ncair(chunk_path, prompt, language, duration):
    token = hf_token()
    try:
        import torch
        from transformers import pipeline
    except Exception as e:
        raise TierUnavailable(f"ncair local deps missing (torch/transformers): {e}")
    try:
        x = _read_16k(chunk_path)
    except Exception as e:
        raise TierUnavailable(f"ncair local audio read failed: {e}")
    device = "mps" if getattr(torch.backends, "mps", None) and torch.backends.mps.is_available() else -1
    weights = NCAIR_LOCAL_DIR / "pytorch_model.bin"
    model_ref = str(NCAIR_LOCAL_DIR) if weights.exists() and weights.stat().st_size > 100_000_000 else NCAIR_MODEL
    try:
        asr = pipeline("automatic-speech-recognition", model=model_ref, token=token,
                       device=device, chunk_length_s=30)
        out = asr({"raw": x, "sampling_rate": 16000}, return_timestamps=True)
    except Exception as e:
        raise Transient(f"ncair local: {e}")
    out = out if isinstance(out, dict) else {}
    chunks = out.get("chunks")
    if chunks:
        segs = [{"start": float((c.get("timestamp") or (0, 0))[0] or 0),
                 "end": float((c.get("timestamp") or (0, 0))[1] or 0),
                 "text": (c.get("text") or "").strip()} for c in chunks]
        timing = "segment"
        text = " ".join(s["text"] for s in segs).strip()
    else:
        text = (out.get("text") or "").strip()
        segs = estimate_segments(text, duration)
        timing = "estimated"
    return {"text": text, "language": language or "yo", "duration": duration,
            "segments": segs, "timing": timing, "model": NCAIR_MODEL}


def transcribe_mms(chunk_path, prompt, language, duration):
    try:
        import torch
        import warnings
        from transformers import Wav2Vec2ForCTC, AutoProcessor
    except Exception as e:
        raise TierUnavailable(f"local MMS deps missing (torch/transformers/scipy): {e}")
    warnings.filterwarnings("ignore")
    try:
        x = _read_16k(chunk_path)
    except Exception as e:
        raise TierUnavailable(f"mms audio read failed: {e}")
    lang = "yor" if (language or "yo") in ("yo", "yor") else (language or "yor")
    proc = AutoProcessor.from_pretrained("facebook/mms-1b-all", target_lang=lang)
    model = Wav2Vec2ForCTC.from_pretrained("facebook/mms-1b-all", target_lang=lang,
                                           ignore_mismatched_sizes=True).eval()
    step = 16000 * 20
    segs = []
    for i in range(0, len(x), step):
        seg = x[i:i + step]
        if len(seg) < 16000:
            continue
        inp = proc(seg, sampling_rate=16000, return_tensors="pt")
        with torch.no_grad():
            logits = model(**inp).logits
        segs.append({"start": i / 16000, "end": min(len(x), i + step) / 16000,
                     "text": proc.decode(torch.argmax(logits, -1)[0]).strip()})
    return {"text": " ".join(s["text"] for s in segs), "language": lang,
            "duration": len(x) / 16000, "segments": segs, "timing": "segment",
            "model": "facebook/mms-1b-all"}


PROVIDERS = {
    "groq": {"fn": transcribe_groq, "key": "GROQ_API_KEY", "timing": "word",
             "label": "Groq whisper-large-v3", "model": GROQ_MODEL,
             "languages": "many, incl. yo", "cost": "free tier / paid per hour"},
    "deepinfra": {"fn": transcribe_deepinfra, "key": "DEEPINFRA_API_KEY", "timing": "word",
                  "label": "DeepInfra whisper-large-v3", "model": DEEPINFRA_MODEL,
                  "languages": "many, incl. yo", "cost": "about $0.11/hour"},
    "elevenlabs": {"fn": transcribe_elevenlabs, "key": "ELEVENLABS_API_KEY", "timing": "word",
                   "label": "ElevenLabs Scribe v2", "model": ELEVENLABS_MODEL,
                   "languages": "yo (WER 25-50% band)", "cost": "330 credits/minute"},
    "speechyou": {"fn": transcribe_speechyou, "key": "SPEECHYOU_API_KEY", "timing": "segment",
                  "label": "Speechyou STT", "model": SPEECHYOU_MODEL,
                  "languages": "yo, 1,700+", "cost": "$0.50/hour whisper, $2.00/hour pro"},
    "9jalingo": {"fn": transcribe_9jalingo, "key": "NAIJALINGO_API_KEY", "timing": "estimated",
                 "label": "9jaLingo Yoruba STT", "model": "9jalingo-stt",
                 "languages": "yo, ig, ha, pcm, en", "cost": "NGN 15/minute (NGN 900/hour)"},
    "hf": {"fn": transcribe_hf, "key": "HF_TOKEN", "timing": "estimated",
           "label": "HF Inference Endpoint", "model": HF_MODEL,
           "languages": "model dependent", "cost": "endpoint plan"},
    "ncair": {"fn": transcribe_ncair, "key": "HF_TOKEN", "timing": "segment",
              "label": "NCAIR Yoruba-ASR (local)", "model": NCAIR_MODEL,
              "languages": "yo", "cost": "free (local compute)"},
    "mms": {"fn": transcribe_mms, "key": None, "timing": "segment",
            "label": "Meta MMS 1B (local)", "model": "facebook/mms-1b-all",
            "languages": "1,100+ incl. yo", "cost": "free (local compute)"},
}


def available(name):
    if name in ("hf", "ncair"):
        return bool(hf_token())
    key = PROVIDERS[name].get("key")
    return bool(key is None or os.environ.get(key))


def list_engines():
    rows = []
    for name, p in PROVIDERS.items():
        rows.append({"engine": name, "available": available(name), "key": p.get("key"),
                     "timing": p["timing"], "model": p["model"],
                     "languages": p["languages"], "cost": p["cost"], "label": p["label"]})
    return rows


def print_engines():
    print(f"{'engine':<11} {'ready':<6} {'timing':<10} {'key':<22} model")
    for r in list_engines():
        print(f"{r['engine']:<11} {'yes' if r['available'] else 'no':<6} {r['timing']:<10} "
              f"{(r['key'] or '-'):<22} {r['model']}")


def _call_engine(name, path, prompt, language, duration, keyterms, public_url):
    fn = PROVIDERS[name]["fn"]
    if name == "elevenlabs":
        return run_tier(lambda p, pr, la, du: fn(p, pr, la, du, keyterms=keyterms),
                        str(path), prompt, language, duration)
    if name in ("9jalingo", "speechyou"):
        return run_tier(lambda p, pr, la, du: fn(p, pr, la, du, public_url=public_url),
                        str(path), prompt, language, duration)
    return run_tier(fn, str(path), prompt, language, duration)


def _split_chunk(path, duration, workdir):
    half = duration / 2.0
    out = []
    for a, b in ((0.0, half), (half, duration)):
        dst = Path(workdir) / f"split_{time.time_ns()}_{int(a * 1000):09d}.wav"
        sh(["ffmpeg", "-y", "-v", "error", "-ss", f"{a:.3f}", "-t", f"{b - a:.3f}",
            "-i", str(path), "-vn", "-ac", "1", "-ar", "16000", "-c:a", "pcm_s16le", str(dst)])
        out.append((dst, a, b - a))
    return out


def _transcribe_span(path, offset, duration, names, prompt, language, keyterms, public_url,
                     workdir, report, label, depth=0):
    """Try engines in order; split a failing chunk in half so only the bad seconds move on."""
    for name in names:
        try:
            got = _call_engine(name, path, prompt, language, duration, keyterms, public_url)
            if is_placeholder(got.get("text")):
                raise NoSpeech(f"returned {got.get('text')[:48]!r}")
            report.append({"chunk": label, "offset": round(offset, 3), "engine": name,
                           "ok": True, "timing": got.get("timing"), "model": got.get("model")})
            print(f"  {label} (+) {name} [{got.get('timing')}]", flush=True)
            return got, {name}, {got.get("timing") or "estimated"}
        except NoSpeech as e:
            report.append({"chunk": label, "offset": round(offset, 3), "engine": name,
                           "ok": False, "error": f"{type(e).__name__}: {e}"})
            print(f"  {label} (~) {name}: {e}", flush=True)
            if duration >= 2 * MIN_SPLIT_SECONDS and depth < MAX_SPLIT_DEPTH:
                parts, used, timings = [], set(), set()
                for sub, rel, subdur in _split_chunk(path, duration, workdir):
                    got2, used2, timings2 = _transcribe_span(
                        sub, offset + rel, subdur, names, prompt, language, keyterms,
                        public_url, workdir, report, f"{label}.{int(rel)}", depth + 1)
                    used |= used2
                    timings |= timings2
                    if not got2:
                        continue
                    for seg in got2["segments"]:
                        seg["start"] = round(seg["start"] + rel, 3)
                        seg["end"] = round(seg["end"] + rel, 3)
                        for w in seg.get("words", []):
                            w["start"] = round(w["start"] + rel, 3)
                            w["end"] = round(w["end"] + rel, 3)
                    parts.append(got2)
                if parts:
                    merged_timing = "word" if "word" in timings else (
                        "segment" if "segment" in timings else "estimated")
                    return ({"text": " ".join(p["text"] for p in parts).strip(),
                             "segments": [seg for p in parts for seg in p["segments"]],
                             "duration": duration, "timing": merged_timing,
                             "language": language, "model": "+".join(sorted(used))},
                            used, timings)
                return None, used, timings
            continue
        except TranscribeError as e:
            report.append({"chunk": label, "offset": round(offset, 3), "engine": name,
                           "ok": False, "error": f"{type(e).__name__}: {e}"})
            print(f"  {label} (x) {name}: {e}", flush=True)
        except Exception as e:
            report.append({"chunk": label, "offset": round(offset, 3), "engine": name,
                           "ok": False, "error": f"unexpected {type(e).__name__}: {e}"})
            print(f"  {label} (x) {name}: unexpected {type(e).__name__}: {e}", flush=True)
    return None, set(), set()


def run_tier(fn, chunk_path, prompt, language, duration, attempts=2):
    last = None
    for n in range(attempts):
        try:
            return fn(chunk_path, prompt, language, duration)
        except TranscribeError as e:
            last = e
            if not getattr(e, "retryable", False):
                raise
            time.sleep(2 * (n + 1))
    raise last


def realign(segments, offset):
    for s in segments:
        s["start"] = round(s["start"] + offset, 3)
        s["end"] = round(s["end"] + offset, 3)
        for w in s.get("words", []):
            w["start"] = round(w["start"] + offset, 3)
            w["end"] = round(w["end"] + offset, 3)
    return segments


def _resolve_engines(engines, start_tier):
    if engines:
        names = [e.strip() for e in (engines.split(",") if isinstance(engines, str) else engines) if e.strip()]
    else:
        names = list(DEFAULT_ENGINES)
    if start_tier and start_tier > 1:
        names = names[max(0, start_tier - 1):]
    unknown = [n for n in names if n not in PROVIDERS]
    if unknown:
        raise SystemExit(f"unknown engine(s): {', '.join(unknown)}; known: {', '.join(PROVIDERS)}")
    return names


def transcribe(source, out_path=None, workdir=None, chunk_seconds=300, prompt=DEFAULT_PROMPT,
               language="yo", start_tier=1, keep_going=True, no_chunk=False, txt_path=None,
               engines=None, keyterms=None, public_url=None, review_path=None,
               chapter_map=None, force=False):
    source = Path(source).resolve()
    if not source.exists():
        raise SystemExit(f"source not found: {source}")
    out_path = Path(out_path) if out_path else source.with_suffix(".raw.json")
    workdir = Path(workdir) if workdir else out_path.parent / (out_path.stem + "_chunks")
    if out_path.exists() and not force:
        raise SystemExit(f"refusing to overwrite raw transcript {out_path}; pass --force only after approval")
    if txt_path and Path(txt_path).exists() and not force:
        raise SystemExit(f"refusing to overwrite {txt_path}; pass --force only after approval")
    if review_path and Path(review_path).exists() and not force:
        raise SystemExit(f"refusing to overwrite {review_path}; pass --force only after approval")
    workdir.mkdir(parents=True, exist_ok=True)
    names = _resolve_engines(engines, start_tier)
    chapter_ranges = load_chapter_map(chapter_map)
    total = duration_of(source)
    print(f"source {source.name}  duration {total:.1f}s  chunks {chunk_seconds}s  "
          f"engines {','.join(names)}", flush=True)

    if no_chunk or total <= chunk_seconds:
        single = workdir / "chunk_000.mp3"
        extract_audio(str(source), str(single))
        chunks = [(single, 0.0)]
    else:
        chunks = chunk_audio(str(source), workdir, chunk_seconds)
    chunks = [(c, o) for (c, o) in chunks if total - o >= 1.0] or chunks

    all_segments, report, used = [], [], {}
    failed_chunks = []
    timings = set()
    for idx, (chunk, offset) in enumerate(chunks):
        cdur = min(chunk_seconds, max(0.0, total - offset)) if total else 0.0
        got, used_names, span_timings = _transcribe_span(
            chunk, offset, cdur, names, prompt, language, keyterms, public_url,
            workdir, report, str(idx))
        if got is None:
            failed_chunks.append({"chunk": idx, "offset": round(offset, 3),
                                  "duration": round(cdur, 3)})
            if keep_going:
                print(f"  chunk {idx}: all engines failed, skipped", flush=True)
                continue
            raise SystemExit(f"all engines failed on chunk {idx}")
        for name in used_names:
            used[name] = used.get(name, 0) + 1
        timings |= span_timings
        all_segments.extend(realign(got["segments"], offset))

    all_segments.sort(key=lambda s: s["start"])
    engine = "+".join(sorted(used)) or "none"
    timing = "word" if "word" in timings else ("segment" if "segment" in timings else "estimated")
    result = {"task": "transcribe", "engine": engine, "timing": timing,
              "engines_used": sorted(used), "language": language or "auto",
              "source": str(source), "duration": round(total, 3),
              "created": datetime.now(timezone.utc).isoformat(timespec="seconds"),
              "prompt_hint": prompt, "chunk_seconds": chunk_seconds,
              "tier_report": report, "failed_chunks": failed_chunks,
              "segments": all_segments,
              "text": " ".join(s["text"] for s in all_segments).strip()}
    out_path.write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print(f"wrote {out_path}  ({len(all_segments)} segments, engine {engine}, timing {timing})",
          flush=True)
    if txt_path:
        write_txt(result, txt_path)
    if review_path:
        write_review(result, review_path, chapter_ranges)
    return result


def _stamp(t):
    m, s = divmod(int(t), 60)
    h, m = divmod(m, 60)
    return f"{h:d}:{m:02d}:{s:02d}" if h else f"{m:02d}:{s:02d}"


def write_txt(result, txt_path):
    lines = [f"[{_stamp(s['start'])}] {s['text']}" for s in result["segments"]]
    Path(txt_path).write_text("\n".join(lines) + "\n", encoding="utf-8")
    print(f"wrote {txt_path}", flush=True)


def load_chapter_map(path):
    """Return chapter ranges from a job or cut-plan JSON."""
    if not path:
        return []
    try:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError) as exc:
        raise SystemExit(f"cannot read chapter map {path}: {exc}")
    chapters = data.get("chapters") if isinstance(data, dict) else None
    if not isinstance(chapters, list):
        raise SystemExit(f"chapter map {path} has no chapters array")
    ranges = []
    for chapter in chapters:
        if not isinstance(chapter, dict):
            continue
        start, end = chapter.get("start"), chapter.get("end")
        if isinstance(start, (int, float)) and isinstance(end, (int, float)) and end > start:
            ranges.append({"id": str(chapter.get("id") or ""), "start": float(start),
                           "end": float(end)})
    return ranges


def chapter_at(midpoint, ranges):
    for item in ranges:
        if item["start"] <= midpoint < item["end"]:
            return item["id"]
    return ""


def write_review(result, review_path, chapter_ranges=None):
    """Write the native-speaker review worksheet for this run."""
    path = Path(review_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    ranges = chapter_ranges or []
    with open(path, "w", newline="", encoding="utf-8") as fh:
        writer = csv.DictWriter(fh, fieldnames=[
            "line", "start", "end", "chapter", "engine", "text", "correction"])
        writer.writeheader()
        engine = result.get("engine") or ""
        for number, segment in enumerate(result.get("segments") or [], start=1):
            start, end = float(segment.get("start", 0)), float(segment.get("end", 0))
            writer.writerow({
                "line": number,
                "start": f"{start:.3f}",
                "end": f"{end:.3f}",
                "chapter": chapter_at((start + end) / 2.0, ranges),
                "engine": engine,
                "text": segment.get("text", ""),
                "correction": "",
            })
    print(f"wrote {path}", flush=True)


def main():
    ap = argparse.ArgumentParser(description="Tiered ASR for code-switched speech.")
    ap.add_argument("source", nargs="?", help="video or audio file")
    ap.add_argument("--out", help="uniform JSON output (default: <source>.raw.json)")
    ap.add_argument("--txt", help="also write [MM:SS] text lines")
    ap.add_argument("--review", help="write a review CSV (line, start, end, chapter, engine, text, correction)")
    ap.add_argument("--chapter-map", help="job or cut-plan JSON used to fill the review chapter column")
    ap.add_argument("--force", action="store_true", help="overwrite existing output (raw transcript, txt or review CSV)")
    ap.add_argument("--workdir", help="chunk/output cache dir")
    ap.add_argument("--chunk-seconds", type=int, default=300, help="chunk length, default 300")
    ap.add_argument("--prompt", default=DEFAULT_PROMPT, help="prompt hint for Whisper-style engines")
    ap.add_argument("--keyterms", default=os.environ.get("ASR_KEYTERMS"),
                    help="comma list for ElevenLabs keyterm prompting (20%% surcharge)")
    ap.add_argument("--language", default="yo", help="ISO language hint, default yo")
    ap.add_argument("--engines", help=f"ordered cascade, e.g. elevenlabs,groq (default {','.join(DEFAULT_ENGINES)})")
    ap.add_argument("--tier", type=int, default=1, choices=list(range(1, len(DEFAULT_ENGINES) + 1)),
                    help="back-compat: start at this position in the default chain")
    ap.add_argument("--no-chunk", action="store_true", help="send the whole file as one chunk")
    ap.add_argument("--strict", action="store_true", help="abort if any chunk fails all engines")
    ap.add_argument("--public-url", help="public audio URL for engines that need one (9jalingo)")
    ap.add_argument("--list-engines", action="store_true", help="show engines, keys and capabilities")
    a = ap.parse_args()
    if a.list_engines:
        print_engines()
        return
    if not a.source:
        ap.error("source is required (or use --list-engines)")
    keyterms = [k.strip() for k in (a.keyterms or "").split(",") if k.strip()]
    transcribe(a.source, out_path=a.out, workdir=a.workdir, chunk_seconds=a.chunk_seconds,
               prompt=a.prompt, language=a.language, start_tier=a.tier,
               keep_going=not a.strict, no_chunk=a.no_chunk, txt_path=a.txt,
               engines=a.engines, keyterms=keyterms, public_url=a.public_url,
               review_path=a.review, chapter_map=a.chapter_map, force=a.force)


if __name__ == "__main__":
    main()
