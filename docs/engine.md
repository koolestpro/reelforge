# Engine

The engine takes one source file plus a clip list and produces one clip per row.

## Flow

1. `config.py` reads the profile and `clips.csv`, checks the ranges, probes the source and writes
   `work/<profile>/<date>/job.json`.
2. `check.py` validates the job before any heavy work. It exits non-zero on a broken range, a missing
   source, a cached aspect mismatch or a missing tool.
3. `render.py` runs the pipeline:
   - repair variable frame rate and crop around the subject
   - optionally denoise speech
   - cut each chapter and set the frame
   - build the title lockup and any text cards
   - burn captions when a transcript is supplied
   - hold the thumbnail at the start
4. `deliver.py` copies the finished files into `deliver/<profile>/<date>/` and writes the manifest
   and queue CSV.

## Modules

| Module | Role |
| --- | --- |
| `config.py` | Profiles, clip list, time parsing, job building |
| `cli.py` | `init`, `check`, `render`, `transcribe`, `demo` |
| `render.py` | Main renderer |
| `check.py` | Preflight |
| `cut_plan.py` | Boundary evidence and protected ranges |
| `overlays.py` | Titles, cards, gradients, fonts |
| `title_anim.py` | Rolled-in title lockup |
| `subtitles.py` | Burned captions |
| `map_captions.py` | Source time to clip time |
| `intake.py` | Source probe and report |
| `audio_chunks.py`, `audio_join.py`, `adobe_chunks.py` | Manual audio enhancement |
| `detect_pauses.py`, `split_for_asr.py` | Long source splitting |
| `enhance_clip.py`, `dfn_run.py` | Frame repair and denoise |
| `asr.py`, `wer_review.py` | Transcription and review |
| `capcut_build.py` | Optional editable CapCut export |
| `deliver.py` | Deliver folder and manifest |

## Invariants

- One aspect per run.
- No clip ends past the source duration.
- No overlapping clips.
- The renderer never invents a cut.
- `deliver/` is written only by `deliver.py`.
