# Third party

No media, fonts or models are bundled. Install or supply your own.

| Component | Licence | Notes |
| --- | --- | --- |
| ffmpeg and ffprobe | LGPL or GPL depending on build | Installed by the user, not shipped |
| Pillow | HPND | Python dependency |
| numpy | BSD-3-Clause | Python dependency |
| opencv-python-headless | Apache-2.0 | Python dependency |
| Inter | SIL Open Font License 1.1 | Bundled in `forge/fonts/` |
| Amiri | SIL Open Font License 1.1 | Optional, for Arabic titles |
| YuNet face detector | OpenCV Zoo model licence | Downloaded by `setup.sh` when possible |
| ElevenLabs, Groq, DeepInfra, 9jaLingo | Vendor terms | Only used by `transcribe`, with the user's own key |

Project licence: FSL-1.1-MIT. See `LICENSE`.

Sound effects are generated locally with ffmpeg on first render. No music is included.
