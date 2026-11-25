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

chmod +x forge.sh setup.sh 2>/dev/null || true
mkdir -p inbox work deliver

echo "setup: ok"
echo "next: ./forge.sh init my-studio"
