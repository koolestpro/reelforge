#!/usr/bin/env python3
"""Write a synthetic source video for the demo. No footage, no keys, no network."""
from __future__ import annotations

import subprocess
from pathlib import Path

BASE = Path(__file__).resolve().parents[2]
INBOX = BASE / "inbox"


def main():
    INBOX.mkdir(parents=True, exist_ok=True)
    out = INBOX / "demo.mp4"
    subprocess.run(
        [
            "ffmpeg", "-nostdin", "-v", "error", "-y",
            "-f", "lavfi", "-i", "testsrc2=size=1080x1920:rate=30:duration=12",
            "-f", "lavfi", "-i", "sine=frequency=220:duration=12:sample_rate=48000",
            "-af", "volume='if(lt(mod(t,2),0.4),0.02,if(lt(mod(t,2),1.6),1,0.02))':eval=frame",
            "-c:v", "libx264", "-pix_fmt", "yuv420p",
            "-c:a", "aac", "-shortest", "-movflags", "+faststart",
            str(out),
        ],
        check=True,
    )
    print(out)


if __name__ == "__main__":
    main()
