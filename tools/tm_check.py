"""Which source frame is each render frame really showing?

usage: tm_check.py SOURCE SRC_START RENDER label [CROP_W=500] [CROP_H=CROP_W*16/9]
use the uncut per-chapter render (<workdir>/<id>.v.mp4), not the silence-cut short. 1:1: ... 720 720
For several render timestamps t, template-match the render frame (shrunk back to the crop size)
against source frames within +-2.5 s of SRC_START+t, and report the time offset of the best match.
offset > 0: render shows a source moment LATER than the audio clock says (video ahead of audio).
offset < 0: video behind audio. ~0 (within 1 frame = 33 ms) = in sync.
"""
import cv2, numpy as np, subprocess, sys

SRC, S, RND, label = sys.argv[1], float(sys.argv[2]), sys.argv[3], sys.argv[4]
CW = int(sys.argv[5]) if len(sys.argv) > 5 else 500
CH = int(sys.argv[6]) if len(sys.argv) > 6 else int(CW * 16 / 9)   # crop height in source px (default 9:16; for 1:1 pass CW=CH=720)
R = 25

def probe(f):
    o = subprocess.run(['ffprobe', '-v', 'error', '-select_streams', 'v:0', '-show_entries', 'stream=width,height',
                        '-of', 'csv=p=0', f], capture_output=True, text=True).stdout.strip().split(',')
    return int(o[0]), int(o[1])

W, H = probe(SRC)

def one_frame(f, t, size):
    raw = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(t), '-i', f, '-frames:v', '1', '-vf',
                          f'scale={size[0]}:{size[1]},format=gray', '-f', 'rawvideo', '-'], capture_output=True).stdout
    return np.frombuffer(raw, np.uint8).reshape(size[1], size[0])

def src_frames(t0, t1):
    p = subprocess.run(['ffmpeg', '-v', 'error', '-ss', str(t0), '-t', str(t1 - t0), '-i', SRC, '-an',
                        '-vf', f'fps={R},format=gray', '-f', 'rawvideo', '-'], capture_output=True)
    raw = np.frombuffer(p.stdout, np.uint8)
    n = len(raw) // (W * H)
    return raw[:n * W * H].reshape(n, H, W)

res = []
for t in [x for x in (4,14,24,34,44,54) if x < float(__import__("subprocess").run(["ffprobe","-v","error","-show_entries","format=duration","-of","csv=p=0",RND],capture_output=True,text=True).stdout)-3]:
    tpl = one_frame(RND, t, (CW, CH))
    c0 = S + t - 2.5
    fr = src_frames(c0, c0 + 5.0)
    best = (-2, 0)
    for k, f in enumerate(fr):
        m = cv2.matchTemplate(f, tpl, cv2.TM_CCOEFF_NORMED).max()
        if m > best[0]:
            best = (m, k)
    src_t = c0 + best[1] / R
    res.append((t, (src_t - (S + t)) * 1000, best[0]))
print(f"{label}: " + "  ".join(f"t={t:>2d}s: {o:+5.0f}ms (match {m:.2f})" for t, o, m in res))
