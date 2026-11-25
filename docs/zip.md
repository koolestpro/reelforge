# ZIP install and update

A ZIP is a snapshot. It has no git history, so it does not update itself. That is fine if you are
happy to download a new ZIP when you want the latest version.

## Install from a ZIP

1. Open https://github.com/koolestpro/reelforge/releases
2. Download the newest `reelforge-<version>.zip` asset, or `Source code (zip)`
3. Extract it. You get a folder named `reelforge-<version>`.
4. Open a terminal in that folder.
5. Run:

```
./setup.sh
./forge.sh demo
```

Then follow `docs/getting-started.md` from step 4.

## Update from a ZIP

Do not extract the new ZIP over your working folder. Keep the new code and your work separate.

1. Note these paths in your old folder. They are your work:
   - `profiles/`
   - `inbox/`
   - `work/`
   - `deliver/`
   - `config/asr.json`, if you made one
   - `.env`, if you use transcription
2. Extract the new ZIP into a fresh folder.
3. Copy the paths above from the old folder into the new one.
4. In the new folder, run:

```
./setup.sh
./forge.sh check --profile <name>
```

`.venv` is not copied on purpose. It is machine specific and setup rebuilds it.

## Why not overwrite

Overwriting mixes old and new code, and can delete a profile or a render cache. A fresh folder plus
a copy of your work is slower but predictable.

## Which version do I have

```
./forge.sh version
```

Compare it with the newest tag on the Releases page.

## Updates without ZIP

If you use git, clone the repo and run `./forge.sh update`. That is the smoother path.
