"""Image cards (titles, message cards, text panel, edge bar, intro and outro text).
Default size 1080x1920; call set_size(w, h) for other aspect ratios (1080 wide: 1:1, 4:5, 3:4).
Vertical positions/heights below were designed for a 1920-tall frame and go through S() to scale to the real height."""
import glob, subprocess
from pathlib import Path
import numpy as np
from PIL import Image, ImageDraw, ImageFont, ImageFilter
W, H = 1080, 1920
LOGO_PATH = Path(__file__).resolve().parents[2] / 'profile' / 'assets' / 'logo.png'

def set_size(w, h):
    global W, H
    W, H = w, h

def S(v):
    """scale a vertical position/length designed for a 1920-tall frame (identity at 1920)"""
    return int(round(v * H / 1920))
GOLD = (210, 210, 210)
RAQM = ImageFont.Layout.RAQM

def _logo(size):
    """Optional logo from the profile, or None when missing."""
    if not LOGO_PATH.exists():
        return None
    return Image.open(LOGO_PATH).convert('RGBA').resize((size, size), Image.LANCZOS)

def _find(names):
    for n in names:
        for pat in (n, f'/usr/share/fonts/**/{n}', f'/Library/Fonts/{n}', f'/System/Library/Fonts/**/{n}',
                    f'{__import__("os").path.expanduser("~")}/Library/Fonts/{n}', f'fonts/{n}', str(__import__('pathlib').Path(__file__).resolve().parent / 'fonts' / n)):
            m = glob.glob(pat, recursive=True)
            if m: return m[0]
    return None

def inter(weight):  # 'Bold' | 'ExtraBold' | 'SemiBold'
    p = _find([f'Inter-{weight}.otf', f'Inter-{weight}.ttf', f'Inter_18pt-{weight}.ttf', 'Inter.ttc', 'Helvetica.ttc', 'Arial Bold.ttf'])
    if not p: raise SystemExit('Inter font not found - install it (https://rsms.me/inter) or put Inter-Bold.otf in ./fonts')
    return p

def arabic_font():
    p = _find(['Amiri-Bold.ttf', 'Amiri-Regular.ttf'])
    if not p: raise SystemExit('Amiri font not found - download Amiri-Bold.ttf into ./fonts (fonts.google.com/specimen/Amiri)')
    return p

def font(p, s, raqm=False):
    return ImageFont.truetype(p, s, layout_engine=RAQM) if raqm else ImageFont.truetype(p, s)

def grad(h, amax=240, pw=1.35):
    a = np.zeros((H, W, 4), np.uint8); a[..., 0] = 8; a[..., 1] = 8; a[..., 2] = 10
    ys = np.arange(H - h, H); a[H - h:, :, 3] = (amax * (((ys - (H - h)) / h) ** pw)).astype(np.uint8)[:, None]
    return Image.fromarray(a, 'RGBA')

def shadowed(im, pos, txt, f, fill, off=3, blur=6, anchor='la', alpha=170, **kw):
    sh = Image.new('RGBA', im.size, (0, 0, 0, 0))
    ImageDraw.Draw(sh).text((pos[0] + off, pos[1] + off), txt, font=f, fill=(0, 0, 0, alpha), anchor=anchor, **kw)
    im = Image.alpha_composite(im, sh.filter(ImageFilter.GaussianBlur(blur)))
    ImageDraw.Draw(im).text(pos, txt, font=f, fill=fill, anchor=anchor, **kw)
    return im

def title(path, yo, en):
    im = grad(S(980), 240, 1.35); yb = H - S(430)
    logo = _logo(190)
    logo_w = 190 if logo is not None else 0
    _d = ImageDraw.Draw(Image.new('RGBA', (10, 10)))
    x0 = 90
    tx = x0 + logo_w + 54
    maxw = W - tx - 60
    ys = 104
    while ys > 60 and _d.textlength(yo, font=font(inter('ExtraBold'), ys)) > maxw:
        ys -= 2
    es = 42
    en_lines = _wrap(en.upper(), font(inter('Bold'), es), maxw)
    while len(en_lines) > 2 and es > 28:
        es -= 2
        en_lines = _wrap(en.upper(), font(inter('Bold'), es), maxw)
    en_lines = en_lines[:2]
    if logo is not None:
        im.alpha_composite(logo, (x0, yb - 190))
        ImageDraw.Draw(im).rectangle([x0 + logo_w + 18, yb - 100, x0 + logo_w + 22, yb + 55], fill=GOLD + (255,))
    im = shadowed(im, (tx, yb - 128), yo, font(inter('ExtraBold'), ys), (255, 255, 255, 255), off=2, blur=6, alpha=150)
    line_h = max(36, int(es * 1.06))
    for i, line in enumerate(en_lines):
        im = shadowed(im, (tx, yb + 30 + i * line_h), line, font(inter('Bold'), es),
                      GOLD + (255,), off=1, blur=4, alpha=200)
    im.save(path)

def _wrap(txt, f, maxw):
    words, lines, cur = txt.split(), [], ''
    d = ImageDraw.Draw(Image.new('RGBA', (10, 10)))
    for w_ in words:
        t = (cur + ' ' + w_).strip()
        if d.textlength(t, font=f) <= maxw or not cur: cur = t
        else: lines.append(cur); cur = w_
    if cur: lines.append(cur)
    return lines

def _wrap_rtl(txt, f, maxw):
    """Greedy Arabic wrap: keep a word on the line while it fits; move only the word that overflows."""
    words, lines, cur = txt.split(), [], ''
    d = ImageDraw.Draw(Image.new('RGBA', (10, 10)))
    for word in words:
        t = (cur + ' ' + word).strip()
        if not cur or d.textlength(t, font=f, direction='rtl', language='ar') <= maxw:
            cur = t
        else:
            lines.append(cur); cur = word
    if cur: lines.append(cur)
    return lines

def wrap_arabic(ar_lines, size, maxw):
    """Join the supplied lines and reuse the full card width before breaking."""
    af = font(arabic_font(), size, True)
    return _wrap_rtl(' '.join(ar_lines), af, maxw)


def _panel_bg(h, y0):
    im = Image.new('RGBA', (W, H), (0, 0, 0, 0))
    ImageDraw.Draw(im).rounded_rectangle([60, y0, 1020, y0 + h], radius=34, fill=(10, 12, 14, 168), outline=GOLD + (190,), width=3)
    return im

def panel_card(path, heading, ar_lines, en_lines, y0=900, source=None, bottom=1340, compact=False, rewrap=True):
    """Text panel: Arabic optional, English optional, small source line optional.
    bottom (1920-tall design units) is the lowest edge the panel may reach; lower it (e.g. 1290 on 1:1) so a tall
    card stays clear of the chapter title and its Yoruba tone marks. compact shrinks a 4-line ayah enough to clear
    the title on a 1:1 frame. rewrap joins the supplied Arabic lines and fills the card width before it breaks."""
    ef = font(inter('SemiBold'), 32 if compact else 36); sf = font(inter('Bold'), 26 if compact else 28)
    en = [l for t in en_lines for l in _wrap(t, ef, 860 if not compact else 900)]
    ar_size, ar_lh, en_lh = (64, 86, 42) if compact else (84, 108, 46)
    ar = list(ar_lines or [])
    if ar and rewrap:
        ar = wrap_arabic(ar, ar_size, 900 if compact else 880)
    ph = 44 + 40 + len(ar) * ar_lh + (40 if ar and en else 0) + len(en) * en_lh + (60 if source else 0) + 36
    y0 = max(20, min(S(y0), S(bottom) - ph))
    im = _panel_bg(ph, y0)
    ImageDraw.Draw(im).text((W / 2, y0 + 44), heading, font=font(inter('Bold'), 30), fill=GOLD + (255,), anchor='mm')
    y = y0 + 84
    if ar:
        af = font(arabic_font(), ar_size, True)
        for l in ar:
            im = shadowed(im, (W / 2, y + (ar_lh // 2)), l, af, (255, 255, 255, 255), anchor='mm', direction='rtl', language='ar', blur=5); y += ar_lh
        if en:
            ImageDraw.Draw(im).line([(W / 2 - 70, y + 8), (W / 2 + 70, y + 8)], fill=GOLD + (220,), width=3); y += 40
    for l in en:
        im = shadowed(im, (W / 2, y + (en_lh // 2)), l, ef, (255, 255, 255, 245), anchor='mm', blur=3, off=2); y += en_lh
    if source:
        ImageDraw.Draw(im).text((W / 2, y + 36), source, font=sf, fill=GOLD + (230,), anchor='mm')
    im.save(path)

def list_card(path, heading, items, style='numbered', y0=820, source=None, only=None, bottom=1340):
    """Short list panel (e.g. 3 things to do / not do). style: numbered | dos | donts | bullets.
    items: strings, or {"text": ..., "mark": "do"|"dont"} to mix. Keep to ~6 short items.
    only=None: whole card.  only='base': panel + heading only.  only=k: just item k (for revealing one by one)."""
    f = font(inter('SemiBold'), 44); rows = []
    for it in items:
        txt, mark = (it, None) if isinstance(it, str) else (it['text'], it.get('mark'))
        rows.append((_wrap(txt, f, 740), mark))
    ph = 44 + 50 + sum(len(l) * 56 + 26 for l, _ in rows) + (50 if source else 0) + 20
    y0 = max(20, min(S(y0), S(bottom) - ph))
    show_panel = only in (None, 'base'); show_items = only is None or isinstance(only, int)
    im = _panel_bg(ph, y0) if show_panel else Image.new('RGBA', (W, H), (0, 0, 0, 0))
    if show_panel:
        ImageDraw.Draw(im).text((W / 2, y0 + 48), heading, font=font(inter('Bold'), 32), fill=GOLD + (255,), anchor='mm')
    y = y0 + 100; GREEN, RED = (96, 210, 128), (236, 96, 86)
    for k, (lines, mark) in enumerate(rows):
        if show_items and (only is None or only == k):
            m = mark or {'dos': 'do', 'donts': 'dont'}.get(style)
            cy = y + 28; d = ImageDraw.Draw(im)
            if m == 'do':
                d.ellipse([100, cy - 26, 152, cy + 26], outline=GREEN + (255,), width=4); d.line([(112, cy), (123, cy + 11), (142, cy - 11)], fill=GREEN + (255,), width=6)
            elif m == 'dont':
                d.ellipse([100, cy - 26, 152, cy + 26], outline=RED + (255,), width=4); d.line([(113, cy - 13), (139, cy + 13)], fill=RED + (255,), width=6); d.line([(139, cy - 13), (113, cy + 13)], fill=RED + (255,), width=6)
            elif style == 'bullets':
                d.ellipse([112, cy - 10, 132, cy + 10], fill=GOLD + (255,))
            else:
                d.ellipse([100, cy - 26, 152, cy + 26], fill=GOLD + (255,)); d.text((126, cy + 1), str(k + 1), font=font(inter('ExtraBold'), 34), fill=(20, 20, 20, 255), anchor='mm')
            for j, l in enumerate(lines):
                im = shadowed(im, (178, y + j * 56), l, f, (255, 255, 255, 255), blur=3, off=2)
        y += len(lines) * 56 + 26
    if source and show_panel: ImageDraw.Draw(im).text((W / 2, y + 22), source, font=font(inter('Bold'), 28), fill=GOLD + (230,), anchor='mm')
    im.save(path)

def make_card(path, k, y0=None, only=None):
    """Build a card from a job card dict. kind: quote (default) or list."""
    kind = k.get('kind', 'quote')
    if kind == 'list':
        list_card(path, k.get('heading', 'REMEMBER'), k['items'], k.get('style', 'numbered'), y0 if y0 is not None else 820, k.get('source'), only, k.get('bottom', 1340))
    else:
        panel_card(path, k.get('heading', 'THE PROPHET’S DUʿĀ’'), k.get('arabic', []), k.get('english', []), y0 if y0 is not None else k.get('y0', 900), k.get('source'), k.get('bottom', 1340), k.get('compact', False), k.get('rewrap', True))

def blank(path):
    """A fully transparent frame, used when a chapter should carry no title."""
    Image.new('RGBA', (W, H), (0, 0, 0, 0)).save(path)

def edge_bar(path):
    bw = 90; bar = np.zeros((H, bw, 4), np.uint8)
    for x in range(bw):
        bar[:, x] = (*GOLD, int(255 * np.exp(-((x - bw / 2) / (bw / 6)) ** 2)))
    Image.fromarray(bar, 'RGBA').save(path)

def msg_card(path, lines, idx, n):
    im = grad(S(900), 235, 1.3); f = font(inter('ExtraBold'), 66); lh = 84; y = H - S(250) - len(lines) * lh
    ImageDraw.Draw(im).rectangle([70, y - 12, 80, y + len(lines) * lh - 8], fill=GOLD + (255,))
    for k, l in enumerate(lines):
        im = shadowed(im, (112, y + k * lh), l, f, (255, 255, 255, 255), blur=7, off=4)
    if n > 1:
        im = shadowed(im, (112, y - 62), f"{idx} / {n}", font(inter('Bold'), 32), GOLD + (255,), blur=3, off=2)
    im.save(path)

def counter_on(card_png, path, idx, n):
    im = Image.open(card_png).convert('RGBA')
    shadowed(im, (112, H - S(330)), f"{idx} / {n}", font(inter('Bold'), 38), GOLD + (255,), blur=3, off=2).save(path)

def backdrop(src_png, out):
    b = Image.open(src_png).convert('RGB').resize((W, H)).filter(ImageFilter.GaussianBlur(28))
    a = (np.asarray(b).astype(np.float32) * 0.30)
    yy, xx = np.mgrid[0:H, 0:W]; v = (1 - 0.45 * (((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2)).clip(0.3, 1)
    Image.fromarray((a * v[..., None]).astype(np.uint8)).save(out)

def text_card(path, small, big_lines, sub=None):
    im = Image.new('RGBA', (W, H), (0, 0, 0, 0)); cy = H / 2 - 80
    logo = _logo(120)
    if logo is not None:
        im.alpha_composite(logo, (int(W / 2 - 60), int(cy - 330)))
    im = shadowed(im, (W / 2, cy - 190), small, font(inter('Bold'), 40), GOLD + (255,), anchor='mm', blur=4, off=2)
    ImageDraw.Draw(im).line([(W / 2 - 60, cy - 140), (W / 2 + 60, cy - 140)], fill=GOLD + (255,), width=4)
    for k, l in enumerate(big_lines):
        im = shadowed(im, (W / 2, cy - 40 + k * 120), l, font(inter('ExtraBold'), 108), (255, 255, 255, 255), anchor='mm', blur=8, off=4)
    if sub: im = shadowed(im, (W / 2, cy - 40 + len(big_lines) * 120 + 30), sub, font(inter('SemiBold'), 44), GOLD + (255,), anchor='mm', blur=4, off=2)
    im.save(path)
