#!/usr/bin/env python3
"""Render the rolled-in title lockup.

The logo, when present, rolls in from the right, and the
Yoruba title and English subheading are revealed in its wake.

Test video:
    python3 title_anim.py --source source.mp4 --at 5 --title-yo "..." --title-en "..." --out test.mp4

Alpha frames for a chapter overlay:
    python3 title_anim.py --title-yo "..." --title-en "..." --alpha-dir out/frames --duration 1.2
"""
import argparse
import math
import subprocess
from pathlib import Path

from PIL import Image, ImageChops, ImageDraw, ImageOps

import overlays as cards

HERE = Path(__file__).resolve().parent
DEFAULT_LOGO = None
W = 1080
H = 1080
Y_CENTER = 735
X0 = 60
LOGO_SIZE = 190
GOLD = (210, 210, 210)


def background_from(source, at, out):
    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-ss", str(at), "-i", str(source),
        "-frames:v", "1", "-q:v", "2", str(out),
    ], check=True)
    return ImageOps.fit(Image.open(out).convert("RGB"), (W, H), Image.LANCZOS)


def dark_background():
    im = Image.new("RGB", (W, H), (13, 59, 46))
    d = ImageDraw.Draw(im)
    for y in range(H):
        k = y / H
        d.line([(0, y), (W, y)], fill=(int(13 + 3 * k), int(59 - 22 * k), int(46 - 16 * k)))
    return im


def _fit_size(text, font_path, max_size, min_size, maxw, raqm=False, **kw):
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    size = max_size
    while size > min_size:
        f = cards.font(font_path, size, raqm)
        if probe.textlength(text, font=f, **kw) <= maxw:
            return size
        size -= 2
    return min_size


def _wrap_two(text, font_path, max_size, min_size, maxw):
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))
    size = max_size
    while size >= min_size:
        f = cards.font(font_path, size)
        lines = cards._wrap(text, f, maxw)
        if len(lines) <= 2:
            return lines, size
        size -= 2
    f = cards.font(font_path, min_size)
    return cards._wrap(text, f, maxw), min_size


def build_lockup(title_yo, title_en, logo_path=DEFAULT_LOGO, title_small=None,
                 style="default"):
    """Return (rotating emblem, fully revealed title layer)."""
    global X0
    cards.set_size(W, H)
    logo = Image.new("RGBA", (LOGO_SIZE, LOGO_SIZE), (0, 0, 0, 0))
    if logo_path and Path(logo_path).exists():
        logo = Image.open(logo_path).convert("RGBA").resize((LOGO_SIZE, LOGO_SIZE), Image.LANCZOS)
    title = Image.new("RGBA", (W, H), (0, 0, 0, 0))
    maxw = W - 204 - (LOGO_SIZE if logo_path else 0)
    logo_center = Y_CENTER + (LOGO_SIZE / 2 if logo_path else 0)
    probe = ImageDraw.Draw(Image.new("RGBA", (10, 10)))

    en_path = cards.inter("Bold")
    en_lines, en_size = _wrap_two(title_en.upper(), en_path, 42, 30, maxw)
    en_lh = max(36, int(en_size * 1.06))

    arabic = style == "arabic_translit" or any("\u0600" <= ch <= "\u06ff" for ch in title_yo)
    main_path = cards.arabic_font() if arabic else cards.inter("ExtraBold")
    main_kw = {"direction": "rtl", "language": "ar"} if arabic else {}
    small_size, small_font, small_w = 0, None, 0
    if title_small:
        small_size = _fit_size(title_small, en_path, 34, 26, maxw)
        small_font = cards.font(en_path, small_size)
        small_w = probe.textlength(title_small, font=small_font)
    main_limit = maxw - (small_w + 12 if title_small else 0)
    main_size = _fit_size(title_yo, main_path, 80, 46, main_limit, raqm=arabic, **main_kw)
    main_font = cards.font(main_path, main_size, arabic)

    X0 = 90
    x = X0 + (LOGO_SIZE + 54 if logo_path else 0)

    block_h = main_size + 24 + len(en_lines) * en_lh
    main_top = int(logo_center - block_h / 2)
    sub_top = main_top + main_size + 24

    title = cards.shadowed(title, (x, main_top), title_yo, main_font,
                           (255, 255, 255, 255), off=2, blur=7, alpha=170, **main_kw)
    if title_small:
        main_w = probe.textlength(title_yo, font=main_font, **main_kw)
        small_y = main_top + int((main_size - small_size) * 0.55)
        title = cards.shadowed(title, (x + main_w + 12, small_y), title_small, small_font,
                               GOLD + (255,), off=1, blur=4, alpha=200)
    for i, line in enumerate(en_lines):
        y = sub_top + i * en_lh
        line_x = x
        title = cards.shadowed(title, (line_x, y), line,
                               cards.font(en_path, en_size), GOLD + (255,),
                               off=1, blur=4, alpha=200)
    rule_bottom = sub_top + len(en_lines) * en_lh + 10
    # no rule by default
    return logo, title


def pose(t, roll, settle):
    """(x, y, angle) at time t. After roll + settle the lockup stays settled."""
    if t <= roll:
        p = min(1.0, t / roll) if roll > 0 else 1.0
        ease = 1 - (1 - p) ** 3
        x = W + 140 + (X0 - (W + 140)) * ease
        angle = 1080 * ease
        y = Y_CENTER
    else:
        q = min(1.0, (t - roll) / settle) if settle > 0 else 1.0
        x = X0
        angle = 1080
        y = Y_CENTER - 10 * math.sin(math.pi * q)
    return x, y, angle


def compose(logo, title, x, y, angle, base=None):
    """One transparent frame with the emblem and the text revealed behind it.
    base may be the dark bottom gradient, so the lockup keeps its old readable base."""
    right = max(0, min(W, int(x + LOGO_SIZE)))
    mask = Image.new("L", (W, H), 0)
    ImageDraw.Draw(mask).rectangle([right, 0, W, H], fill=255)
    title_masked = title.copy()
    title_masked.putalpha(ImageChops.multiply(title_masked.getchannel("A"), mask))
    rot = logo.rotate(angle, resample=Image.BICUBIC, expand=True)
    px = int(x + LOGO_SIZE / 2 - rot.width / 2)
    py = int(y + LOGO_SIZE / 2 - rot.height / 2)
    frame = base.copy() if base is not None else Image.new("RGBA", (W, H), (0, 0, 0, 0))
    frame.alpha_composite(title_masked)
    frame.alpha_composite(rot, (px, py))
    return frame


def render_alpha(title_yo, title_en, out_dir, duration=1.2, roll=0.9, settle=0.3,
                 reverse=False, settled_path=None, fps=30, logo_path=DEFAULT_LOGO,
                 title_small=None, style="default", width=None, height=None):
    """Write a transparent PNG sequence. Returns the actual sequence duration."""
    global W, H, Y_CENTER
    if width:
        W = int(width)
    if height:
        H = int(height)
    Y_CENTER = int(H * 0.68)
    cards.set_size(W, H)
    out_dir = Path(out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    for old in out_dir.glob("frame_*.png"):
        old.unlink()
    logo, title = build_lockup(title_yo, title_en, logo_path, title_small, style)
    gradient = cards.grad(cards.S(980), 240, 1.35)
    frames = max(2, int(round(duration * fps)) + 1)
    order = list(range(frames))
    if reverse:
        order.reverse()
    for n, i in enumerate(order):
        x, y, angle = pose(i / fps, roll, settle)
        compose(logo, title, x, y, angle, gradient).save(out_dir / f"frame_{n:04d}.png")
    if settled_path:
        x, y, angle = pose(roll + settle, roll, settle)
        compose(logo, title, x, y, angle, gradient).save(settled_path)
    return (frames - 1) / fps


def render_video(args):
    cards.set_size(W, H)
    if args.background:
        base = ImageOps.fit(Image.open(args.background).convert("RGB"), (W, H), Image.LANCZOS)
    elif args.source:
        base = background_from(args.source, args.at, Path(args.out).with_suffix(".bg.jpg"))
    else:
        base = dark_background()

    logo, title = build_lockup(args.title_yo, args.title_en, args.logo,
                               args.title_small or None, args.style)
    gradient = cards.grad(cards.S(980), 240, 1.35)
    work = Path(args.out).with_suffix(".frames")
    work.mkdir(parents=True, exist_ok=True)
    frames = max(1, int(args.duration * args.fps))
    for i in range(frames):
        x, y, angle = pose(i / args.fps, 0.9, 0.25)
        frame = base.copy().convert("RGBA")
        frame.alpha_composite(compose(logo, title, x, y, angle, gradient))
        frame.convert("RGB").save(work / f"frame_{i:04d}.png")

    subprocess.run([
        "ffmpeg", "-nostdin", "-v", "error", "-y", "-framerate", str(args.fps),
        "-i", str(work / "frame_%04d.png"), "-c:v", "libx264", "-pix_fmt", "yuv420p",
        "-crf", "18", "-preset", "medium", "-movflags", "+faststart", args.out,
    ], check=True)
    print(f"wrote {args.out} ({args.duration}s, {frames} frames)")


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--source", default="")
    ap.add_argument("--at", type=float, default=5.0)
    ap.add_argument("--background", default="")
    ap.add_argument("--logo", default=str(DEFAULT_LOGO))
    ap.add_argument("--title-yo", required=True)
    ap.add_argument("--title-en", required=True)
    ap.add_argument("--title-small", default="")
    ap.add_argument("--style", default="default")
    ap.add_argument("--out", default="")
    ap.add_argument("--alpha-dir", default="")
    ap.add_argument("--settled", default="")
    ap.add_argument("--duration", type=float, default=2.0)
    ap.add_argument("--roll", type=float, default=0.9)
    ap.add_argument("--settle", type=float, default=0.25)
    ap.add_argument("--reverse", action="store_true")
    ap.add_argument("--fps", type=int, default=30)
    a = ap.parse_args()
    if a.alpha_dir:
        actual = render_alpha(a.title_yo, a.title_en, a.alpha_dir, a.duration, a.roll, a.settle,
                              a.reverse, a.settled or None, a.fps, a.logo)
        print(f"wrote {a.alpha_dir} ({actual:.3f}s)")
        return
    if not a.out:
        raise SystemExit("--out is required unless --alpha-dir is set")
    render_video(a)


if __name__ == "__main__":
    main()
