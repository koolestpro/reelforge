"""Burned-in sentence subtitles (one short sentence at a time).

Renders each caption as a transparent PNG with Pillow and overlays it with an
enable window, because this ffmpeg build has no libass/drawtext filters.

    python3 subtitles.py clip.mp4 captions.json out.mp4

captions.json:
    {"y_bottom": 1340, "font_size": 46,
     "captions": [{"start": 1.0, "end": 4.8, "text": "..."}]}

Times are in the clip's own timeline (after cut and speed).
"""
import argparse, json, subprocess, sys, tempfile
from pathlib import Path
from PIL import Image, ImageDraw, ImageFilter
import overlays as cards

FADE = 0.16


def _probe_size(video):
    out = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0',
                          '-show_entries', 'stream=width,height', '-of', 'csv=p=0', str(video)],
                         capture_output=True, text=True, check=True).stdout.strip()
    w, h = out.split(',')[:2]
    return int(w), int(h)


def render(text, path, w, h, y_bottom=1720, size=34, weight='SemiBold', stroke=0, alpha=235, box=False, box_alpha=130):
    f = cards.font(cards.inter(weight), size)
    lines = text.split('\n') if '\n' in text else cards._wrap(text, f, int(w * 0.82))
    lh = int(size * 1.3)
    im = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    y = y_bottom - lh * len(lines)
    if box:
        _d = ImageDraw.Draw(Image.new('RGBA', (10, 10)))
        maxw = max(_d.textlength(line, font=f) for line in lines)
        pad = max(12, int(size * 0.30))
        ImageDraw.Draw(im).rounded_rectangle(
            [(w - maxw) / 2 - pad, y - pad, (w + maxw) / 2 + pad,
             y + (len(lines) - 1) * lh + size + pad],
            radius=max(12, int(size * 0.35)), fill=(0, 0, 0, box_alpha))
    sh = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    ds = ImageDraw.Draw(sh)
    for i, line in enumerate(lines):
        ds.text((w // 2 + 2, y + i * lh + 3), line, font=f, fill=(0, 0, 0, 165), anchor='mt')
    im = Image.alpha_composite(im, sh.filter(ImageFilter.GaussianBlur(6)))
    d = ImageDraw.Draw(im)
    for i, line in enumerate(lines):
        d.text((w // 2, y + i * lh), line, font=f, fill=(255, 255, 255, alpha),
               anchor='mt', stroke_width=stroke, stroke_fill=(0, 0, 0, 180))
    im.save(path)
    return len(lines)


def render_word_group(words, active, path, w, h, y_bottom=940, size=58, weight='Bold', stroke=2, alpha=255, box=False, box_alpha=130):
    """One caption block with the active word in gold and the rest in white."""
    f = cards.font(cards.inter(weight), size)
    d = ImageDraw.Draw(Image.new('RGBA', (10, 10)))
    space = d.textlength(' ', font=f)
    widths = [d.textlength(t, font=f) for t in words]
    total = sum(widths) + space * max(0, len(words) - 1)
    maxw = int(w * 0.88)
    if total > maxw:
        size = max(28, int(size * maxw / total))
        f = cards.font(cards.inter(weight), size)
        space = d.textlength(' ', font=f)
        widths = [d.textlength(t, font=f) for t in words]
        total = sum(widths) + space * max(0, len(words) - 1)
    x0 = (w - total) / 2
    y = y_bottom - size * 1.15
    im = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    if box:
        pad = max(12, int(size * 0.30))
        ImageDraw.Draw(im).rounded_rectangle(
            [x0 - pad, y - pad, x0 + total + pad, y + size + pad],
            radius=max(12, int(size * 0.35)), fill=(0, 0, 0, box_alpha))
    sh = Image.new('RGBA', (w, h), (0, 0, 0, 0))
    ds = ImageDraw.Draw(sh)
    x = x0
    for t, tw in zip(words, widths):
        ds.text((x, y + 4), t, font=f, fill=(0, 0, 0, 190))
        x += tw + space
    im = Image.alpha_composite(im, sh.filter(ImageFilter.GaussianBlur(7)))
    d = ImageDraw.Draw(im)
    x = x0
    for i, (t, tw) in enumerate(zip(words, widths)):
        fill = cards.GOLD + (255,) if i == active else (255, 255, 255, alpha)
        d.text((x, y), t, font=f, fill=fill, stroke_width=stroke,
               stroke_fill=(0, 0, 0, 210))
        x += tw + space
    im.save(path)
    return len(words)


def _groups(words, max_words=3):
    out, cur = [], []
    for wd in words:
        cur.append(wd)
        if len(cur) >= max_words or wd['text'][-1:] in '.!?':
            out.append(cur); cur = []
    if cur: out.append(cur)
    return out


def burn_words(video, words, out, y_bottom=940, size=58, weight='Bold', stroke=2,
               alpha=255, max_words=3, groups=None, box=False, box_alpha=130):
    video, out = Path(video), Path(out)
    w, h = _probe_size(video)
    groups = groups or _groups(sorted(words, key=lambda x: x['start']), max_words)
    tmp = Path(tempfile.mkdtemp(prefix='words_'))
    layers = []
    for gi, group in enumerate(groups):
        texts = [wd['text'] for wd in group]
        for wi, wd in enumerate(group):
            p = tmp / f'g{gi:02d}_{wi}.png'
            render_word_group(texts, wi, p, w, h, y_bottom, size, weight, stroke, alpha, box, box_alpha)
            a = float(wd['start'])
            b = float(group[wi + 1]['start']) if wi + 1 < len(group) else float(group[-1]['end'])
            layers.append((p, a, b))
    inp = ['-i', str(video)]
    for p, a, b in layers:
        inp += ['-loop', '1', '-framerate', '30', '-t', f'{b + 0.1:.2f}', '-i', str(p)]
    fc = '[0:v]fps=30,setsar=1[base];'
    last = 'base'
    for i, (p, a, b) in enumerate(layers):
        fc += f"[{i + 1}:v]format=rgba[s{i}];[{last}][s{i}]overlay=0:0:enable='between(t,{a:.3f},{b:.3f})':format=auto[o{i}];"
        last = f'o{i}'
    cmd = (['ffmpeg', '-nostdin', '-v', 'error', '-y'] + inp +
           ['-filter_complex', fc, '-map', f'[{last}]', '-map', '0:a?',
            '-c:v', 'libx264', '-crf', '19', '-preset', 'fast', '-pix_fmt', 'yuv420p',
            '-c:a', 'copy', '-movflags', '+faststart', str(out)])
    subprocess.run(cmd, check=True)
    return out


def burn(video, captions, out, y_bottom=1720, size=34, weight='SemiBold', stroke=0, alpha=235, box=False, box_alpha=130):
    video, out = Path(video), Path(out)
    w, h = _probe_size(video)
    caps = sorted(captions, key=lambda c: c['start'])
    tmp = Path(tempfile.mkdtemp(prefix='subs_'))
    pngs = []
    for i, c in enumerate(caps):
        p = tmp / f'sub_{i:03d}.png'
        render(c['text'], p, w, h, y_bottom, size, weight, stroke, alpha, box, box_alpha)
        pngs.append(p)
    inp = ['-i', str(video)]
    for i, c in enumerate(caps):
        inp += ['-loop', '1', '-framerate', '30', '-t', f"{c['end'] + 0.1:.2f}", '-i', str(pngs[i])]
    fc = '[0:v]fps=30,setsar=1[base];'
    last = 'base'
    for i, c in enumerate(caps):
        a, b = float(c['start']), float(c['end'])
        d = min(FADE, max(0.04, (b - a) / 2))
        fc += (f"[{i + 1}:v]format=rgba,fade=t=in:st={a:.3f}:d={d:.3f}:alpha=1,"
               f"fade=t=out:st={b - d:.3f}:d={d:.3f}:alpha=1[s{i}];"
               f"[{last}][s{i}]overlay=0:0:enable='between(t,{a:.3f},{b:.3f})':format=auto[o{i}];")
        last = f'o{i}'
    cmd = (['ffmpeg', '-nostdin', '-v', 'error', '-y'] + inp +
           ['-filter_complex', fc, '-map', f'[{last}]', '-map', '0:a?',
            '-c:v', 'libx264', '-crf', '19', '-preset', 'fast', '-pix_fmt', 'yuv420p',
            '-c:a', 'copy', '-movflags', '+faststart', str(out)])
    subprocess.run(cmd, check=True)
    return out


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument('video')
    ap.add_argument('captions')
    ap.add_argument('out')
    ap.add_argument('--words', action='store_true', help='word-by-word karaoke mode (captions file has "words")')
    a = ap.parse_args()
    spec = json.loads(Path(a.captions).read_text())
    style = spec.get('style') or {}

    def pick(key, default):
        return spec[key] if key in spec else style.get(key, default)

    if a.words:
        burn_words(a.video, spec.get('words'), a.out,
                   y_bottom=int(pick('y_bottom', 940)), size=int(pick('font_size', 58)),
                   weight=pick('weight', 'Bold'), stroke=int(pick('stroke', 2)),
                   alpha=int(pick('alpha', 255)), max_words=int(pick('max_words', 3)),
                   groups=spec.get('groups'), box=bool(pick('box', False)),
                   box_alpha=int(pick('box_alpha', 130)))
    else:
        burn(a.video, spec['captions'], a.out,
             y_bottom=int(pick('y_bottom', 1720)), size=int(pick('font_size', 34)),
             weight=pick('weight', 'SemiBold'), stroke=int(pick('stroke', 0)),
             alpha=int(pick('alpha', 235)), box=bool(pick('box', False)),
             box_alpha=int(pick('box_alpha', 130)))
    print(a.out)


if __name__ == '__main__':
    main()
