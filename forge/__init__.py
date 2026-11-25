from pathlib import Path

APP_NAME = "reelforge"
VERSION = (Path(__file__).resolve().parents[1] / "VERSION").read_text().strip()
