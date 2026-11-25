# Configuration

Two files per profile: `profile.json` for identity and output, `style.json` for craft numbers.
Both live in `profiles/<name>/`.

## profile.json

| Key | Type | Meaning |
| --- | --- | --- |
| `studio` | string | Your name or channel |
| `series` | string | Series label, used as the title card kicker |
| `source` | string | Path to the source video. Empty means use the only file in `inbox/` |
| `logo` | string or null | Optional. Not used by default |
| `watermark` | bool | Reserved. Off by default |
| `fonts.display` | string or null | Path to a display font. Inter is used when null |
| `fonts.body` | string or null | Path to a body font |
| `palette.bg` | hex | Background |
| `palette.fg` | hex | Main text |
| `palette.muted` | hex | Secondary text |
| `palette.accent` | hex | Accent, neutral by default |
| `output.aspect` | `9:16`, `4:5`, `3:4`, `1:1` or `source` | Output framing |
| `output.speed` | number | Playback speed, 1.0 by default |
| `output.crf` | number | Quality, reserved |
| `naming` | string | Reserved naming pattern |

## style.json

| Key | Default | Meaning |
| --- | --- | --- |
| `thumb_first.hold` | 0.35 | Seconds the thumbnail holds at the start |
| `thumb_first.fade` | 0.45 | Dissolve into the clip |
| `zoom` | 1.0 | Constant zoom. No movement |
| `teaser_max_seconds` | 10 | Teaser length cap |
| `title.fade_in` | 1.5 | Seconds before the title appears |
| `title.position` | bottom-left | Title placement |
| `captions.mode` | burn-in | Burned in, or sidecar when supported |
| `captions.max_lines` | 2 | Caption line cap |

## Profiles are isolated

Each profile has its own logo, palette, fonts, clip list and deliver folder. Rendering one profile
never reads another profile's files.
