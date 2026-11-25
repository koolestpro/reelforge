#!/bin/sh
# One-time setup. Safe to run again.
set -eu
DIR=$(CDPATH= cd -- "$(dirname -- "$0")" && pwd)
cd "$DIR"

PY=${PYTHON:-python3}
if ! command -v "$PY" >/dev/null 2>&1; then
  echo "python3 is required" >&2
  exit 1
fi

for tool in ffmpeg ffprobe; do
  if ! command -v "$tool" >/dev/null 2>&1; then
    echo "$tool is required and was not found on PATH" >&2
    exit 1
  fi
done

if [ ! -d .venv ]; then
  "$PY" -m venv .venv
fi
./.venv/bin/python -m pip install --quiet --upgrade pip
./.venv/bin/python -m pip install --quiet -r requirements.txt

if ! ./.venv/bin/python -c "import sys; sys.path.insert(0, 'forge'); import overlays; overlays.inter('Bold')" >/dev/null 2>&1; then
  echo "warning: Inter did not load. Titles will fall back to a system font." >&2
fi

if [ ! -f forge/models/face_detection_yunet_2023mar.onnx ]; then
  mkdir -p forge/models
  if command -v curl >/dev/null 2>&1; then
    curl -fsSL -o forge/models/face_detection_yunet_2023mar.onnx \
      https://github.com/opencv/opencv_zoo/raw/main/models/face_detection_yunet/face_detection_yunet_2023mar.onnx \
      || echo "warning: optional face detector not downloaded. Exact-aspect sources still render." >&2
  fi
fi

chmod +x forge.sh setup.sh 2>/dev/null || true
mkdir -p inbox work deliver

echo "setup: ok"
echo "next: ./forge.sh init my-studio"
