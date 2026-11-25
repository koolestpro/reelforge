# Transcription

Transcription is optional. Rendering works without it.

## Provider order

Copy `config/asr.example.json` to `config/asr.json` and edit the order. `config/asr.json` is
gitignored, so an update never overwrites it. If the file is absent, the example is used.

| Provider | Model | Notes |
| --- | --- | --- |
| `elevenlabs` | Scribe v2 | Word timings. Recommended first choice |
| `9jalingo` | Yoruba STT | Text only, needs a public audio URL |
| `groq` | whisper-large-v3 | Word timings, free tier limits |
| `deepinfra` | whisper-large-v3 | Word timings, paid |
| `hf` | any endpoint | Text only |
| local | whisper or MMS | Offline, needs the model installed |

## Keys

Put keys in `.env` at the repo root. The engine reads only that file. Never commit it.

```
ELEVENLABS_API_KEY=
GROQ_API_KEY=
DEEPINFRA_API_KEY=
```

## Cost

`./forge.sh transcribe` prints the provider and model before it spends. Long sources are split at a
silence below the provider limit. Raw part transcripts are kept beside the merged result.

## Review

A timestamped transcript is not a reviewed transcript. `wer_review.py` compares a reviewed worksheet
against the machine output and reports word error rate.

## Importing an existing transcript

If you already have an SRT, convert it to the captions JSON shape and pass it to the renderer.
Transcription is not required.
