#!/usr/bin/env python3
"""Download PolyHaven 3D assets for the Godot office/warehouse scenes.

Run once before opening the Godot project:
    python3 setup_assets.py

Downloads GLTF + textures for each asset into:
    godot/assets/polyhaven/{asset_id}/
"""
from __future__ import annotations

import argparse
import os
import sys
import urllib.request
import urllib.error
import json
import threading
from pathlib import Path

HERE = Path(__file__).parent
ASSETS_DIR = HERE / "assets" / "polyhaven"

# PolyHaven GLTF download pattern:
#   https://dl.polyhaven.org/file/ph-assets/Models/gltf/{res}/{id}/{id}_{res}.gltf
PH_GLTF = "https://dl.polyhaven.org/file/ph-assets/Models/gltf/{res}/{id}/{id}_{res}.gltf"
PH_FILES_API = "https://api.polyhaven.com/files/{id}"

# Assets we want. Tuple: (asset_id, label, notes)
ASSETS = [
    # --- Chairs (must be visually distinct and countable) ---
    ("SchoolChair_01",          "school_chair",         "navy plastic chair, metal legs"),
    ("modern_arm_chair_01",     "arm_chair",            "modern wooden armchair, leather"),
    ("metal_stool_01",          "stool",                "industrial metal stool"),
    ("mid_century_lounge_chair","lounge_chair",         "mid-century lounge chair"),
    # --- Desks ---
    ("metal_office_desk",       "office_desk",          "industrial metal desk"),
    ("SchoolDesk_01",           "school_desk",          "single-seat school desk"),
    # --- Supporting furniture ---
    ("side_table_01",           "side_table",           "minimalist wooden side table"),
    ("drawer_cabinet",          "drawer_cabinet",       "warm wood drawer cabinet"),
    ("steel_frame_shelves_02",  "shelves",              "industrial five-tier shelf"),
    ("CoffeeTable_01",          "coffee_table",         "wooden coffee table"),
]


def _fetch_file_list(asset_id: str) -> dict:
    url = PH_FILES_API.format(id=asset_id)
    try:
        with urllib.request.urlopen(url, timeout=15) as r:
            return json.loads(r.read())
    except Exception as e:
        print(f"  [warn] could not fetch file list for {asset_id}: {e}")
        return {}


def _download(url: str, dest: Path) -> bool:
    if dest.exists():
        return True
    dest.parent.mkdir(parents=True, exist_ok=True)
    tmp = dest.with_suffix(".tmp")
    try:
        urllib.request.urlretrieve(url, tmp)
        tmp.rename(dest)
        return True
    except urllib.error.HTTPError as e:
        if tmp.exists():
            tmp.unlink()
        if e.code == 404:
            return False
        print(f"  [warn] HTTP {e.code} for {url}")
        return False
    except Exception as e:
        if tmp.exists():
            tmp.unlink()
        print(f"  [warn] {e} for {url}")
        return False


PH_BASE = "https://dl.polyhaven.org/file/ph-assets/Models/gltf/{res}/{id}/"


def _deps_from_gltf(gltf_path: Path) -> list[str]:
    """Return all external file references (images + buffers) from a GLTF."""
    import json as _json
    try:
        g = _json.loads(gltf_path.read_text())
    except Exception:
        return []
    refs: list[str] = []
    for img in g.get("images", []):
        uri = img.get("uri", "")
        if uri and not uri.startswith("data:"):
            refs.append(uri)
    for buf in g.get("buffers", []):
        uri = buf.get("uri", "")
        if uri and not uri.startswith("data:"):
            refs.append(uri)
    return refs


def download_asset(asset_id: str, label: str, res: str = "2k") -> Path | None:
    """Download the GLTF + all its textures for one asset. Returns dest dir."""
    dest_dir = ASSETS_DIR / asset_id

    # Resolve which resolution we'll use.
    gltf_path = dest_dir / f"{asset_id}_{res}.gltf"
    gltf_url = PH_GLTF.format(res=res, id=asset_id)

    # Check if already fully downloaded (GLTF + at least one texture).
    if gltf_path.exists() and any(dest_dir.glob("textures/*")):
        print(f"  [ok] {asset_id} already downloaded")
        return dest_dir

    print(f"  --> {asset_id} ({label}) …", flush=True)

    # Download GLTF.
    ok = _download(gltf_url, gltf_path)
    if not ok:
        fallback_url = PH_GLTF.format(res="1k", id=asset_id)
        ok = _download(fallback_url, dest_dir / f"{asset_id}_1k.gltf")
        if not ok:
            print(f"  [skip] {asset_id}: GLTF not available at {res} or 1k")
            return None
        res = "1k"
        gltf_path = dest_dir / f"{asset_id}_1k.gltf"

    # Parse GLTF to find all external dependencies (textures + .bin buffer).
    base_url = PH_BASE.format(res=res, id=asset_id)
    for dep in _deps_from_gltf(gltf_path):
        dep_url = base_url + dep
        _download(dep_url, dest_dir / dep)

    print(f"  [done] {asset_id} → {dest_dir.relative_to(HERE)}")
    return dest_dir


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--res", default="2k", choices=["1k", "2k", "4k"],
                    help="Texture resolution (default: 2k)")
    ap.add_argument("--jobs", type=int, default=4,
                    help="Parallel download threads")
    args = ap.parse_args()

    ASSETS_DIR.mkdir(parents=True, exist_ok=True)
    print(f"Downloading {len(ASSETS)} PolyHaven assets at {args.res} → {ASSETS_DIR}\n")

    results: dict[str, Path | None] = {}
    lock = threading.Lock()
    sem = threading.Semaphore(args.jobs)

    def _worker(asset_id, label):
        with sem:
            r = download_asset(asset_id, label, args.res)
        with lock:
            results[asset_id] = r

    threads = [threading.Thread(target=_worker, args=(aid, lbl), daemon=True)
               for aid, lbl, _ in ASSETS]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    ok = [k for k, v in results.items() if v is not None]
    fail = [k for k, v in results.items() if v is None]
    print(f"\n{len(ok)}/{len(ASSETS)} assets ready.")
    if fail:
        print("Failed:", ", ".join(fail))

    # Write a manifest Godot can read at runtime.
    manifest_path = ASSETS_DIR / "manifest.json"
    manifest = {}
    for asset_id, label, notes in ASSETS:
        d = results.get(asset_id)
        if d:
            glbs = list(d.glob("*.gltf"))
            if glbs:
                # Path relative to the godot/ project root.
                rel = glbs[0].relative_to(HERE)
                manifest[label] = "res://" + str(rel).replace("\\", "/")
    manifest_path.write_text(json.dumps(manifest, indent=2))
    print(f"Manifest written → {manifest_path.relative_to(HERE)}")


if __name__ == "__main__":
    main()
