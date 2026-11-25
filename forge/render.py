#!/usr/bin/env python3
"""One command: long source video + job.json  ->  short clip per chapter.

    python3 forge/render.py job.json           # everything
    python3 forge/render.py job.json --shorts  # only the per-chapter clips
    python3 forge/render.py job.json --summary # only the summary video
    python3 forge/render.py job.json --chapters c1,c3
    python3 forge/render.py job.json --prep-only  # prepare media, cut nothing

Outputs land in <workdir>/out/ : short_<id>.mp4 ..., summary.mp4, status_teaser.mp4.  See README.md and example/job.json.
Stages are cached in <workdir>; delete a file there to redo that step.
"""
import argparse, json, math, os, shutil, subprocess, sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
import overlays as cards
import check as doctor
import title_anim

ENC = ['-c:v', 'libx264', '-pix_fmt', 'yuv420p', '-video_track_timescale', '15360', '-c:a', 'aac', '-b:a', '192k', '-ar', '48000', '-ac', '2']
TI, TD = 1.5, 0.7           # title appears at 1.5 s; chapter wipe lasts 0.7 s
ASPECTS = {'9:16': (1080, 1920), '4:5': (1080, 1350), '3:4': (1080, 1440), '1:1': (1080, 1080)}   # job.json "aspect"; default 9:16


def sh(cmd, cwd=None, env=None):
    cmd = [str(c) for c in cmd]
    print('$', ' '.join(cmd)[:160], flush=True)
    subprocess.run(cmd, check=True, stdin=subprocess.DEVNULL, cwd=cwd, env=env)

def fc_arg(path):
    """Pass a filtergraph inline. Some ffmpeg builds (e.g. 9.0.2) lack -filter_complex_script,
    so read the .fc file we wrote and hand the graph to -filter_complex directly."""
    return ['-filter_complex', Path(path).read_text()]

def write_thumb(vid, out_jpg, t=4.0, floor=1.0):
    """Poster frame for a rendered clip. The clips fade in from black, so frame 0 looks
    black in phones/Finder/Telegram previews; grab a frame just after the title appears."""
    D = dur(vid); t = max(floor, min(t, max(floor, D - 0.5)))
    sh(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', f'{t:.2f}', '-i', str(vid),
        '-frames:v', '1', '-q:v', '2', str(out_jpg)])

def thumb_first(vid, jpg, hold=0.35, fade=0.45):
    """Make the clip OPEN on its thumbnail (rule 2026-10-16): frame 0 is the poster image, held
    `hold` s, then it dissolves into the video over `fade` s. Feeds and players that show frame 0
    (Facebook, WhatsApp, Telegram, Finder) then show the thumbnail instead of black. Re-encodes the
    picture once at high quality; audio is copied. Idempotent per render (call once, after write_thumb)."""
    vid, jpg = Path(vid), Path(jpg)
    tmp = vid.with_name(vid.stem + '.tf.mp4')
    st = hold + fade
    fc = (f"[1:v]scale=iw:ih,format=rgba,fade=t=out:st={hold}:d={fade}:alpha=1,setsar=1[p];"
          f"[0:v][p]overlay=0:0:eof_action=pass:enable='lt(t,{st:.2f})':format=auto,format=yuv420p[v]")
    sh(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(vid), '-loop', '1', '-framerate', '30',
        '-t', f'{st + 0.1:.2f}', '-i', str(jpg), '-filter_complex', fc, '-map', '[v]', '-map', '0:a',
        '-c:v', 'libx264', '-crf', '19', '-preset', 'fast', '-pix_fmt', 'yuv420p', '-c:a', 'copy',
        '-movflags', '+faststart', str(tmp)])
    os.replace(tmp, vid)

def dur(f):
    return float(subprocess.run(['ffprobe', '-v', 'error', '-show_entries', 'format=duration', '-of', 'csv=p=0', str(f)], capture_output=True, text=True).stdout)

def loud(f):
    r = subprocess.run(['ffmpeg', '-nostdin', '-i', str(f), '-af', 'ebur128=peak=true', '-f', 'null', '-'], capture_output=True, text=True).stderr
    return [l.strip() for l in r.splitlines() if l.strip().startswith(('I:', 'Peak:'))][-2:]


class Job:
    def __init__(self, path):
        self.j = json.load(open(path)); base = Path(path).resolve().parent
        res = lambda p: str((base / p).resolve()) if p and not os.path.isabs(p) else p
        self.src = res(self.j['source']); self.w = Path(res(self.j.get('workdir', 'work'))); self.w.mkdir(parents=True, exist_ok=True)
        (self.w / 'out').mkdir(exist_ok=True)
        self.aspect = self.j.get('aspect', '9:16')
        if self.aspect not in ASPECTS: raise SystemExit(f'job.json: aspect must be one of {list(ASPECTS)}')
        self.OW, self.OH = ASPECTS[self.aspect]; self.ZW, self.ZH = int(self.OW * 1.1) // 2 * 2, int(self.OH * 1.1) // 2 * 2
        cards.set_size(self.OW, self.OH)
        af = self.w / 'aspect.txt'          # stage caches are size-specific: never mix aspects in one workdir
        if af.exists() and af.read_text().strip() != self.aspect: raise SystemExit(f'workdir {self.w} was built for {af.read_text().strip()}; use a separate workdir for {self.aspect}')
        af.write_text(self.aspect)
        self.ch = self.j['chapters']
        nocards = [c['id'] for c in self.ch if not c.get('card')]
        if nocards: print(f"NOTE: chapters with no card: {', '.join(nocards)} (no card data supplied)", flush=True)
        self.music = {k: res(v) for k, v in self.j.get('music', {}).items()}
        self.sfx = {k: res(v) for k, v in self.j.get('sfx', {}).items()}
        self.models = {k: (res(v) if isinstance(v, str) else [res(x) for x in v]) for k, v in self.j.get('models', {}).items()}
        self.audio_source = res(self.j['audio_source']) if self.j.get('audio_source') else None
        self.audio_enhanced = bool(self.j.get('audio_enhanced', False))
        self.title_anim = self.j.get('title_anim') or {}
        self.label = self.j.get('label', 'THIS WEEK')
        self.outro = self.j.get('outro', {'small': '', 'lines': ['Share this', 'video'], 'sub': ''})
        self.grade = self.j.get('grade', 'warm')     # "clean" (chapter 1 look) or "warm" (punchier, default)
        self.speed = float(self.j.get('speed', 1.0))  # playback speed multiplier (1.0 = natural); audio pitch preserved
        self.outro_seconds = float(self.j.get('outro_seconds', 7.0))  # 0 = no outro (TikTok/status short)
        self.deliver = Path(res(self.j['deliver'])) if self.j.get('deliver') else None
        self.overwrite_deliver = bool(self.j.get('overwrite_deliver', False))
        self.id_prefix = self.j.get('id', '')       # e.g. "2026-10-16"; used only for delivery names
    def deliver_output(self, src, c, kind=None):
        """Copy (hardlink when possible) a finished master to <deliver>/<kind>/<name>.
        The workdir stays the scratch area, so existing scripts that read out/ still work."""
        src = Path(src)
        if not self.deliver: return
        d = self.deliver / (kind or c.get('kind', 'clips'))
        d.mkdir(parents=True, exist_ok=True)
        name = c.get('deliver_name') or f"{self.id_prefix or self.w.name}_{c['id']}_{c.get('slug', c['id'])}"
        dest = d / f"{name}{src.suffix}"
        if dest.exists() and not self.overwrite_deliver:
            raise SystemExit(
                f"refusing to overwrite existing deliverable {dest}; "
                "set overwrite_deliver: true only after the user approves a re-render"
            )
        try:
            if dest.exists(): dest.unlink()
            os.link(src, dest)
        except OSError:
            shutil.copy2(src, dest)
        return dest
    def p(self, name): return self.w / name
    def env(self):
        e = dict(os.environ)
        e['OUT_W'], e['OUT_H'] = str(self.OW), str(self.OH)
        e['GRADE_STYLE'] = self.grade
        e['YUNET'] = self.models.get('yunet') or str(HERE / 'models' / 'face_detection_yunet_2023mar.onnx')
        if 'dfn3' in self.models: e['DFN3_DIR'] = self.models['dfn3']
        if 'pydeps' in self.models: e['DFN_PYPATH'] = ':'.join(self.models['pydeps'])
        return e


# ---------------------------------------------------------------- prep
def prep_assets(J, beds=True):
    w = J.p
    for key, gain in (('title', -2.8), ('whoosh', -9.4), ('hit', -4.6), ('riser', -5.2)):
        if key not in J.sfx: raise SystemExit(f'job.json: sfx.{key} missing')
        if not w(f'sfx_{key}.wav').exists():
            sh(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', J.sfx[key], '-af', f'volume={gain}dB', '-ar', '48000', '-ac', '2', w(f'sfx_{key}.wav')])
    if beds:
        if not w('bed_intro.wav').exists():
            sh(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', J.music['intro'], '-af', 'loudnorm=I=-23:TP=-2:LRA=7', '-ar', '48000', '-ac', '2', w('bed_intro.wav')])
        if not w('bed_outro.wav').exists():
            sh(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', J.music['outro'], '-af', 'loudnorm=I=-20:TP=-2:LRA=7', '-ar', '48000', '-ac', '2', w('bed_outro.wav')])
    cards.edge_bar(str(w('edge_bar.png')))

def prep_manifest(J, c):
    """Provenance for cached chapter prep files: boundaries, source and audio fingerprints."""
    try:
        stat = os.stat(J.src)
        size, mtime = stat.st_size, stat.st_mtime
    except OSError:
        size, mtime = None, None
    audio_source = Path(J.audio_source).resolve() if J.audio_source else None
    try:
        astat = os.stat(audio_source) if audio_source else None
        asize, amtime = (astat.st_size, astat.st_mtime) if astat else (None, None)
    except OSError:
        asize, amtime = None, None
    return {
        "version": 1,
        "id": c["id"],
        "start": c["start"],
        "end": c["end"],
        "aspect": J.aspect,
        "source": str(Path(J.src).resolve()),
        "source_size": size,
        "source_mtime": mtime,
        "audio_source": str(audio_source) if audio_source else None,
        "audio_source_size": asize,
        "audio_source_mtime": amtime,
        "audio_enhanced": J.audio_enhanced,
    }


def chapter_cards(c):
    """Return a chapter's card list. Supports one `card` or a `cards` list."""
    if isinstance(c.get('cards'), list):
        return c['cards']
    return [c['card']] if c.get('card') else []


def prep_chapter(J, c):
    i, w = c['id'], J.p
    start, dur_ = c['start'], c['end'] - c['start']
    manifest_path = w(f'{i}_prep.json')
    had_manifest = manifest_path.exists()
    had_cache = any(w(f'{i}{suffix}').exists()
                    for suffix in ('_raw48.wav', '_pauses.json', '_speech.wav', '.v.mp4'))
    current_manifest = prep_manifest(J, c)
    if had_manifest:
        existing = json.loads(manifest_path.read_text())
        if (abs(float(existing.get('start', -1)) - float(c['start'])) > 0.01
                or abs(float(existing.get('end', -1)) - float(c['end'])) > 0.01
                or existing.get('aspect') != J.aspect
                or Path(existing.get('source', '')).resolve() != Path(J.src).resolve()
                or existing.get('audio_source') != current_manifest.get('audio_source')
                or existing.get('audio_enhanced') != current_manifest.get('audio_enhanced')):
            raise SystemExit(
                f'[{i}] prepped cache was built for different boundaries or source; '
                'use a fresh workdir or remove the stale cache after approval'
            )
    elif had_cache:
        print(f'!! [{i}] cache exists without a prep manifest; reusing it as-is. '
              'Use a fresh workdir if the chapter boundaries changed.', flush=True)
    raw48 = w(f'{i}_raw48.wav')
    if not raw48.exists():
        audio_in = J.audio_source or J.src
        sh(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', start, '-t', dur_, '-i', audio_in, '-vn', '-ac', '1', '-ar', '48000', raw48])
    # Pause map for this chapter only: no need to decode the full 40-minute source for a short.
    if not w(f'{i}_pauses.json').exists():
        sh([sys.executable, HERE / 'detect_pauses.py', raw48, w(f'{i}_pauses.json')])
    if not w(f'{i}_speech.wav').exists():
        if J.audio_enhanced:
            sh(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', raw48, '-af',
                'highpass=f=60,loudnorm=I=-16:TP=-1.5:LRA=9,alimiter=limit=0.84:level=disabled',
                '-ar', '48000', w(f'{i}_speech.wav')])
        else:
            try:
                sh([sys.executable, HERE / 'dfn_run.py', raw48, w(f'{i}_dfn.wav')], cwd=J.w, env=J.env())
                cleaned = w(f'{i}_dfn.wav')
            except subprocess.CalledProcessError:
                print('!! DeepFilterNet unavailable - falling back to ffmpeg denoise (noticeably weaker).')
                sh(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', raw48, '-af', 'afftdn=nr=18:nf=-35', w(f'{i}_dfn.wav')]); cleaned = w(f'{i}_dfn.wav')
            sh(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', cleaned, '-af',
                'highpass=f=80,equalizer=f=200:t=q:w=1.2:g=-2,equalizer=f=3000:t=q:w=1.0:g=2.5,equalizer=f=9000:t=h:w=0.7:g=1.2,loudnorm=I=-16:TP=-1.5:LRA=9', '-ar', '48000', w(f'{i}_speech.wav')])
    if not w(f'{i}.v.mp4').exists():
        raw = w(f'{i}_enh.mp4')
        sh([sys.executable, HERE / 'enhance_clip.py', J.src, start, dur_, raw], cwd=J.w, env=J.env())
        vd = c.get('audio_delay_ms', 0) / 1000.0     # >0: voice is late vs lips -> show older picture; <0: voice early
        if abs(vd) < 1e-3:
            os.replace(str(raw) + '.v.mp4', w(f'{i}.v.mp4'))   # already 30 fps CFR: no second encode
        else:
            D = dur(str(raw) + '.v.mp4')
            if vd > 0: vf = f'tpad=start_duration={vd}:start_mode=clone,trim=duration={D:.3f},setpts=PTS-STARTPTS'
            else: vf = f'trim=start={-vd},setpts=PTS-STARTPTS,tpad=stop_duration={-vd}:stop_mode=clone'
            sh(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-i', str(raw) + '.v.mp4', '-vf', vf, '-r', '30', '-c:v', 'libx264', '-crf', '15', '-preset', 'veryfast', '-pix_fmt', 'yuv420p', '-an', w(f'{i}.v.mp4')])
    if c.get('title', True): cards.title(str(w(f'title_{i}.png')), c['yo'], c['en'])
    else: cards.blank(str(w(f'title_{i}.png')))   # no title: transparent overlay, no title sound
    cl = chapter_cards(c)
    for j, k in enumerate(cl):
        suffix = f'{i}_{j}' if len(cl) > 1 else i
        cards.make_card(str(w(f'card_{suffix}.png')), k)
        cards.make_card(str(w(f'card_{suffix}_teaser.png')), k, 700)
        if k.get('kind') == 'list' and k.get('reveal', True):      # one layer per item so they appear one after the other
            cards.make_card(str(w(f'card_{suffix}_base.png')), k, only='base')
            for q in range(len(k['items'])):
                cards.make_card(str(w(f'card_{suffix}_item{q}.png')), k, only=q)
    if had_manifest or not had_cache:
        manifest_path.write_text(json.dumps(current_manifest, ensure_ascii=False, indent=2) + '\n')

def make_outro(J):
    if J.p('outro.mp4').exists(): return
    w = J.p
    # Use a chapter that was actually prepped: a --chapters subset may not include the last one.
    c = next((x for x in reversed(J.ch) if J.p(f"{x['id']}.v.mp4").exists()), J.ch[-1])
    sh(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', 20, '-i', w(f"{c['id']}.v.mp4"), '-frames:v', 1, w('bg_outro_src.png')])
    cards.backdrop(str(w('bg_outro_src.png')), str(w('bg_outro.png')))
    o = J.outro; cards.text_card(str(w('outro_text.png')), o['small'], o['lines'], o.get('sub'))
    O = J.outro_seconds
    inp = ['-loop', '1', '-framerate', '30', '-t', O + 1, '-i', w('bg_outro.png'), '-loop', '1', '-framerate', '30', '-t', O + 1, '-i', w('outro_text.png'), '-i', w('bed_outro.wav'), '-i', w('sfx_whoosh.wav')]
    fc = (f"[0:v]scale={J.ZW}:{J.ZH},zoompan=z='1.0+0.0006*on':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d=1:s={J.OW}x{J.OH}:fps=30,trim=duration={O}[bg];"
          f"[1:v]format=rgba,fade=t=in:st=0.6:d=0.7:alpha=1[txt];[bg][txt]overlay=0:0:format=auto,fade=t=in:st=0:d=0.5,fade=t=out:st={O-0.9}:d=0.9,format=yuv420p,setsar=1,fps=30[vo];"
          f"[2:a]atrim=start=0:duration={O+1},asetpts=PTS-STARTPTS,volume=1.7,afade=t=in:d=0.8,afade=t=out:st={O-1.8}:d=1.8[bed];[3:a]volume=0.12,adelay=450|450[wh];"
          f"[bed][wh]amix=inputs=2:duration=longest:normalize=0,atrim=duration={O},alimiter=limit=0.84:level=disabled[ao]")
    sh(['ffmpeg', '-nostdin', '-v', 'error', '-y'] + inp + ['-filter_complex', fc, '-map', '[vo]', '-map', '[ao]', '-t', O, '-r', 30, '-crf', 20, '-preset', 'veryfast'] + ENC + [w('outro.mp4')])



def card_layers(J, c):
    """[(png, appear_at, disappear_at, is_item)] in chapter seconds. Supports `card` or `cards`."""
    i, w = c['id'], J.p
    cl = chapter_cards(c)
    multi = len(cl) > 1
    min_card = float(J.j.get('card_min_seconds', 4.5))
    layers = []
    for j, k in enumerate(cl):
        suffix = f'{i}_{j}' if multi else i
        st = k['start']
        en = max(k['end'], st + min_card)
        if k.get('kind') == 'list' and k.get('reveal', True):
            n = len(k['items'])
            times = k.get('item_times') or [st + 0.4 + q * ((en - st - 1.6) / n) for q in range(n)]
            layers.append((w(f'card_{suffix}_base.png'), st, en, False))
            layers += [(w(f'card_{suffix}_item{q}.png'), times[q], en, True) for q in range(n)]
        else:
            layers.append((w(f'card_{suffix}.png'), st, en, False))
    return layers

def card_filters(layers, first_idx, last, mapt, tag):
    """overlay chain for card layers fed as inputs first_idx.. ; returns (filter_text, last_label)"""
    fc = ''
    for j, (png, a, b, item) in enumerate(layers):
        a, b = mapt(a), mapt(b); idx = first_idx + j
        ye = f"if(lt(t,{a:.2f}),26,if(lt(t,{a+0.4:.2f}),26*pow(1-(t-{a:.2f})/0.4\\,2),0))" if item else '0'
        fc += (f"[{idx}:v]format=rgba,fade=t=in:st={a:.2f}:d={0.4 if item else 0.45}:alpha=1,fade=t=out:st={b-0.45:.2f}:d=0.45:alpha=1[l{tag}{j}];"
               f"[{last}][l{tag}{j}]overlay=x=0:y='{ye}':format=auto[o{tag}{j}];")
        last = f'o{tag}{j}'
    return fc, last

# ---------------------------------------------------------------- short clip (silence cut + zoom)
def keep_segments(J, c, D, PAD=0.055, MINSEG=0.6):
    ph = J.p(f"{c['id']}_pauses.json")
    if ph.exists():
        # Chapter-local pauses (seconds from chapter start): no full-source decode needed.
        pauses = json.loads(ph.read_text())
        P = [(s, e) for s, e in pauses if e > 0 and s < D]
    else:
        pauses = json.loads(J.p('pauses.json').read_text())
        P = [(s - c['start'], e - c['start']) for s, e in pauses if e - c['start'] > 0 and s - c['start'] < D]
    keep, cur = [], 0.0
    for s, e in P:
        if s <= 0.02: cur = max(cur, e - PAD); continue
        ke = min(s + PAD, D)
        if ke - cur >= MINSEG and e < D - 0.05:
            keep.append([cur, ke]); cur = e - PAD
    if D - cur > 0.3: keep.append([cur, D])
    for a0, b0 in c.get('skip', []):          # absolute source seconds to drop (e.g. a stretch where the source picture collapsed)
        a0 -= c['start']; b0 -= c['start']; out = []
        for a, b in keep:
            if b <= a0 or a >= b0: out.append([a, b])
            else:
                if a0 - a >= 0.3: out.append([a, a0])
                if b - b0 >= 0.3: out.append([b0, b])
        keep = out
    return keep

def prep_title_anim(J, c):
    """Render the transparent roll-in, roll-out and settled lockup for one chapter."""
    i, w = c['id'], J.p
    if not J.title_anim.get('enabled') or not c.get('title', True):
        return None
    d_in = w(f'title_anim_{i}_in')
    d_out = w(f'title_anim_{i}_out')
    settled = w(f'title_anim_{i}_settled.png')
    in_dur, out_dur = 1.2, 2.0
    if not settled.exists() or not (d_in / 'frame_0000.png').exists():
        in_dur = title_anim.render_alpha(c['yo'], c['en'], d_in, duration=in_dur, roll=0.9, settle=0.3,
                                         settled_path=settled, title_small=c.get('title_small'),
                                         style=c.get('title_style', 'default'),
                                         width=J.OW, height=J.OH)
    if not (d_out / 'frame_0000.png').exists():
        out_dur = title_anim.render_alpha(c['yo'], c['en'], d_out, duration=out_dur, roll=1.5, settle=0.5,
                                          reverse=True, title_small=c.get('title_small'),
                                          style=c.get('title_style', 'default'),
                                          width=J.OW, height=J.OH)
    return {'in_dir': d_in, 'out_dir': d_out, 'settled': settled, 'in_dur': in_dur, 'out_dur': out_dur}


def build_short(J, c):
    i, w = c['id'], J.p
    vid, aud = w(f'{i}.v.mp4'), w(f'{i}_speech.wav')
    D = min(dur(vid), dur(aud)); keep = keep_segments(J, c, D); N = len(keep); tot = sum(b - a for a, b in keep)
    if not keep:
        raise SystemExit(f'[{i}] no usable speech segments remain after pause/skip trimming')
    def mp(t):
        n = 0.0
        for a, b in keep:
            if t < a: return n
            if t <= b: return n + t - a
            n += b - a
        return n
    print(f'[{i}] {D:.1f}s -> {tot:.1f}s tight, {N} cuts')
    w(f'{i}_timemap.json').write_text(json.dumps({
        'chapter': i, 'start': c['start'], 'end': c['end'], 'source_duration': D,
        'speed': J.speed, 'clip_duration': round(tot, 3),
        'keep': [[round(a, 3), round(b, 3)] for a, b in keep],
    }, ensure_ascii=False, indent=2) + '\n')
    fc = f"[0:v]fps=30,setsar=1,split={N}" + ''.join(f"[v{k}]" for k in range(N)) + ";" + f"[1:a]asplit={N}" + ''.join(f"[a{k}]" for k in range(N)) + ";"
    for k, (a, b) in enumerate(keep):
        d = b - a; z = "1.0"          # constant zoom: the per-cut push-in did not work in most cases (user rule 31 Oct)
        fc += (f"[v{k}]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS,scale=w='trunc({J.OW}*{z}/2)*2':h='trunc({J.OH}*{z}/2)*2':eval=frame:flags=bicubic,"
               f"crop={J.OW}:{J.OH}:'(iw-{J.OW})/2':'(ih-{J.OH})*0.3',setsar=1[zv{k}];"
               f"[a{k}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS,afade=t=in:d=0.012,afade=t=out:st={d-0.012:.3f}:d=0.012[za{k}];")
    fc += ''.join(f"[zv{k}][za{k}]" for k in range(N)) + f"concat=n={N}:v=1:a=1[cv][ca];"

    inp = ['-i', str(vid), '-i', str(aud)]
    n_inputs = 2
    def add_input(opts):
        nonlocal n_inputs
        idx = n_inputs
        inp.extend(opts)
        n_inputs += 1
        return idx

    title_on = c.get('title', True)
    anim = prep_title_anim(J, c) if tot > 5.0 else None
    last = 'cv'
    if anim and title_on:
        start_in = 0.85
        in_dur = anim['in_dur']
        hold_start = start_in + in_dur - 1 / 30
        out_start = max(hold_start + 0.1, tot - anim['out_dur'])
        idx_in = add_input(['-itsoffset', f'{start_in:.3f}', '-framerate', '30', '-i', str(anim['in_dir'] / 'frame_%04d.png')])
        idx_hold = add_input(['-loop', '1', '-framerate', '30', '-t', f'{tot + 1:.3f}', '-i', str(anim['settled'])])
        idx_out = add_input(['-itsoffset', f'{out_start:.3f}', '-framerate', '30', '-i', str(anim['out_dir'] / 'frame_%04d.png')])
        fc += (f"[{idx_in}:v]format=rgba[ti];[{last}][ti]overlay=0:0:eof_action=pass:enable='between(t,{start_in:.3f},{start_in + in_dur:.3f})':format=auto[oi];")
        last = 'oi'
        fc += (f"[{idx_hold}:v]format=rgba[th];[{last}][th]overlay=0:0:eof_action=pass:enable='between(t,{hold_start:.3f},{out_start:.3f})':format=auto[oh];")
        last = 'oh'
        fc += (f"[{idx_out}:v]format=rgba[to];[{last}][to]overlay=0:0:eof_action=pass:enable='between(t,{out_start:.3f},{tot:.3f})':format=auto[oo];")
        last = 'oo'
    elif title_on:
        idx_title = add_input(['-loop', '1', '-framerate', '30', '-t', f'{tot + 1:.3f}', '-i', str(w(f'title_{i}.png'))])
        fc += (f"[{idx_title}:v]format=rgba,fade=t=in:st={TI}:d=0.5:alpha=1[t];"
               f"[{last}][t]overlay=x=0:y='if(lt(t,{TI+0.6}),38*pow(1-(t-{TI})/0.6\\,2),0)':format=auto[vt];")
        last = 'vt'

    if chapter_cards(c):
        L = card_layers(J, c)
        card_start = n_inputs
        for png, *_ in L:
            add_input(['-loop', '1', '-framerate', '30', '-t', f'{tot + 1:.3f}', '-i', str(png)])
        t, last = card_filters(L, card_start, last, mp, 's'); fc += t

    fc += f"[{last}]fade=t=in:st=0:d=0.3,fade=t=out:st={tot-0.4:.2f}:d=0.4,format=yuv420p[vo0];"
    fc += "[ca]pan=stereo|c0=c0|c1=c0,volume='0.12+0.88*pow(min(t/2.2\\,1)\\,1.6)':eval=frame,volume=-1.5dB[sp];"
    sfx_boost = float(getattr(J, 'j', {}).get('sfx_boost', 1.0))
    if anim and title_on:
        idx_wh = add_input(['-i', str(w('sfx_whoosh.wav'))])
        idx_st = add_input(['-i', str(w('sfx_title.wav'))])
        idx_we = add_input(['-i', str(w('sfx_whoosh.wav'))])
        exit_ms = int(out_start * 1000)
        fc += (f"[{idx_wh}:a]volume={0.22 * sfx_boost:.3f},adelay=850|850[w1];"
               f"[{idx_st}:a]volume={0.35 * sfx_boost:.3f},adelay=1700|1700[s1];"
               f"[{idx_we}:a]volume={0.18 * sfx_boost:.3f},adelay={exit_ms}|{exit_ms}[w2];"
               f"[sp][w1][s1][w2]amix=inputs=4:duration=first:normalize=0,afade=t=out:st={tot-0.4:.2f}:d=0.4,alimiter=limit=0.84:level=disabled[ao0];")
    else:
        title_vol = (0.62 * sfx_boost) if title_on else 0.0
        idx_title_sfx = add_input(['-i', str(w('sfx_title.wav'))])
        fc += (f"[{idx_title_sfx}:a]volume={title_vol}[sx];"
               f"[sp][sx]amix=inputs=2:duration=first:normalize=0,afade=t=out:st={tot-0.4:.2f}:d=0.4,alimiter=limit=0.84:level=disabled[ao0];")
    sp = J.speed
    if abs(sp - 1.0) > 1e-3:                    # speed in the same encode (atempo keeps pitch): one less full pass
        fc += f"[vo0]setpts=PTS/{sp:.6f},fps=30[vos];[ao0]atempo={sp:.6f}[aos];"
    else:
        fc += "[vo0]null[vos];[ao0]anull[aos];"
    outdur = tot / sp
    w(f'{i}_clip.fc').write_text(fc)
    sh(['ffmpeg', '-nostdin', '-v', 'error', '-y'] + inp + [*fc_arg(w(f'{i}_clip.fc')), '-map', '[vos]', '-map', '[aos]', '-t', f'{outdur:.3f}', '-r', 30, '-crf', 22, '-preset', 'fast', '-maxrate', '9M', '-bufsize', '18M'] + ENC + [w(f'{i}_tight.mp4')])
    final = J.p('out') / f'short_{i}.mp4'
    if getattr(J, 'outro_seconds', 7.0) > 0:
        w(f'{i}_join.txt').write_text(f"file '{i}_tight.mp4'\nfile 'outro.mp4'\n")
        sh(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'concat', '-safe', 0, '-i', w(f'{i}_join.txt'), '-c', 'copy', '-movflags', '+faststart', final])
    else:
        shutil.copy2(w(f'{i}_tight.mp4'), final)   # no outro: the tight cut is the master (TikTok/status)
    poster = J.p('out') / f'thumb_{i}.jpg'
    open_poster = J.p('out') / f'open_{i}.jpg'
    write_thumb(final, poster)
    write_thumb(final, open_poster, t=0.4, floor=0.2)
    thumb_first(final, open_poster)
    dest = J.deliver_output(final, c) if hasattr(J, 'deliver_output') else None
    if dest:
        shutil.copy2(poster, dest.with_suffix('.jpg'))
        print(f'[{i}] delivered -> {dest}')
    print(f'[{i}] short_{i}.mp4', loud(J.p('out') / f'short_{i}.mp4'))


# ---------------------------------------------------------------- summary (body + intro + outro)
def build_body(J):
    w, CH = J.p, J.ch; n = len(CH)
    D = [min(dur(w(f"{c['id']}.v.mp4"), ), dur(w(f"{c['id']}_speech.wav"))) for c in CH]
    inp, fc = [], ''
    for k, c in enumerate(CH):
        inp += ['-i', w(f"{c['id']}.v.mp4"), '-i', w(f"{c['id']}_speech.wav"), '-loop', '1', '-framerate', '30', '-t', f'{D[k]:.2f}', '-i', w(f"title_{c['id']}.png")]
    base = n * 3
    inp += ['-i', w('sfx_title.wav'), '-i', w('sfx_whoosh.wav')]; T_IDX, W_IDX = base, base + 1; nxt = base + 2
    card_idx = {}
    for k, c in enumerate(CH):
        if chapter_cards(c):
            L = card_layers(J, c); card_idx[k] = (nxt, L)
            for png, *_ in L: inp += ['-loop', '1', '-framerate', '30', '-t', f'{D[k]+1:.2f}', '-i', png]; nxt += 1
    BAR0 = nxt
    for _ in range(max(n - 1, 1)): inp += ['-loop', '1', '-framerate', '30', '-t', 900, '-i', w('edge_bar.png')]
    for k, c in enumerate(CH):
        b = k * 3
        fc += (f"[{b+2}:v]format=rgba,fade=t=in:st={TI}:d=0.5:alpha=1[t{k}];[{b}:v]fps=30,setsar=1[bv{k}];"
               f"[bv{k}][t{k}]overlay=x=0:y='if(lt(t,{TI+0.6}),38*pow(1-(t-{TI})/0.6\\,2),0)':format=auto[vt{k}];")
        last = f'vt{k}'
        if k in card_idx:
            first, L = card_idx[k]; t, last = card_filters(L, first, last, lambda x: x, f'b{k}'); fc += t
        fc += f"[{last}]format=yuv420p[v{k}];[{b+1}:a]pan=stereo|c0=c0|c1=c0,volume='0.12+0.88*pow(min(t/2.2\\,1)\\,1.6)':eval=frame[sp{k}];[{T_IDX}:a]volume=0.62[sx{k}];"
        if k > 0: fc += f"[{W_IDX}:a]volume=0.5[wx{k}];[sp{k}][sx{k}][wx{k}]amix=inputs=3:duration=first:normalize=0[a{k}];"
        else: fc += f"[sp{k}][sx{k}]amix=inputs=2:duration=first:normalize=0[a{k}];"
    lv, la, off, offs = 'v0', 'a0', 0.0, []
    for k in range(1, n):
        off += D[k - 1] - TD; offs.append(off)
        fc += (f"[{lv}][v{k}]xfade=transition=wipeleft:duration={TD}:offset={off:.3f}[xv{k}];[{BAR0+k-1}:v]format=rgba[bar{k}];"
               f"[xv{k}][bar{k}]overlay=x='W*(1-(t-{off:.3f})/{TD})-45':y=0:enable='between(t,{off:.3f},{off+TD:.3f})':format=auto[ov{k}];[{la}][a{k}]acrossfade=d={TD}:c1=tri:c2=tri[xa{k}];")
        lv, la = f'ov{k}', f'xa{k}'
    tot = sum(D) - TD * (n - 1)
    fc += (f"[{lv}]fade=t=in:st=0:d=0.35,fade=t=out:st={tot-0.6:.2f}:d=0.6,format=yuv420p[vo];"
           f"[{la}]afade=t=in:st=0:d=0.2,afade=t=out:st={tot-0.6:.2f}:d=0.6,volume=-2dB,alimiter=limit=0.84:level=disabled[ao]")
    w('body_fc.txt').write_text(fc)
    sh(['ffmpeg', '-nostdin', '-v', 'error', '-y'] + inp + [*fc_arg(w('body_fc.txt')), '-map', '[vo]', '-map', '[ao]', '-t', f'{tot:.3f}', '-r', 30, '-crf', 22, '-preset', 'medium', '-maxrate', '9M', '-bufsize', '18M'] + ENC + [w('body.mp4')])
    json.dump({'D': D, 'tot': tot, 'offs': offs}, open(w('body_meta.json'), 'w'))
    return tot

def prep_teaser(J, c):
    """Compact one teaser: drop sub-second silences inside its window and apply J.speed.
    Writes teaser_<id>.v.mp4 (video only) and teaser_<id>.a.wav (48k mono), returns the duration."""
    t, i = c['teaser'], c['id']; at = float(t['at']); d = float(t['dur'])
    vo, ao = J.p(f'teaser_{i}.v.mp4'), J.p(f'teaser_{i}.a.wav')
    if vo.exists() and ao.exists(): return min(dur(vo), dur(ao))
    P = json.loads(J.p(f'{i}_pauses.json').read_text())
    seg = [(s - at, e - at) for s, e in P if e > at and s < at + d]
    keep, cur = [], 0.0; PAD = 0.055; MIN = 0.5
    for s, e in seg:
        s = max(0.0, s); e = min(d, e)
        if s <= 0.02: cur = max(cur, e - PAD); continue
        ke = min(s + PAD, d)
        if ke - cur >= MIN and e < d - 0.05:
            keep.append([cur, ke]); cur = e - PAD
    if d - cur > 0.25: keep.append([cur, d])
    if not keep: keep = [[0.0, d]]
    N = len(keep); sp = J.speed
    inp = ['-ss', f'{at:.3f}', '-t', f'{d:.3f}', '-i', str(J.p(f'{i}.v.mp4')),
           '-ss', f'{at:.3f}', '-t', f'{d:.3f}', '-i', str(J.p(f'{i}_speech.wav'))]
    fc = (f"[0:v]fps=30,setsar=1,split={N}" + ''.join(f"[v{k}]" for k in range(N)) + ";"
          + f"[1:a]asplit={N}" + ''.join(f"[a{k}]" for k in range(N)) + ";")
    for k, (a, b) in enumerate(keep):
        fc += (f"[v{k}]trim=start={a:.3f}:end={b:.3f},setpts=PTS-STARTPTS[zv{k}];"
               f"[a{k}]atrim=start={a:.3f}:end={b:.3f},asetpts=PTS-STARTPTS[za{k}];")
    fc += ''.join(f"[zv{k}][za{k}]" for k in range(N)) + f"concat=n={N}:v=1:a=1[cv][ca];"
    if abs(sp - 1.0) > 1e-3:
        fc += f"[cv]setpts=PTS/{sp:.6f},fps=30[v];[ca]atempo={sp:.6f}[a]"
    else:
        fc += "[cv]null[v];[ca]anull[a]"
    sh(['ffmpeg', '-nostdin', '-v', 'error', '-y'] + inp + ['-filter_complex', fc,
        '-map', '[v]', '-an', '-r', '30', '-crf', '20', '-preset', 'fast', str(vo),
        '-map', '[a]', '-vn', '-ar', '48000', '-ac', '1', '-c:a', 'pcm_s16le', str(ao)])
    return min(dur(vo), dur(ao))

def build_intro(J, body_total, status=False):
    w = J.p; TDI = 0.35
    card = J.j.get('intro_card', True)          # False: start straight on reminder 1, no headline scene
    A = 3.6 if card else 0.0
    teasers = [c for c in J.ch if c.get('teaser')]
    if not teasers: return None
    n = len(teasers); T = [prep_teaser(J, c) for c in teasers]
    for k, c in enumerate(teasers):
        t = c['teaser']; i = c['id']
        if t.get('use_card') and (t.get('card') or c.get('card')):
            kc = t.get('card') or c['card']
            png = w(f'teaser_card_{i}.png')
            cards.make_card(str(png), kc, 700)
            T[k] = max(T[k], 5.8); cards.counter_on(str(png), str(w(f'msg_{i}.png')), k + 1, n)
        else: cards.msg_card(str(w(f'msg_{i}.png')), t['lines'], k + 1, n)
    offs, o = [], (A - TDI if card else 0.0)
    for k in range(n): offs.append(o); o += T[k] - TDI
    tot = offs[-1] + T[-1]
    if J.j.get('intro_headline'):
        head = J.j['intro_headline']
    elif status:
        head = [f"{n} reminder{'s' if n != 1 else ''}", f"in {round(tot)} seconds"]
    else:
        mins = max(1, round(body_total / 60)); nr = len(J.ch)
        head = [f"{nr} reminder{'s' if nr != 1 else ''}", f"in {mins} minute{'s' if mins != 1 else ''}"]
    inp = []
    if card:
        cards.text_card(str(w('intro_text.png')), J.label, head)
        c0 = J.ch[0]
        sh(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-ss', 30, '-i', w(f"{c0['id']}.v.mp4"), '-frames:v', 1, w('bg_intro_src.png')]); cards.backdrop(str(w('bg_intro_src.png')), str(w('bg_intro.png')))
        inp = ['-loop', 1, '-framerate', 30, '-t', A + 1, '-i', w('bg_intro.png'), '-loop', 1, '-framerate', 30, '-t', A + 1, '-i', w('intro_text.png')]
    b0 = 2 if card else 0
    for k, c in enumerate(teasers):
        d = T[k]
        inp += ['-i', w(f"teaser_{c['id']}.v.mp4"), '-i', w(f"teaser_{c['id']}.a.wav"), '-loop', 1, '-framerate', 30, '-t', d, '-i', w(f"msg_{c['id']}.png")]
    BED = b0 + 3 * n; inp += ['-i', w('bed_intro.wav'), '-i', w('sfx_hit.wav'), '-i', w('sfx_whoosh.wav'), '-i', w('sfx_riser.wav')]; HIT, WH, RIS = BED + 1, BED + 2, BED + 3
    fc = ''
    if card:
        fc += (f"[0:v]scale={J.ZW}:{J.ZH},zoompan=z='1.0+0.0006*on':x='(iw-iw/zoom)/2':y='(ih-ih/zoom)/2':d=1:s={J.OW}x{J.OH}:fps=30,trim=duration={A}[bg];"
               f"[1:v]format=rgba,fade=t=in:st=0.5:d=0.6:alpha=1,fade=t=out:st={A-0.5}:d=0.4:alpha=1[txt];[bg][txt]overlay=0:0:format=auto,format=yuv420p,setsar=1,fps=30[v0];")
    for k in range(n):
        b = b0 + k * 3
        fc += f"[{b+2}:v]format=rgba,fade=t=in:st=0.35:d=0.35:alpha=1[m{k}];[{b}:v]fps=30,setsar=1[c{k}];[c{k}][m{k}]overlay=0:0:format=auto,format=yuv420p[tk{k}];"
    trs = ['slideleft', 'slideup', 'smoothleft', 'slideleft', 'slideup', 'smoothleft']
    seq = (['v0'] if card else []) + [f'tk{k}' for k in range(n)]
    lv = seq[0]
    for idx in range(1, len(seq)):
        off = offs[idx - 1] if card else offs[idx]
        fc += f"[{lv}][{seq[idx]}]xfade=transition={trs[(idx-1) % len(trs)]}:duration={TDI}:offset={off:.3f}[x{idx}];"; lv = f'x{idx}'
    fc += f"[{lv}]fade=t=out:st={tot-0.5:.2f}:d=0.5,format=yuv420p[vo];"
    for k, c in enumerate(teasers):
        b = b0 + k * 3; d = T[k]; ms = int(offs[k] * 1000)
        fc += f"[{b+1}:a]pan=stereo|c0=c0|c1=c0,afade=t=in:d=0.08,afade=t=out:st={d-0.15:.2f}:d=0.15,adelay={ms}|{ms}[s{k}];"
    fc += f"[{BED}:a]atrim=start=0:duration={tot+1},asetpts=PTS-STARTPTS,volume=0.9,afade=t=in:d=1.2,afade=t=out:st={tot-1.4:.2f}:d=1.4[bed];[{RIS}:a]volume=0.55[r0];"
    parts = ['r0']
    for k, o in enumerate(offs):
        d0 = max(0, int(o * 1000)); d1 = max(0, int((o - 0.05) * 1000))
        fc += f"[{HIT}:a]volume=0.45,adelay={d0}|{d0}[h{k}];[{WH}:a]volume=0.4,adelay={d1}|{d1}[w{k}];"; parts += [f'h{k}', f'w{k}']
    mix = ['bed'] + [f's{k}' for k in range(n)] + parts
    fc += ''.join(f'[{m}]' for m in mix) + f"amix=inputs={len(mix)}:duration=longest:normalize=0,atrim=duration={tot},alimiter=limit=0.84:level=disabled[ao]"
    w('intro_fc.txt').write_text(fc)
    sh(['ffmpeg', '-nostdin', '-v', 'error', '-y'] + inp + [*fc_arg(w('intro_fc.txt')), '-map', '[vo]', '-map', '[ao]', '-t', f'{tot:.3f}', '-r', 30, '-crf', 20, '-preset', 'veryfast'] + ENC + [w('intro.mp4')])
    return tot

def build_summary(J):
    w = J.p; has_outro = getattr(J, 'outro_seconds', 7.0) > 0
    if has_outro: make_outro(J)
    tot = build_body(J)
    outro = "file 'outro.mp4'\n" if has_outro else ""
    if build_intro(J, tot):
        w('summary_join.txt').write_text("file 'intro.mp4'\nfile 'body.mp4'\n" + outro)
        w('status_join.txt').write_text("file 'intro.mp4'\n" + outro)
        sh(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'concat', '-safe', 0, '-i', w('status_join.txt'), '-c', 'copy', '-movflags', '+faststart', J.p('out') / 'status_teaser.mp4'])
    else: w('summary_join.txt').write_text("file 'body.mp4'\n" + outro)
    sh(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'concat', '-safe', 0, '-i', w('summary_join.txt'), '-c', 'copy', '-movflags', '+faststart', J.p('out') / 'summary.mp4'])
    write_thumb(J.p('out') / 'summary.mp4', J.p('out') / 'thumb_summary.jpg')
    thumb_first(J.p('out') / 'summary.mp4', J.p('out') / 'thumb_summary.jpg')
    if (J.p('out') / 'status_teaser.mp4').exists():
        write_thumb(J.p('out') / 'status_teaser.mp4', J.p('out') / 'thumb_status_teaser.jpg')
        thumb_first(J.p('out') / 'status_teaser.mp4', J.p('out') / 'thumb_status_teaser.jpg')
    print('summary.mp4', round(dur(J.p('out') / 'summary.mp4'), 1), 's', loud(J.p('out') / 'summary.mp4'))


def main():
    ap = argparse.ArgumentParser(); ap.add_argument('job'); ap.add_argument('--shorts', action='store_true'); ap.add_argument('--summary', action='store_true')
    ap.add_argument('--prep-only', action='store_true'); ap.add_argument('--chapters', default='')
    ap.add_argument('--status', action='store_true')   # just the intro+outro status piece
    a = ap.parse_args()
    errors, warnings = doctor.validate_job(a.job)
    for warning in warnings: print(f'NOTE: {warning}', flush=True)
    if errors:
        for error in errors: print(f'ERROR: {error}', file=sys.stderr, flush=True)
        print(f'Doctor found {len(errors)} error(s); rendering stopped before creating the workdir.', file=sys.stderr, flush=True)
        return 1
    print('Doctor passed.', flush=True)
    J = Job(a.job)
    want = [x for x in a.chapters.split(',') if x]
    do_s, do_m = (a.shorts or not a.summary), (a.summary or not a.shorts)
    # Prep only what this run will render. Previously --chapters c7 still denoised/enhanced c1-c11.
    if a.status:
        prep_ids = {c['id'] for c in J.ch if c.get('teaser')} | {J.ch[-1]['id']}
    elif a.prep_only or do_m or not want:
        prep_ids = {c['id'] for c in J.ch}
    else:
        prep_ids = set(want)
    need_beds = do_m or a.status or (do_s and J.outro_seconds > 0)
    prep_assets(J, beds=need_beds)
    for c in J.ch:
        if c['id'] in prep_ids: prep_chapter(J, c)
    if a.prep_only: return
    if a.status:
        if J.outro_seconds > 0: make_outro(J)
        if build_intro(J, 0.0, status=True):
            w = J.p
            if J.outro_seconds > 0:
                w('status_join.txt').write_text("file 'intro.mp4'\nfile 'outro.mp4'\n")
                sh(['ffmpeg', '-nostdin', '-v', 'error', '-y', '-f', 'concat', '-safe', 0, '-i', w('status_join.txt'), '-c', 'copy', J.p('out') / 'status_teaser.mp4'])
            else:
                shutil.copy2(w('intro.mp4'), J.p('out') / 'status_teaser.mp4')
            write_thumb(J.p('out') / 'status_teaser.mp4', J.p('out') / 'thumb_status_teaser.jpg')
            thumb_first(J.p('out') / 'status_teaser.mp4', J.p('out') / 'thumb_status_teaser.jpg')
        else:
            print('!! no teasers in job -> nothing to build for status')
        if not a.summary:            # --summary --status builds both; --status alone stops here
            print('done ->', J.p('out')); return
    if do_s:
        if J.outro_seconds > 0: make_outro(J)
        for c in J.ch:
            if not want or c['id'] in want: build_short(J, c)
    if do_m: build_summary(J)
    print('done ->', J.p('out'))
    return 0

if __name__ == '__main__':
    sys.exit(main())
