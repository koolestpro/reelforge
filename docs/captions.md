# Captions

Captions are burned into the delivered clip from a reviewed, source-time SRT. The engine maps each
cue through the real cuts and speed. It does not translate and it does not invent text.

## 1. Get a transcript

```
./forge.sh transcribe --profile ahmed
```

You need your own key in `.env`. The transcript is a starting point, not a finished subtitle.

## 2. Review it

Correct the words, punctuation and line breaks by hand. Captions are read on a phone with the sound
off, so the text must stand alone. Keep one short sentence per cue where you can.

Save the reviewed file as:

```
profiles/ahmed/captions.srt
```

Times are source-video times. A clip that starts at 12:40 uses the cues at 12:40, not cues starting
at zero.

## 3. Render, then caption

```
./forge.sh render --profile ahmed
./forge.sh captions --profile ahmed
```

Or one clip:

```
./forge.sh captions --profile ahmed --id c1
```

Captions are burned into the delivered file from the clean render master, so running the command
again replaces the captions instead of stacking another layer.

## Output

```
deliver/ahmed/<date>/clips/<name>.mp4       captioned video
deliver/ahmed/<date>/captions/<name>.srt    sidecar for platforms that accept captions
```

`manifest.json` records `captions: true` and the sidecar path.

## Style

`style.json` controls the caption defaults:

- two lines maximum
- font size and bottom position scale to the output height
- the first cue is held clear of the thumb-first opening

Change the numbers there, not in the code.

## If a cue is missing

- Check the cue sits inside the clip range.
- Check the timemap exists: render the clip first.
- Check the SRT uses `HH:MM:SS,mmm --> HH:MM:SS,mmm`.
- A cue that starts and ends inside a cut gap is dropped, because that moment is not in the clip.

## Screening

If the optional plugin is enabled, run the screening check before burning:

```
FORGE_PLUGINS=islamic ./forge.sh check --profile ahmed
```

The captions command itself does not rewrite your text. Review is human work.
