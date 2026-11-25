"""Enhance one clip of a (variable-frame-rate) Facebook livestream.

Fixes the lip-sync drift of the first prototype: the source drops frames, so we let
ffmpeg decode by *timestamp* into a constant 30 fps stream (duplicating frames where the
stream dropped them). Frame i is then exactly t = i/30, the same clock the audio uses.

usage: enhance_clip.py SRC START DUR OUT.mp4 [DFN_WAV]
  DFN_WAV: optional already-enhanced 48k mono wav for the same START/DUR (else raw audio is used)
"""
import cv2, numpy as np, subprocess, sys, json

SRC, T0, DUR, OUT = sys.argv[1], float(sys.argv[2]), float(sys.argv[3]), sys.argv[4]
AUDIO = sys.argv[5] if len(sys.argv) > 5 else None
FPS = 30
import os
WARM = float(os.environ.get('WARM', '0'))   # seconds of lead-in used only to warm up the face-tracking smoothing (not written)
OW, OH = int(os.environ.get('OUT_W', 1080)), int(os.environ.get('OUT_H', 1920))   # output size (set by forge/render.py from profile output.aspect)

def probe_size(src):
    o = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height',
                        '-of', 'csv=p=0', src], capture_output=True, text=True).stdout.strip().split(',')
    return int(o[0]), int(o[1])

W, H = probe_size(SRC)
if W / H < OW / OH:     # source narrower than the output (e.g. 576x1024 portrait -> 9:16): crop ~87% of the width (original behaviour)
    CW = int(W * 0.868) // 2 * 2; CH = int(CW * OH / OW) // 2 * 2
else:                   # wider source (e.g. 1280x720 landscape -> 1:1, 4:5, 3:4, 9:16): full height, crop the width around the face
    CH = H // 2 * 2; CW = int(CH * OW / OH) // 2 * 2
print(f'source {W}x{H} -> crop {CW}x{CH} -> output {OW}x{OH}', flush=True)

def frames():
    """CFR frames by timestamp. -ss before -i = accurate seek when re-encoding.
    The Facebook DASH master switches resolution mid-stream, so force the rawvideo frame size;
    otherwise the fixed-size pipe reads desync and stop early."""
    p = subprocess.Popen(['ffmpeg', '-v', 'error', '-ss', str(T0), '-i', SRC, '-t', str(DUR), '-an',
                          '-vf', f'fps={FPS}', '-s', f'{W}x{H}', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-'],
                         stdout=subprocess.PIPE)
    n = W * H * 3
    while True:
        parts, got = [], 0
        while got < n:
            chunk = p.stdout.read(n - got)
            if not chunk:
                break
            parts.append(chunk); got += len(chunk)
        if got < n:
            break
        yield np.frombuffer(b''.join(parts), np.uint8).reshape(H, W, 3)

# ---- one pass: detect on a small frame, buffer ~13 frames, then crop/write ----
# The old version decoded the whole clip twice (track, then crop). Detection at 640 px
# keeps the same tracked face and costs a fraction of full-1080p YuNet.
DET_W = 640
DET_H = max(2, int(round(H * DET_W / W / 2) * 2))
if abs(W / H - OW / OH) < 1e-3:
    fd = None
else:
    fd = cv2.FaceDetectorYN.create(os.environ.get('YUNET','yunet.onnx'), '', (DET_W, DET_H), 0.5, 0.3, 5000)
samples = []                 # (time, full-res cx, full-res cy) every 3rd frame
sx, sy = [], []              # smoothed sample values, grown lazily
last = (W / 2, H * 0.3)
LOOKAHEAD = 4                # median window is 9 samples (k=9), so 4 samples ahead

def smoothed(si):
    """Median(9) + 0.9 EMA up to sample index si. Causal, so future samples never change earlier output."""
    while len(sx) <= si:
        k = len(sx)
        idx = [min(max(q, 0), len(samples) - 1) for q in range(k - LOOKAHEAD, k + LOOKAHEAD + 1)]
        mx = float(np.median([samples[q][1] for q in idx]))
        my = float(np.median([samples[q][2] for q in idx]))
        if k == 0:
            ex, ey = mx, my
        else:
            ex, ey = 0.9 * sx[k - 1] + 0.1 * mx, 0.9 * sy[k - 1] + 0.1 * my
        sx.append(ex); sy.append(ey)
    return sx[si], sy[si]

def face_xy(fr):
    small = cv2.resize(fr, (DET_W, DET_H), interpolation=cv2.INTER_AREA)
    _, faces = fd.detect(small)
    if faces is None or not len(faces):
        return last
    f = max(faces, key=lambda f: f[-1])
    return (float(f[0] + f[2] / 2) * W / DET_W, float(f[1] + f[3] / 2) * H / DET_H)

# Grade styles: "clean" = chapter 1 look; "warm" = punchier + warmer (default from chapter 2 on).
# Pick with env GRADE_STYLE (set by forge/render.py from profile output.grade); keep banding in mind on 720p.
GRADES = {
    'clean': "eq=contrast=1.07:saturation=0.92:gamma=1.02,"
             "colorbalance=rm=0.03:gm=-0.07:bm=0.02:rh=0.02:gh=-0.04",
    'warm':  "eq=contrast=1.13:saturation=1.07:gamma=1.03,"
             "colorbalance=rm=0.07:gm=-0.03:bm=-0.07:rh=0.06:gh=-0.01:bh=-0.05",
    # v2 soft for a green-wall phone source.
    'v2':    "colorchannelmixer=rr=1:rg=0:rb=0:gr=0:gg=0.955:gb=0:br=0:bg=0:bb=1.02,"
             "eq=contrast=1.03:brightness=0.03:saturation=1.05:gamma=0.90,"
             "curves=preset=lighter",
}
GRADE_STYLE = os.environ.get('GRADE_STYLE', 'warm')
grade = GRADES.get(GRADE_STYLE, GRADES['warm'])
print(f'grade {GRADE_STYLE}', flush=True)
_SHARPEN = "" if GRADE_STYLE == 'v2' else "unsharp=5:5:0.8:5:5:0.0,"
vf = (f"hqdn3d=3:2:5:5,scale={OW}:{OH}:flags=lanczos,{_SHARPEN}" + grade)
vid_tmp = OUT + '.v.mp4'
if abs(W / H - OW / OH) < 1e-3:
    print('exact aspect: direct ffmpeg path (no face tracking)', flush=True)
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-ss', str(T0), '-i', SRC, '-t', str(DUR), '-an',
                    '-vf', vf, '-c:v', 'libx264', '-crf', '20', '-preset', 'veryfast',
                    '-maxrate', '4M', '-bufsize', '8M', '-pix_fmt', 'yuv420p', vid_tmp], check=True)
    a_in = ['-i', AUDIO] if AUDIO else ['-ss', str(T0), '-i', SRC, '-t', str(DUR)]
    subprocess.run(['ffmpeg', '-v', 'error', '-y', '-i', vid_tmp] + a_in +
                   ['-map', '0:v', '-map', '1:a', '-c:v', 'copy', '-c:a', 'aac', '-b:a', '160k',
                    '-shortest', '-movflags', '+faststart', OUT], check=True)
    print('exact aspect: wrote', OUT, flush=True)
    sys.exit(0)
enc = subprocess.Popen(['ffmpeg', '-v', 'error', '-y', '-f', 'rawvideo', '-pix_fmt', 'bgr24', '-s', f'{CW}x{CH}',
                        '-r', str(FPS), '-i', '-', '-vf', vf, '-an', '-c:v', 'libx264', '-crf', '20',
                        '-preset', 'veryfast', '-maxrate', '4M', '-bufsize', '8M', '-pix_fmt', 'yuv420p', vid_tmp],
                       stdin=subprocess.PIPE)
buf = []                     # [(frame_index, frame)] waiting for enough future samples
nframes = 0

def write_frames(limit_index):
    """Write buffered frames up to limit_index (inclusive)."""
    while buf and buf[0][0] <= limit_index:
        j, fr = buf.pop(0)
        t = j / FPS
        if t >= WARM - 1e-6:
            si = j // 3
            smoothed(min(si + LOOKAHEAD, len(samples) - 1))
            upto = min(si + LOOKAHEAD, len(sx) - 1)
            times = [samples[q][0] for q in range(upto + 1)]
            x = float(np.interp(t, times, sx[:upto + 1]))
            y = float(np.interp(t, times, sy[:upto + 1]))
            x0 = int(np.clip(x - CW / 2, 0, W - CW))
            y0 = int(np.clip(y - CH * 0.30, 0, H - CH))
            enc.stdin.write(np.ascontiguousarray(fr[y0:y0 + CH, x0:x0 + CW]).tobytes())

for i, fr in enumerate(frames()):
    nframes = i + 1
    if i % 3 == 0:
        last = face_xy(fr)
        samples.append((i / FPS, last[0], last[1]))
    buf.append((i, fr))
    # A frame j needs samples up to j//3 + LOOKAHEAD; we have samples up to i//3.
    write_frames(i - 3 * LOOKAHEAD)
write_frames(10 ** 12)
enc.stdin.close(); enc.wait()

# ---- audio: same START/DUR on the same clock ----
if AUDIO:
    a_in = ['-i', AUDIO]
else:
    a_in = ['-ss', str(T0), '-i', SRC, '-t', str(DUR)]
mux = ['ffmpeg', '-v', 'error', '-y', '-i', vid_tmp] + a_in + ['-map', '0:v', '-map', '1:a', '-c:v', 'copy',
       '-c:a', 'aac', '-b:a', '160k', '-shortest', '-movflags', '+faststart', OUT]
subprocess.run(mux)
print('frames', nframes, 'duration', nframes / FPS, '->', OUT)
