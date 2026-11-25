# Getting started

This is the sequence for a person using the workflow for the first time. It assumes macOS or Linux.

## 1. Install the prerequisites

- Python 3.10 or newer
- ffmpeg and ffprobe on PATH
- git, if you clone
- Inter font. It is bundled, so usually nothing to do

macOS:

```
brew install ffmpeg
```

Linux:

```
sudo apt install ffmpeg
```

## 2. Get the code

Clone, if you want updates:

```
git clone https://github.com/koolestpro/reelforge.git
cd reelforge
```

Or download a ZIP from the Releases page and extract it. A ZIP does not update itself.

## 3. Set up once

```
./setup.sh
./forge.sh demo
```

`setup.sh` creates `.venv`, installs the Python packages and checks ffmpeg. `demo` renders a
synthetic clip with no footage and no key. If `demo` fails, stop and fix the environment before
using real video.

## 4. Make a profile

```
./forge.sh init ahmed
```

This creates `profiles/ahmed/`. Each person, channel or client should have its own profile.

## 5. Add the source

Put exactly one video or audio file in `inbox/`, or set `source` in
`profiles/ahmed/profile.json` to a path.

## 6. Fill the profile

Open `profiles/ahmed/profile.json`:

- `studio`: the name on the profile
- `series`: the label used on the title card
- `output.aspect`: `9:16`, `4:5`, `3:4`, `1:1` or `source`
- `output.speed`: `1.0` unless you have a reason

A logo is optional. Without one, the title renders clean with no watermark.

## 7. Write the clip list

Open `profiles/ahmed/clips.csv`:

```csv
id,start,end,title,subtitle,aspect
c1,00:02:14,00:03:02,Opening story,,9:16
```

Rules: end after start, range inside the source, no overlaps, one title per clip. The engine does
not choose what to cut.

## 8. Check before rendering

```
./forge.sh check --profile ahmed
```

Fix every error before a render. Check is cheap. A render is not.

## 9. Render

```
./forge.sh render --profile ahmed
```

Or one clip:

```
./forge.sh render --profile ahmed --id c1
```

## 10. Captions

If you need a transcript first, copy the key file and fill one provider:

```
cp env.example .env
# edit .env, for example ELEVENLABS_API_KEY=...
./forge.sh keys
./forge.sh transcribe --profile ahmed
```

`./forge.sh keys` shows which keys are set and never prints a value. Keys are yours alone. Do not
share them with the person who set this up for you.

Put the reviewed source-time SRT at `profiles/ahmed/captions.srt`, then:

```
./forge.sh captions --profile ahmed
```

See `docs/captions.md`. Captions are optional but recommended.

## 11. Collect the output

```
deliver/ahmed/<date>/clips/       mp4 and jpg per clip
deliver/ahmed/<date>/captions/    sidecar srt
deliver/ahmed/<date>/manifest.json
deliver/ahmed/<date>/queue.csv
```

Nothing is uploaded. No account is connected.

## 12. Keep it current

Clone:

```
./forge.sh update
```

ZIP: download the new release and follow `docs/zip.md`.

Your profiles, captions, media, work and keys are ignored by git, so an update does not overwrite
them.

## If something breaks

- `docs/troubleshooting.md`
- `./forge.sh check --profile <name>` for a readable error
- `./forge.sh demo` to prove the install is sound

## What this does not do

It does not post, schedule, translate, choose clips, or supply music. It does not replace an editor
for complex work.
