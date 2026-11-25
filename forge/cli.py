#!/usr/bin/env python3
"""Command line for the video clip workflow."""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
from pathlib import Path

BASE = Path(__file__).resolve().parents[1]
HERE = Path(__file__).resolve().parent
VERSION = (BASE / "VERSION").read_text().strip() if (BASE / "VERSION").is_file() else "0.0.0"
sys.path.insert(0, str(HERE))

PLUGIN = BASE / "plugins" / "islamic"
if PLUGIN.is_dir() and os.environ.get("FORGE_PLUGINS", "").lower() in {"islamic", "all"}:
    sys.path.insert(0, str(PLUGIN))

import config as cfg  # noqa: E402
import deliver as dl  # noqa: E402


def cmd_init(name):
    source = cfg.PROFILES / "_template"
    target = cfg.PROFILES / name
    if target.exists():
        raise cfg.ConfigError(f"profile already exists: {target}")
    if not source.is_dir():
        raise cfg.ConfigError(f"missing template profile: {source}")
    shutil.copytree(source, target)
    (target / "assets" / "sfx").mkdir(parents=True, exist_ok=True)
    print(f"created profiles/{name}")
    print(f"edit profiles/{name}/profile.json and profiles/{name}/clips.csv")
    return 0


def cmd_check(profile):
    job_path, job, clips, _profile, _style = cfg.build_job(profile)
    import check

    errors, warnings = check.validate_job(str(job_path))
    for warning in warnings:
        print(f"note: {warning}")
    for error in errors:
        print(f"error: {error}", file=sys.stderr)
    if errors:
        return 1
    print(f"check: ok. {len(clips)} clip(s), {job['aspect']}, source {job['source']}")
    return 0


def render_profile(profile, clip_id=None):
    job_path, job, clips, _profile, _style = cfg.build_job(profile, clip_id)
    import render

    argv = ["forge", str(job_path), "--shorts"]
    if clip_id:
        argv += ["--chapters", clip_id]
    previous = sys.argv
    sys.argv = argv
    try:
        code = render.main()
    finally:
        sys.argv = previous
    if code:
        return code

    target = BASE / "deliver" / profile / job["id"]
    manifest, target = dl.collect(target, job, profile)
    print(f"delivered {len(manifest['clips'])} clip(s) to {target}")
    return 0


def cmd_render(profile, clip_id=None):
    return render_profile(profile, clip_id)


def cmd_transcribe(profile, provider=None):
    _job_path, job, _clips, _profile, _style = cfg.build_job(profile)
    config_path = BASE / "config" / "asr.json"
    if not config_path.is_file():
        config_path = BASE / "config" / "asr.example.json"
    config = json.loads(config_path.read_text()) if config_path.is_file() else {}
    engines = provider or ",".join(config.get("provider_order", ["elevenlabs", "9jalingo", "groq"]))
    out = Path(job["workdir"]) / "transcript.raw.json"
    print(f"transcribing with {engines}")
    print("this command may spend credit on your own key")
    subprocess.run(
        [sys.executable, str(HERE / "asr.py"), job["source"], "--out", str(out), "--engines", engines],
        check=True,
    )
    print(f"transcript: {out}")
    return 0


def cmd_version():
    print(f"reelforge {VERSION}")
    return 0


def cmd_update():
    if not (BASE / ".git").is_dir():
        print("No git checkout here. Download the latest release from")
        print("https://github.com/koolestpro/reelforge/releases")
        return 0

    def git(*args, check=True):
        return subprocess.run(
            ["git", "-C", str(BASE), *args], capture_output=True, text=True, check=check
        )

    status = git("status", "--porcelain", check=False)
    if status.returncode != 0:
        raise cfg.ConfigError(f"git status failed: {status.stderr.strip()}")
    if status.stdout.strip():
        print("Uncommitted changes found. Commit or stash them first:", file=sys.stderr)
        print(status.stdout.strip(), file=sys.stderr)
        return 1

    remotes = git("remote", check=False).stdout.split()
    remote = "upstream" if "upstream" in remotes else "origin"
    if remote not in remotes:
        raise cfg.ConfigError("no git remote found; download the latest release instead")

    before = git("describe", "--tags", "--abbrev=0", check=False).stdout.strip() or "untagged"
    print(f"updating from {remote} (currently {before})")
    git("fetch", remote, "--tags")

    merge = git("merge", "--ff-only", f"{remote}/main", check=False)
    if merge.returncode != 0:
        print("Fast-forward failed. You have local commits or diverged history.", file=sys.stderr)
        print((merge.stdout + merge.stderr).strip(), file=sys.stderr)
        return 1

    subprocess.run(
        [sys.executable, "-m", "pip", "install", "--quiet", "-r", str(BASE / "requirements.txt")],
        check=True,
    )
    after = git("describe", "--tags", "--abbrev=0", check=False).stdout.strip() or "untagged"
    print(f"updated: {before} -> {after}")
    return 0


def cmd_demo():
    media = BASE / "inbox" / "demo.mp4"
    if not media.exists():
        fixture = BASE / "tests" / "fixtures" / "make_demo_media.py"
        subprocess.run([sys.executable, str(fixture)], check=True)
    if not (cfg.PROFILES / "demo").is_dir():
        cmd_init("demo")
    profile_path = cfg.PROFILES / "demo" / "profile.json"
    profile = json.loads(profile_path.read_text())
    profile["studio"] = "Demo Studio"
    profile["series"] = "Demo Series"
    profile["source"] = "../../inbox/demo.mp4"
    profile["output"]["aspect"] = "9:16"
    profile["output"]["speed"] = 1.0
    profile_path.write_text(json.dumps(profile, indent=2) + "\n")
    (cfg.PROFILES / "demo" / "clips.csv").write_text(
        "id,start,end,title,subtitle,aspect\n"
        "c1,00:00:01.0,00:00:07.0,Demo clip,A synthetic test,9:16\n"
    )
    return render_profile("demo")


def main():
    parser = argparse.ArgumentParser(prog="forge")
    parser.add_argument("--version", action="version", version=f"reelforge {VERSION}")
    sub = parser.add_subparsers(dest="command", required=True)

    init = sub.add_parser("init", help="create a profile from the template")
    init.add_argument("name")

    check = sub.add_parser("check", help="validate profile, clips and tools")
    check.add_argument("--profile", required=True)

    render = sub.add_parser("render", help="render clips")
    render.add_argument("--profile", required=True)
    render.add_argument("--id", default="")

    transcribe = sub.add_parser("transcribe", help="transcribe with your own key")
    transcribe.add_argument("--profile", required=True)
    transcribe.add_argument("--provider", default="")

    sub.add_parser("demo", help="synthetic end to end run")
    sub.add_parser("update", help="pull the latest release into this checkout")
    sub.add_parser("version", help="print the version")

    args = parser.parse_args()
    try:
        if args.command == "init":
            return cmd_init(args.name)
        if args.command == "check":
            return cmd_check(args.profile)
        if args.command == "render":
            return cmd_render(args.profile, args.id or None)
        if args.command == "transcribe":
            return cmd_transcribe(args.profile, args.provider or None)
        if args.command == "demo":
            return cmd_demo()
        if args.command == "update":
            return cmd_update()
        if args.command == "version":
            return cmd_version()
        parser.error("unknown command")
    except cfg.ConfigError as error:
        print(f"error: {error}", file=sys.stderr)
        return 2
    except subprocess.CalledProcessError as error:
        print(f"error: command failed: {error}", file=sys.stderr)
        return error.returncode


if __name__ == "__main__":
    sys.exit(main())
