# Troubleshooting

## `ffmpeg is required`

Install ffmpeg and ffprobe, then run `./setup.sh` again. On macOS: `brew install ffmpeg`.

## `Inter font not found`

Install Inter, or put `Inter-Bold.otf` and `Inter-ExtraBold.otf` in `forge/fonts/`.
Arabic titles need `Amiri-Bold.ttf` in the same folder.

## `no source file in inbox/`

Put exactly one video or audio file in `inbox/`, or set `source` in the profile to a path.

## `clips.csv mixes aspects`

Render one aspect per run. Move the odd clips into a second profile or change them to `source`.

## `overlaps` or `end must be after start`

The clip list is contradictory. Fix the times. Overlaps are rejected on purpose.

## Render is slow

The first run denoises audio and tracks faces. Later runs reuse the work folder for the same date.
Delete `work/<profile>/<date>/` to force a rebuild.

## Output is black at the start

The clip opens on its thumbnail. If the thumbnail frame is black, the source frame at that point is
black. Move the clip start.

## Nothing appears in `deliver/`

Rendering succeeded but collection failed. Check the console error, then run `./forge.sh render`
again. `deliver/<profile>/<date>/manifest.json` lists what was written.

## A profile cannot see another profile's files

That is intended. Each profile has its own folder and deliver path.

## Keys

Only `./forge.sh transcribe` uses an API key. Put keys in `.env` at the repo root. Never commit it.
