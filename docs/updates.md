# Updates

The canonical copy is https://github.com/koolestpro/reelforge. The latest version always comes from
there.

## Clone or fork, do not download a ZIP

- **Clone** if you only use the workflow: `git clone https://github.com/koolestpro/reelforge.git`
- **Fork** if you plan to change the code and contribute back. A fork keeps the upstream link.
- A ZIP or "Use this template" copy is a snapshot. It will not receive updates.

## Update a clone

```
./forge.sh update
```

It fetches tags, merges the upstream main branch with `--ff-only`, and reinstalls dependencies. It
refuses to run if you have uncommitted changes, so your work is never overwritten.

If `upstream` exists, the updater uses it. That is the normal case for a fork.

## What survives an update

- `profiles/*`, except the tracked `_template`
- `inbox/`
- `work/`
- `deliver/`
- `config/asr.json`
- `.env`

Those paths are untracked on purpose. The updater only changes code, docs, tests and the template
profile.

## If the update refuses

`git status --short` shows what is dirty. Commit or stash your code changes, or move your profile
edits into a new folder under `profiles/`, which is already ignored.

## Notifications

On GitHub, click **Watch**, then **Custom**, then tick **Releases**. You will only be notified when
a new version is tagged.

## ZIP users

A ZIP has no git history, so `./forge.sh update` cannot run. Download the new release and follow
`docs/zip.md`. Your profiles, captions, media and keys are copied across by hand.

## Roll back

Every release is a tag. To return to one:

```
git checkout v0.1.0
```

To go back to the latest:

```
git checkout main
```
