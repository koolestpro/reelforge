# clips.csv

The clip list is the editorial decision. The engine does not choose what to cut.

## Columns

| Column | Required | Meaning |
| --- | --- | --- |
| `id` | yes | Short unique id, for example `c1` |
| `start` | yes | Source start time |
| `end` | yes | Source end time |
| `title` | yes | Main title line |
| `subtitle` | no | Second line |
| `aspect` | no | `source`, `9:16`, `4:5`, `3:4` or `1:1` |
| `slug` | no | File name fragment. Generated from the title when empty |

## Times

`75`, `01:15` and `1:01:15` all work. Seconds may have decimals.

## Rules

- `end` must be after `start`.
- The range must sit inside the source duration.
- Clips may not overlap.
- One run uses one aspect. Leave `aspect` empty or `source` to use the profile setting.
- `title` is required. It becomes the title lockup and the queue caption.

## Example

```csv
id,start,end,title,subtitle,aspect
c1,00:00:30,00:01:05,Opening story,,9:16
c2,00:02:10,00:02:48,The turning point,What changed after that day,source
```

## Getting the times

Play the source, note the in and out points, and write them down. `./forge.sh transcribe` gives you
a timestamped transcript if a time-coded reference helps.
