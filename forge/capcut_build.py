#!/usr/bin/env python3
"""Build an editable CapCut project from a small clip spec.

The project is written as `draft_info.json` inside a CapCut project folder, using a real CapCut 9.8
draft as the schema template. Nothing is flattened: every cut is its own segment with its own source
in and out, overlays are separate tracks, and titles are native CapCut text.

    python3 capcut_build.py SPEC.json --projects "~/Movies/CapCut/User Data/Projects/com.lveditor.draft"

Spec:

    {
      "name": "2026-11-16_c13_interview_1x1",
      "canvas": {"width": 1080, "height": 1080, "ratio": "1:1"},
      "fps": 30,
      "source": "/abs/path/master.mp4",
      "source_duration": 2188.16,
      "fill": 1.7778,
      "segments": [{"in": 1444.62, "out": 1527.85, "note": "setup"}],
      "overlays": [{"path": "/abs/overlay.png", "start": 0, "end": null}],
      "texts": [{"text": "Line 1\nLine 2", "start": 0.2, "end": 6.0, "y": 0.22, "size": 9}]
    }

`fill` scales the source to fill the canvas (a 16:9 source in a 1:1 canvas needs 1.7778). A value of
1.0 leaves the fit-to-canvas default.
"""
import argparse
import copy
import json
import os
import time
import uuid
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATE = HERE / "capcut_template_9_8.json"
US = 1_000_000
PHOTO_DURATION = 10_800_000_000


class BuildError(Exception):
    pass


def _id():
    return uuid.uuid4().hex


def _material(entry, **overrides):
    item = copy.deepcopy(entry)
    item["id"] = _id()
    item["material_id"] = item["id"]
    for key, value in overrides.items():
        item[key] = value
    return item


def load_template(path=TEMPLATE):
    with open(path, encoding="utf-8") as handle:
        return json.load(handle)


def _check_spec(spec):
    for key in ("name", "source", "segments"):
        if key not in spec:
            raise BuildError(f"spec is missing {key}")
    segments = spec["segments"]
    if not segments:
        raise BuildError("spec has no segments")
    total = 0.0
    for index, seg in enumerate(segments):
        start, end = float(seg["in"]), float(seg["out"])
        if end <= start:
            raise BuildError(f"segment {index} ends before it starts")
        if spec.get("source_duration") and end > float(spec["source_duration"]) + 0.5:
            raise BuildError(f"segment {index} runs past the source")
        total += end - start
    return total


def build(spec, template=None):
    """Return (draft_info dict, report dict)."""
    template = template or load_template()
    total = _check_spec(spec)
    canvas = spec.get("canvas") or {"width": 1080, "height": 1080, "ratio": "1:1"}
    fps = int(spec.get("fps") or 30)

    draft = copy.deepcopy(template)
    draft["id"] = _id()
    draft["name"] = spec["name"]
    draft["duration"] = int(round(total * US))
    draft["fps"] = fps
    now = int(time.time())
    draft["create_time"] = now
    draft["update_time"] = now
    draft["canvas_config"] = {
        "ratio": canvas.get("ratio", "1:1"),
        "width": int(canvas["width"]),
        "height": int(canvas["height"]),
        "background": None,
    }

    base_video = template["tracks"][0]["segments"][0]
    base_photo = template["tracks"][1]["segments"][0]
    base_text = template["tracks"][3]["segments"][0]
    video_refs = base_video["extra_material_refs"]
    photo_refs = base_photo["extra_material_refs"]

    materials = draft["materials"]
    for key, value in list(materials.items()):
        if isinstance(value, list):
            materials[key] = []

    source_path = str(Path(spec["source"]).expanduser())
    if not Path(source_path).exists():
        raise BuildError(f"source not found: {source_path}")
    width, height = (spec.get("source_size") or [1920, 1080])[:2]
    master = _material(
        base_video,
        type="video",
        path=source_path,
        material_name=Path(source_path).name,
        duration=int(round(float(spec.get("source_duration") or total) * US)),
        width=int(width),
        height=int(height),
        has_audio=True,
    )
    materials["videos"].append(master)

    video_segments = []
    cursor = 0
    fill = float(spec.get("fill") or 1.0)
    for index, seg in enumerate(spec["segments"]):
        start, end = float(seg["in"]), float(seg["out"])
        duration = int(round((end - start) * US))
        segment = copy.deepcopy(base_video)
        segment["id"] = _id()
        segment["material_id"] = master["id"]
        segment["extra_material_refs"] = list(video_refs)
        segment["source_timerange"] = {"start": int(round(start * US)), "duration": duration}
        segment["target_timerange"] = {"start": int(cursor), "duration": duration}
        segment["render_timerange"] = {"start": 0, "duration": 0}
        segment["render_index"] = index
        segment["track_render_index"] = 0
        segment["keyframe_refs"] = []
        segment["desc"] = seg.get("note", "")
        if fill != 1.0:
            segment["clip"] = copy.deepcopy(segment["clip"])
            segment["clip"]["scale"] = {"x": fill, "y": fill}
            segment["clip"]["transform"] = {"x": 0.0, "y": 0.0}
            segment["uniform_scale"] = {"on": True, "value": fill}
        video_segments.append(segment)
        cursor += duration

    main_track = {
        "id": _id(),
        "type": "video",
        "flag": 0,
        "attribute": 0,
        "name": "cuts",
        "is_default_name": False,
        "segments": video_segments,
    }
    tracks = [main_track]

    overlays = spec.get("overlays") or []
    for order, overlay in enumerate(overlays):
        path = str(Path(overlay["path"]).expanduser())
        if not Path(path).exists():
            raise BuildError(f"overlay not found: {path}")
        photo = _material(
            base_photo,
            type="photo",
            path=path,
            material_name=Path(path).name,
            duration=PHOTO_DURATION,
            width=1080,
            height=1080,
            has_audio=False,
        )
        materials["videos"].append(photo)
        start = float(overlay.get("start") or 0)
        end = overlay.get("end")
        duration = int(round((float(end) - start) * US)) if end else draft["duration"] - int(round(start * US))
        segment = copy.deepcopy(base_photo)
        segment["id"] = _id()
        segment["material_id"] = photo["id"]
        segment["extra_material_refs"] = list(photo_refs)
        segment["source_timerange"] = {"start": 0, "duration": duration}
        segment["target_timerange"] = {"start": int(round(start * US)), "duration": duration}
        segment["render_timerange"] = {"start": 0, "duration": 0}
        segment["render_index"] = 0
        segment["track_render_index"] = 1 + order
        segment["keyframe_refs"] = []
        segment["clip"] = copy.deepcopy(segment["clip"])
        segment["clip"]["scale"] = {"x": 1.0, "y": 1.0}
        segment["clip"]["transform"] = {"x": 0.0, "y": 0.0}
        segment["volume"] = 0.0
        tracks.append({
            "id": _id(),
            "type": "video",
            "flag": 2,
            "attribute": 0,
            "name": overlay.get("name") or f"overlay{order + 1}",
            "is_default_name": False,
            "segments": [segment],
        })

    texts = spec.get("texts") or []
    if texts:
        text_segments = []
        for order, item in enumerate(texts):
            text_material = copy.deepcopy(template["materials"]["texts"][0])
            text_material["id"] = _id()
            runs = item.get("runs")
            if runs:
                text = "".join(run["text"] for run in runs)
                styles = []
                pos = 0
                for run in runs:
                    run_text = run["text"]
                    run_size = float(run.get("size") or 9)
                    run_color = run.get("color") or [1.0, 1.0, 1.0]
                    styles.append({
                        "fill": {"alpha": 1.0,
                                 "content": {"render_type": "solid",
                                             "solid": {"alpha": 1.0, "color": list(run_color)}}},
                        "range": [pos, pos + max(0, len(run_text) - 1)],
                        "size": run_size,
                        "bold": bool(run.get("bold", True)),
                        "italic": False,
                        "underline": False,
                        "strokes": [],
                        "font": {"id": "", "path": "/Applications/CapCut.app/Contents/Resources/Font/SystemFont/en.ttf"},
                    })
                    pos += len(run_text)
                text_material["content"] = json.dumps(
                    {"styles": styles, "text": text}, ensure_ascii=False)
                text_material["font_size"] = float(runs[0].get("size") or 9)
                text_material["text_size"] = int(round(float(runs[0].get("size") or 9) * 3))
                text_material["alignment"] = int(item.get("align", 1))
                materials["texts"].append(text_material)
                segment = copy.deepcopy(base_text)
                segment["id"] = _id()
                segment["material_id"] = text_material["id"]
                segment["extra_material_refs"] = []
                segment["source_timerange"] = None
                start = float(item.get("start") or 0)
                end = float(item["end"]) if item.get("end") else total
                segment["target_timerange"] = {
                    "start": int(round(start * US)),
                    "duration": int(round((end - start) * US)),
                }
                segment["render_timerange"] = {"start": 0, "duration": 0}
                segment["render_index"] = 15000 + order
                segment["track_render_index"] = 1 + len(overlays) + order
                segment["keyframe_refs"] = []
                segment["clip"] = copy.deepcopy(segment["clip"])
                segment["clip"]["scale"] = {
                    "x": float(item.get("scale", 1.0)),
                    "y": float(item.get("scale", 1.0)),
                }
                segment["clip"]["transform"] = {"x": float(item.get("x", 0.0)), "y": float(item.get("y", 0.22))}
                text_segments.append(segment)
                continue
            size = float(item.get("size") or 9)
            color = item.get("color") or [1.0, 1.0, 1.0]
            style = {
                "fill": {"alpha": 1.0,
                         "content": {"render_type": "solid",
                                     "solid": {"alpha": 1.0, "color": list(color)}}},
                "range": [0, len(item["text"])],
                "size": size,
                "bold": bool(item.get("bold", True)),
                "italic": False,
                "underline": False,
                "strokes": [],
                "font": {"id": "", "path": "/Applications/CapCut.app/Contents/Resources/Font/SystemFont/en.ttf"},
            }
            text_material["content"] = json.dumps(
                {"styles": [style], "text": item["text"]}, ensure_ascii=False)
            text_material["font_size"] = size
            text_material["text_size"] = int(round(size * 3))
            text_material["alignment"] = int(item.get("align", 1))
            materials["texts"].append(text_material)

            segment = copy.deepcopy(base_text)
            segment["id"] = _id()
            segment["material_id"] = text_material["id"]
            segment["extra_material_refs"] = []
            segment["source_timerange"] = None
            start = float(item.get("start") or 0)
            end = float(item["end"]) if item.get("end") else total
            segment["target_timerange"] = {
                "start": int(round(start * US)),
                "duration": int(round((end - start) * US)),
            }
            segment["render_timerange"] = {"start": 0, "duration": 0}
            segment["render_index"] = 15000 + order
            segment["track_render_index"] = 1 + len(overlays) + order
            segment["keyframe_refs"] = []
            segment["clip"] = copy.deepcopy(segment["clip"])
            segment["clip"]["scale"] = {
                "x": float(item.get("scale", 1.0)),
                "y": float(item.get("scale", 1.0)),
            }
            segment["clip"]["transform"] = {"x": float(item.get("x", 0.0)), "y": float(item.get("y", 0.22))}
            text_segments.append(segment)
        tracks.append({
            "id": _id(),
            "type": "text",
            "flag": 0,
            "attribute": 0,
            "name": "titles",
            "is_default_name": False,
            "segments": text_segments,
        })

    draft["tracks"] = tracks
    report = {
        "name": spec["name"],
        "duration": round(total, 3),
        "segments": len(video_segments),
        "overlays": len(overlays),
        "texts": len(texts),
        "cuts": [
            {"in": seg["in"], "out": seg["out"]} for seg in spec["segments"]
        ],
    }
    return draft, report


def write_project(draft, report, projects_dir):
    projects_dir = Path(projects_dir).expanduser()
    project_dir = projects_dir / draft["name"]
    project_dir.mkdir(parents=True, exist_ok=True)
    target = project_dir / "draft_info.json"
    if target.exists():
        backup = project_dir / "draft_info.json.bak"
        if not backup.exists():
            backup.write_bytes(target.read_bytes())
    target.write_text(json.dumps(draft, ensure_ascii=False), encoding="utf-8")
    meta = {
        "name": draft["name"],
        "duration": report["duration"],
        "segments": report["segments"],
        "built": time.strftime("%Y-%m-%dT%H:%M:%S"),
        "note": "built by forge/capcut_build.py",
    }
    (project_dir / "capcut_build.json").write_text(json.dumps(meta, indent=2), encoding="utf-8")
    return project_dir


def make_cover(source, seconds, target, size=1080):
    """Write a square cover frame. Uses ffmpeg one-shot."""
    import subprocess
    target = Path(target)
    command = [
        "ffmpeg", "-hide_banner", "-nostats", "-y", "-ss", f"{seconds}",
        "-i", str(source), "-frames:v", "1", "-vf",
        f"scale={size}:{size}:force_original_aspect_ratio=increase,crop={size}:{size}",
        "-q:v", "3", str(target),
    ]
    result = subprocess.run(command, capture_output=True, text=True)
    if result.returncode != 0 or not target.exists():
        raise BuildError(f"cover frame failed: {result.stderr[-300:]}")
    return target


def register_in_root_meta(draft, project_dir, projects_dir, cover=None):
    """Add or update this project in CapCut's root_meta_info.json.

    CapCut only rescans its project list at launch, and a folder that is not listed in
    root_meta_info.json may not appear at all. This mirrors the entry CapCut writes itself.
    """
    projects_dir = Path(projects_dir).expanduser()
    root_meta = projects_dir / "root_meta_info.json"
    if not root_meta.exists():
        return None
    data = json.loads(root_meta.read_text(encoding="utf-8"))
    store = data.setdefault("all_draft_store", [])
    now_us = int(time.time() * 1_000_000)
    entry = next((item for item in store if item.get("draft_name") == draft["name"]), None)
    if entry is None:
        entry = {
            "cloud_draft_cover": False,
            "cloud_draft_sync": False,
            "draft_cloud_last_action_download": False,
            "draft_cloud_purchase_info": "",
            "draft_cloud_template_id": "",
            "draft_cloud_tutorial_info": "",
            "draft_cloud_videocut_purchase_info": "",
            "draft_cover": "",
            "draft_fold_path": str(project_dir),
            "draft_id": str(uuid.uuid4()).upper(),
            "draft_is_ai_shorts": False,
            "draft_is_cloud_temp_draft": False,
            "draft_is_infinite_canvas_draft": False,
            "draft_is_invisible": False,
            "draft_is_pippit_draft": False,
            "draft_is_web_article_video": False,
            "draft_json_file": str(project_dir / "draft_info.json"),
            "draft_name": draft["name"],
            "draft_new_version": draft.get("new_version", ""),
            "draft_root_path": str(projects_dir),
            "draft_timeline_materials_size": 0,
            "draft_type": "",
            "draft_web_article_video_enter_from": "",
            "pippit_avatar_url": "",
            "pippit_extra_info": "",
            "pippit_id": "",
            "pippit_user_name": "",
            "streaming_edit_draft_ready": True,
            "tm_draft_cloud_completed": "",
            "tm_draft_cloud_entry_id": -1,
            "tm_draft_cloud_modified": 0,
            "tm_draft_cloud_parent_entry_id": -1,
            "tm_draft_cloud_space_id": -1,
            "tm_draft_cloud_user_id": -1,
            "tm_draft_create": now_us,
            "tm_draft_modified": now_us,
            "tm_draft_removed": 0,
            "tm_duration": draft["duration"],
        }
        store.append(entry)
    else:
        entry["tm_draft_modified"] = now_us
        entry["tm_duration"] = draft["duration"]
    if cover:
        entry["draft_cover"] = str(cover)
    entry["draft_fold_path"] = str(project_dir)
    entry["draft_json_file"] = str(project_dir / "draft_info.json")
    data["draft_ids"] = len(store)
    backup = root_meta.with_suffix(".json.bak")
    if not backup.exists():
        backup.write_bytes(root_meta.read_bytes())
    root_meta.write_text(json.dumps(data, ensure_ascii=False), encoding="utf-8")
    return entry["draft_id"]


def write_draft_meta(project_dir, draft, report, draft_id=None, root_path=None):
    """Write the sidecar `draft_meta_info.json` CapCut keeps next to draft_info.json."""
    project_dir = Path(project_dir)
    now_us = int(time.time() * 1_000_000)
    meta = {
        "cloud_draft_cover": False,
        "cloud_draft_sync": False,
        "cloud_package_completed_time": "",
        "draft_cloud_capcut_purchase_info": "",
        "draft_cloud_last_action_download": False,
        "draft_cloud_package_type": "",
        "draft_cloud_purchase_info": "",
        "draft_cloud_template_id": "",
        "draft_cloud_tutorial_info": "",
        "draft_cloud_videocut_purchase_info": "",
        "draft_cover": "draft_cover.jpg",
        "draft_deeplink_url": "",
        "draft_enterprise_info": {"draft_enterprise_extra": "", "draft_enterprise_id": "",
                                  "draft_enterprise_name": "", "enterprise_material": []},
        "draft_fold_path": str(project_dir),
        "draft_has_unfinished_aigc_video_effect": False,
        "draft_id": draft_id or str(uuid.uuid4()).upper(),
        "draft_is_ae_produce": False,
        "draft_is_ai_packaging_used": False,
        "draft_is_ai_shorts": False,
        "draft_is_ai_translate": False,
        "draft_is_article_video_draft": False,
        "draft_is_cloud_temp_draft": False,
        "draft_is_from_deeplink": False,
        "draft_is_infinite_canvas_draft": False,
        "draft_is_invisible": False,
        "draft_is_pippit_draft": False,
        "draft_is_web_article_video": False,
        "draft_materials": [],
        "draft_materials_copied_info": [],
        "draft_name": draft["name"],
        "draft_need_rename_folder": False,
        "draft_new_version": draft.get("new_version", ""),
        "draft_removable_storage_device": "",
        "draft_root_path": str(Path(root_path).expanduser()) if root_path else str(project_dir.parent),
        "draft_segment_extra_info": [],
        "draft_timeline_materials_size_": 0,
        "draft_type": "",
        "draft_web_article_video_enter_from": "",
        "pippit_avatar_url": "",
        "pippit_extra_info": "",
        "pippit_id": "",
        "pippit_user_name": "",
        "tm_draft_cloud_completed": "",
        "tm_draft_cloud_entry_id": -1,
        "tm_draft_cloud_modified": 0,
        "tm_draft_cloud_parent_entry_id": -1,
        "tm_draft_cloud_space_id": -1,
        "tm_draft_cloud_user_id": -1,
        "tm_draft_create": now_us,
        "tm_draft_modified": now_us,
        "tm_draft_removed": 0,
        "tm_duration": draft["duration"],
    }
    (project_dir / "draft_meta_info.json").write_text(
        json.dumps(meta, ensure_ascii=False), encoding="utf-8")
    return meta


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("spec", help="clip spec JSON")
    parser.add_argument("--projects", default=os.path.expanduser(
        "~/Movies/CapCut/User Data/Projects/com.lveditor.draft"))
    parser.add_argument("--template", default=str(TEMPLATE))
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--register", action="store_true",
                        help="add the project to root_meta_info.json so CapCut lists it")
    parser.add_argument("--cover-time", type=float, default=None,
                        help="seconds into the source for the cover frame")
    args = parser.parse_args(argv)
    with open(args.spec, encoding="utf-8") as handle:
        spec = json.load(handle)
    draft, report = build(spec, load_template(args.template))
    print(json.dumps(report, indent=2))
    if args.dry_run:
        return 0
    project_dir = write_project(draft, report, args.projects)
    cover = None
    if args.cover_time is not None:
        cover = make_cover(spec["source"], args.cover_time, project_dir / "draft_cover.jpg")
    draft_id = None
    if args.register:
        draft_id = register_in_root_meta(draft, project_dir, args.projects, cover=cover)
        print(f"registered as {draft_id}")
    write_draft_meta(project_dir, draft, report, draft_id=draft_id, root_path=args.projects)
    print(f"wrote {project_dir}")
    print("Close CapCut before opening it, or relaunch CapCut so the project list rescans.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
