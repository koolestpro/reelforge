"""Find speech pauses >= MIN_PAUSE s in a 16k mono wav.  usage: detect_pauses.py in.wav out.json
50 ms RMS envelope vs a rolling 15th-percentile floor; a frame is 'pause' if < floor*3 (and near-silent)."""
import numpy as np, subprocess, sys, json
wav, out = sys.argv[1:3]
MIN_PAUSE = 0.4
a = np.frombuffer(subprocess.run(['ffmpeg','-nostdin','-v','error','-i',wav,'-ac','1','-ar','16000','-f','f32le','-'],capture_output=True).stdout, np.float32)
hop = 800
e = np.sqrt((a[:len(a)//hop*hop].reshape(-1, hop)**2).mean(1)) + 1e-9
win = 400  # 20 s
floor = np.array([np.percentile(e[max(0,i-win//2):i+win//2], 15) for i in range(0, len(e))]) if len(e) < 4000 else None
if floor is None:
    from numpy.lib.stride_tricks import sliding_window_view
    pad = np.pad(e, (win//2, win//2), mode='edge')
    floor = np.percentile(sliding_window_view(pad, win+1)[::10], 15, axis=1)
    floor = np.interp(np.arange(len(e)), np.arange(0, len(e), 10)[:len(floor)], floor)
quiet = e < np.maximum(floor * 2.5, 1e-4)
pauses = []; i = 0
while i < len(quiet):
    if quiet[i]:
        j = i
        while j < len(quiet) and quiet[j]: j += 1
        if (j - i) * 0.05 >= MIN_PAUSE: pauses.append([round(i*0.05,2), round(j*0.05,2)])
        i = j
    else: i += 1
json.dump(pauses, open(out, 'w'))
print(len(pauses), 'pauses')
