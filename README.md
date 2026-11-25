# Video clip workflow

Turn one long video into short vertical clips with clean captions, thumbnails and a manifest.

This is a reusable workflow. It carries no brand, no logo, no bundled music and no account connections.
You supply the footage, the profile and the clip ranges.

## Requirements

- Python 3.10 or newer
- ffmpeg and ffprobe on PATH
- Inter is bundled. Amiri is only needed for Arabic titles.

## Start here

- `docs/getting-started.md` for the full first-run sequence
- `docs/captions.md` for reviewed captions
- `docs/zip.md` if you use a ZIP instead of git
- `docs/updates.md` for updates

## Setup

```
./setup.sh
./forge.sh init my-studio
```

`setup.sh` creates `.venv`, installs the Python dependencies and checks ffmpeg.
`./forge.sh init` copies `profiles/_template/` to `profiles/my-studio/` and makes the folders.
For transcription later, copy `env.example` to `.env` and fill your own keys.

## Use

```
cp talk.mp4 inbox/
$EDITOR profiles/my-studio/profile.json
$EDITOR profiles/my-studio/clips.csv
./forge.sh check --profile my-studio
./forge.sh render --profile my-studio
```

Output lands in `deliver/my-studio/<date>/`:

```
clips/        one mp4 and one jpg per clip
captions/     sidecar srt after the captions step
manifest.json profile, ranges, files, hashes
queue.csv     id, title, file, thumbnail, caption
```

Nothing is uploaded. No account is connected. No API key is needed to render.

## Commands

| Command | What it does |
| --- | --- |
| `./forge.sh init NAME` | Create a profile from the template |
| `./forge.sh check --profile NAME` | Validate profile, clips, source and tools |
| `./forge.sh render --profile NAME` | Render every clip |
| `./forge.sh render --profile NAME --id c1` | Render one clip |
| `./forge.sh captions --profile NAME` | Burn reviewed captions into delivered clips |
| `./forge.sh transcribe --profile NAME` | Transcribe with your own key |
| `./forge.sh demo` | Synthetic end to end run, no footage or key |

## Profiles

One clone can hold several profiles, one per person or channel. Each profile has its own logo,
palette, fonts, clip list and output folder. They do not read each other's files.

## Updates

The canonical copy is https://github.com/koolestpro/reelforge. Clone or fork it, not a ZIP, if you
want updates.

```
./forge.sh update
```

Your profiles, media, work folders and keys survive an update. See `docs/updates.md`.

## What it does not do

- It does not choose what to cut. You supply the ranges.
- It does not post, schedule or upload.
- It does not bundle music or sound effects. It generates neutral ones on first render.
- It does not replace an editor for complex work.

## Licence

FSL-1.1-MIT. Free to use, copy, modify and share for any purpose except selling it or offering it as
a competing product. Each release becomes MIT two years after it is published. Read `LICENSE`.
