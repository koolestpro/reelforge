# Changelog

All notable changes to reelforge are recorded here. Versions follow tags.

## 0.2.0 - 2026-11-25

- Captions command: reviewed source-time SRT mapped through cuts and speed, burned into delivered
  clips, with a sidecar SRT in `deliver/<profile>/<date>/captions/`.
- Captions are idempotent: they are rebuilt from the clean render master, never stacked.
- Inter is bundled in `forge/fonts/` with its OFL licence, so titles look the same on any machine.
- `setup.sh` downloads the optional YuNet face detector for landscape sources.
- New guides: getting started, captions, ZIP install and update.
- README links the guides and lists the captions command.

## 0.1.0 - 2026-11-25

- First public release.
- One long source in, short clips out from `clips.csv`.
- Profiles per person or channel, with isolated assets and deliver folders.
- Neutral overlay, title and caption rendering with no bundled brand.
- Synthetic sound effects generated on first render.
- Optional transcription with your own key and provider order in config.
- Optional Islamic citation and screening plugin, off by default.
- Deliver manifest and queue CSV.
- `./forge.sh update` for clones and forks.
