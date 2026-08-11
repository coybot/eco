#!/usr/bin/env python3
"""
Download and set up AI models for mission autonomy (Orin Nano, NX, AGX).

Usage:
  python setup_models.py --yolo-only          # YOLOv8n ONNX (all variants)
  python setup_models.py --yolox              # YOLOv8x ONNX (AGX only)
  python setup_models.py --qwen3-vl-2b        # Qwen3-VL 2B for Nano
  python setup_models.py --qwen3-vl-8b       # Qwen3-VL 8B for NX / AGX 32GB
  python setup_models.py --qwen3-vl-32b      # Qwen3-VL 30B for AGX 64GB
  python setup_models.py --setup-device --variant <nano|nx|agx32|agx64>

All VLM outputs are written as vlm.gguf and vlm_mmproj.gguf in the current directory.

NOTE ON LICENSING: this script imports `ultralytics`, which is AGPL-3.0. It is a
build-time tool only — it downloads .pt weights and exports them to ONNX. Nothing
in the runtime perception path imports it, and it is not installed on the vehicle
by the platform installers. Install it yourself on a build machine if you need to
regenerate weights, or obtain equivalent .onnx files another way.
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path


def get_models_dir() -> Path:
    """Directory for model files (current dir when run from install)."""
    return Path(os.getcwd()).resolve()


def run(cmd: list[str], check: bool = True) -> subprocess.CompletedProcess:
    return subprocess.run(cmd, check=check)


def download_hf_file(repo_id: str, filename: str, dest: Path) -> None:
    """Download a single file from Hugging Face Hub."""
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("Install huggingface_hub: pip install huggingface_hub")
        sys.exit(1)
    print(f"Downloading {repo_id}/{filename} ...")
    path = hf_hub_download(repo_id=repo_id, filename=filename, local_dir=str(dest.parent), local_dir_use_symlinks=False)
    # hf_hub_download returns path to file; we may need to move to dest if names differ
    path = Path(path)
    if path.resolve() != dest.resolve():
        shutil.copy2(path, dest)
        path.unlink(missing_ok=True)


def yolo_only(models_dir: Path) -> None:
    """Download YOLOv8n and export to ONNX (shared by Nano, NX, AGX)."""
    try:
        from ultralytics import YOLO
    except ImportError:
        print("Install ultralytics: pip install ultralytics")
        sys.exit(1)
    out_onnx = models_dir / "yolov8n.onnx"
    if out_onnx.exists():
        print("yolov8n.onnx already exists, skipping.")
        return
    print("Downloading YOLOv8n and exporting to ONNX...")
    model = YOLO("yolov8n.pt")
    model.export(format="onnx", imgsz=640, simplify=True)
    # Export writes to cwd as yolov8n.onnx
    cwd_onnx = Path("yolov8n.onnx")
    if cwd_onnx.exists():
        shutil.move(str(cwd_onnx), str(out_onnx))
    print(f"Saved {out_onnx}")


def yolox(models_dir: Path) -> None:
    """Download YOLOv8x and export to ONNX (AGX)."""
    try:
        from ultralytics import YOLO
    except ImportError:
        print("Install ultralytics: pip install ultralytics")
        sys.exit(1)
    out_onnx = models_dir / "yolov8x.onnx"
    if out_onnx.exists():
        print("yolov8x.onnx already exists, skipping.")
        return
    print("Downloading YOLOv8x and exporting to ONNX...")
    model = YOLO("yolov8x.pt")
    model.export(format="onnx", imgsz=640, simplify=True)
    cwd_onnx = Path("yolov8x.onnx")
    if cwd_onnx.exists():
        shutil.move(str(cwd_onnx), str(out_onnx))
    print(f"Saved {out_onnx}")


# Official Qwen3-VL GGUF repos (Hugging Face)
VLM_2B_REPO = "Qwen/Qwen3-VL-2B-Instruct-GGUF"
VLM_8B_REPO = "Qwen/Qwen3-VL-8B-Instruct-GGUF"
VLM_30B_REPO = "Qwen/Qwen3-VL-30B-A3B-Instruct-GGUF"


def qwen3_vl_2b(models_dir: Path) -> None:
    """Qwen3-VL 2B for Nano (~1.1GB + 445MB mmproj)."""
    vlm_gguf = models_dir / "vlm.gguf"
    mmproj_gguf = models_dir / "vlm_mmproj.gguf"
    if vlm_gguf.exists() and mmproj_gguf.exists():
        print("vlm.gguf and vlm_mmproj.gguf already exist, skipping.")
        return
    download_hf_file(VLM_2B_REPO, "Qwen3VL-2B-Instruct-Q4_K_M.gguf", vlm_gguf)
    download_hf_file(VLM_2B_REPO, "mmproj-Qwen3VL-2B-Instruct-Q8_0.gguf", mmproj_gguf)
    print("Qwen3-VL 2B ready: vlm.gguf, vlm_mmproj.gguf")


def qwen3_vl_8b(models_dir: Path) -> None:
    """Qwen3-VL 8B for NX / AGX 32GB (~5GB + 752MB mmproj)."""
    vlm_gguf = models_dir / "vlm.gguf"
    mmproj_gguf = models_dir / "vlm_mmproj.gguf"
    if vlm_gguf.exists() and mmproj_gguf.exists():
        print("vlm.gguf and vlm_mmproj.gguf already exist, skipping.")
        return
    download_hf_file(VLM_8B_REPO, "Qwen3VL-8B-Instruct-Q4_K_M.gguf", vlm_gguf)
    download_hf_file(VLM_8B_REPO, "mmproj-Qwen3VL-8B-Instruct-Q8_0.gguf", mmproj_gguf)
    print("Qwen3-VL 8B ready: vlm.gguf, vlm_mmproj.gguf")


def qwen3_vl_32b(models_dir: Path) -> None:
    """Qwen3-VL 30B for AGX 64GB (~18.6GB + 712MB mmproj)."""
    vlm_gguf = models_dir / "vlm.gguf"
    mmproj_gguf = models_dir / "vlm_mmproj.gguf"
    if vlm_gguf.exists() and mmproj_gguf.exists():
        print("vlm.gguf and vlm_mmproj.gguf already exist, skipping.")
        return
    download_hf_file(VLM_30B_REPO, "Qwen3VL-30B-A3B-Instruct-Q4_K_M.gguf", vlm_gguf)
    download_hf_file(VLM_30B_REPO, "mmproj-Qwen3VL-30B-A3B-Instruct-Q8_0.gguf", mmproj_gguf)
    print("Qwen3-VL 30B ready: vlm.gguf, vlm_mmproj.gguf")


# These four point at Presidio's own private S3 bucket - proprietary fine-tuned
# weights (not on the shipped install path; only reachable via the opt-in
# --domain-detector/--vlm-drone/--reactive-policy/--depth-model flags, never
# called by drone/platforms/orin/install.sh's default variant-based flow).
# Point these at your own bucket if you have equivalent weights to serve.
DOMAIN_DETECTOR_URL = "https://YOUR_MODELS_BUCKET.s3.YOUR_AWS_REGION.amazonaws.com/models/yolov8n_domain_v3.onnx"

POLICY_BASE_URL = "https://YOUR_MODELS_BUCKET.s3.YOUR_AWS_REGION.amazonaws.com/models"
DEPTH_URL = "https://YOUR_MODELS_BUCKET.s3.YOUR_AWS_REGION.amazonaws.com/models/depth_v1.onnx"
VLM_DRONE_BASE_URL = "https://YOUR_MODELS_BUCKET.s3.YOUR_AWS_REGION.amazonaws.com/models"


def domain_detector(models_dir: Path) -> None:
    """Presidio domain-trained YOLOv8n (9 classes: person, drone, vehicle, ...).

    Trained on 18 k sim frames (office/warehouse/hospital) plus public aerial sets.
    Stored in the dev S3 bucket after training on hoopoe. Falls back to curl if requests
    is unavailable.
    """
    out = models_dir / "yolov8n_domain_v1.onnx"
    if out.exists():
        print("yolov8n_domain_v1.onnx already exists, skipping.")
        return
    print("Downloading domain detector v1 ...")
    try:
        import requests
        r = requests.get(DOMAIN_DETECTOR_URL, stream=True, timeout=60)
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    except Exception:
        run(["curl", "-fL", DOMAIN_DETECTOR_URL, "-o", str(out)])
    print(f"Saved {out}")


def vlm_drone(models_dir: Path) -> None:
    """Presidio drone-action VLM v1 — Qwen2.5-VL-3B LoRA fine-tuned on aerial missions.

    Q4_K_M GGUF (~1.8 GB) + mmproj (~1.2 GB). Downloads as vlm.gguf + vlm_mmproj.gguf
    so daemon.py picks them up automatically (same slot as the stock Qwen3-VL-2B).
    Trained on 2,000 VisDrone aerial scenes with deterministic VLMAction labels;
    3 epochs, final loss 0.31, 94.4% token accuracy.
    """
    vlm_gguf    = models_dir / "vlm.gguf"
    mmproj_gguf = models_dir / "vlm_mmproj.gguf"
    if vlm_gguf.exists() and mmproj_gguf.exists():
        print("vlm.gguf and vlm_mmproj.gguf already exist, skipping.")
        return
    for fname, dest in [
        ("vlm_lora_v1_q4km.gguf",    vlm_gguf),
        ("vlm_lora_v1_mmproj.gguf",  mmproj_gguf),
    ]:
        url = f"{VLM_DRONE_BASE_URL}/{fname}"
        print(f"Downloading {fname} → {dest.name} ...")
        try:
            import requests
            r = requests.get(url, stream=True, timeout=120)
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=4 << 20):
                    f.write(chunk)
        except Exception:
            run(["curl", "-fL", url, "-o", str(dest)])
        print(f"  Saved {dest} ({dest.stat().st_size // (1 << 20)} MB)")
    print("Drone VLM v1 ready: vlm.gguf + vlm_mmproj.gguf")


def reactive_policy(models_dir: Path) -> None:
    """Presidio reactive policy MLP v1 (~120 KB total: ONNX + external data + state norm).

    Trained via behavioral cloning of the reactive_planner rule-set on 200k synthetic
    state→velocity-command pairs. Input: 12-dim state, output: [vx, vy, vz] m/s.
    Requires policy_v1.onnx, policy_v1.onnx.data, and policy_v1_state_norm.npy co-located.
    """
    onnx_file = models_dir / "policy_v1.onnx"
    if onnx_file.exists():
        print("policy_v1.onnx already exists, skipping.")
        return
    print("Downloading reactive policy v1 ...")
    for fname in ("policy_v1.onnx", "policy_v1.onnx.data", "policy_v1_state_norm.npy"):
        url = f"{POLICY_BASE_URL}/{fname}"
        dest = models_dir / fname
        try:
            import requests
            r = requests.get(url, stream=True, timeout=60)
            r.raise_for_status()
            with open(dest, "wb") as f:
                for chunk in r.iter_content(chunk_size=1 << 20):
                    f.write(chunk)
        except Exception:
            run(["curl", "-fL", url, "-o", str(dest)])
        print(f"  Saved {dest}")
    print("Reactive policy v1 ready.")


NAV_POLICIES_HF_REPO = "astralhf/eco-drone-policies"
NAV_POLICY_FILES = {
    "quadcopter": ["policy_v26rnn_dr.onnx"],
    "rover": ["policy_rover_v2.onnx", "policy_rover_v2.onnx.data"],
    "fixedwing": ["policy_fw.onnx"],
}


def nav_policies(models_dir: Path, vehicles: list[str] | None = None) -> None:
    """L5 learned-planner ONNX policies (quadcopter / rover / fixed-wing), hosted on the
    Hugging Face Hub at astralhf/eco-drone-policies. Filenames match each vehicle class's
    default `policy_onnx` in vehicle_class.py, so no renaming is needed after download.

    These are optional -- the platform's benchmarked L5 fleet controller
    (drone/common/L5.md) is classical control with no model weights at all. This is only
    needed for the separate LearnedPlanner path (e.g. render_fixedwing_demo.py).
    """
    vehicles = vehicles or list(NAV_POLICY_FILES)
    fnames = [f for v in vehicles for f in NAV_POLICY_FILES[v]]
    try:
        from huggingface_hub import hf_hub_download
    except ImportError:
        print("Install huggingface_hub: pip install huggingface_hub")
        sys.exit(1)
    for fname in fnames:
        dest = models_dir / fname
        if dest.exists():
            print(f"{fname} already exists, skipping.")
            continue
        print(f"Downloading {fname} from {NAV_POLICIES_HF_REPO} ...")
        path = Path(hf_hub_download(repo_id=NAV_POLICIES_HF_REPO, filename=fname,
                                     local_dir=str(models_dir)))
        if path.resolve() != dest.resolve():
            shutil.copy2(path, dest)
        print(f"  Saved {dest}")
    print("Nav policies ready.")


def depth_model(models_dir: Path) -> None:
    """Depth Anything V2 Small fine-tuned on aerial domain (~1.6 MB ONNX).

    Fine-tuned on VisDrone val pseudo-depth labels (500 images, 10 epochs).
    Input: 3×518×518 normalised RGB. Output: 518×518 relative depth map (float32).
    Integrate in perception.py as an alternate DistanceEstimator backend
    when stereo depth is unavailable (fixed-wing / long-range).
    """
    out = models_dir / "depth_v1.onnx"
    if out.exists():
        print("depth_v1.onnx already exists, skipping.")
        return
    print("Downloading depth model v1 ...")
    try:
        import requests
        r = requests.get(DEPTH_URL, stream=True, timeout=60)
        r.raise_for_status()
        with open(out, "wb") as f:
            for chunk in r.iter_content(chunk_size=1 << 20):
                f.write(chunk)
    except Exception:
        run(["curl", "-fL", DEPTH_URL, "-o", str(out)])
    print(f"Saved {out}")


def setup_device(variant: str) -> None:
    """Create device symlinks. Single-device install: files already named vlm.gguf / vlm_mmproj.gguf."""
    models_dir = get_models_dir()
    vlm = models_dir / "vlm.gguf"
    mmproj = models_dir / "vlm_mmproj.gguf"
    if not vlm.exists():
        print(f"Warning: {vlm} not found. Run the appropriate --qwen3-vl-* first.")
    if not mmproj.exists():
        print(f"Warning: {mmproj} not found. Run the appropriate --qwen3-vl-* first.")
    print(f"Setup complete for variant: {variant}")


def detect_orin_variant() -> str:
    """Detect Orin variant (nano, nx, agx32, agx64)."""
    try:
        with open("/proc/device-tree/model") as f:
            model = f.read().lower()
        if "orin nano" in model:
            return "nano"
        if "orin nx" in model:
            return "nx"
        if "agx orin" in model or "orin agx" in model:
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemTotal:"):
                        mem_kb = int(line.split()[1])
                        mem_gb = mem_kb // (1024 * 1024)
                        return "agx64" if mem_gb >= 50 else "agx32"
                        break
    except FileNotFoundError:
        pass
    # Fallback by memory
    try:
        with open("/proc/meminfo") as f:
            for line in f:
                if line.startswith("MemTotal:"):
                    mem_kb = int(line.split()[1])
                    mem_gb = mem_kb // (1024 * 1024)
                    if mem_gb < 12:
                        return "nano"
                    if mem_gb < 20:
                        return "nx"
                    if mem_gb < 50:
                        return "agx32"
                    return "agx64"
    except FileNotFoundError:
        pass
    return "unknown"


def main() -> None:
    parser = argparse.ArgumentParser(description="Setup AI models for drone mission autonomy")
    parser.add_argument("--yolo-only", action="store_true", help="Download YOLOv8n and export to ONNX")
    parser.add_argument("--yolox", action="store_true", help="Download YOLOv8x and export to ONNX (AGX)")
    parser.add_argument("--domain-detector", action="store_true", help="Download presidio domain detector v1 (drone/vehicle/person ONNX)")
    parser.add_argument("--vlm-drone", action="store_true", help="Download drone-action VLM v1 (Qwen2.5-VL-3B LoRA Q4_K_M GGUF, ~1.8+1.2 GB)")
    parser.add_argument("--reactive-policy", action="store_true", help="Download reactive policy MLP v1 (12-dim state → velocity cmd ONNX)")
    parser.add_argument("--nav-policies", type=str, nargs="*", metavar="VEHICLE",
                        help="Download L5 learned-planner ONNX policies from Hugging Face "
                             "(astralhf/eco-drone-policies). No args = all vehicles, or "
                             "specify: quadcopter rover fixedwing")
    parser.add_argument("--depth-model", action="store_true", help="Download depth model v1 (Depth Anything V2 Small, aerial fine-tuned ONNX)")
    parser.add_argument("--qwen3-vl-2b", action="store_true", help="Small VLM for Nano (7B Q4_K_M)")
    parser.add_argument("--qwen3-vl-8b", action="store_true", help="7B VLM for NX / AGX 32GB")
    parser.add_argument("--qwen3-vl-32b", action="store_true", help="32B VLM for AGX 64GB")
    parser.add_argument("--setup-device", action="store_true", help="Create device symlinks")
    parser.add_argument("--variant", type=str, choices=("nano", "nx", "agx32", "agx64"), help="Orin variant for --setup-device")
    args = parser.parse_args()

    models_dir = get_models_dir()
    models_dir.mkdir(parents=True, exist_ok=True)

    if args.yolo_only:
        yolo_only(models_dir)
    if args.yolox:
        yolox(models_dir)
    if args.domain_detector:
        domain_detector(models_dir)
    if args.vlm_drone:
        vlm_drone(models_dir)
    if args.reactive_policy:
        reactive_policy(models_dir)
    if args.nav_policies is not None:
        nav_policies(models_dir, args.nav_policies or None)
    if args.depth_model:
        depth_model(models_dir)
    if args.qwen3_vl_2b:
        qwen3_vl_2b(models_dir)
    if args.qwen3_vl_8b:
        qwen3_vl_8b(models_dir)
    if args.qwen3_vl_32b:
        qwen3_vl_32b(models_dir)
    if args.setup_device:
        variant = args.variant or detect_orin_variant()
        setup_device(variant)

    if not any(
        [
            args.yolo_only,
            args.yolox,
            args.domain_detector,
            args.vlm_drone,
            args.reactive_policy,
            args.nav_policies is not None,
            args.depth_model,
            args.qwen3_vl_2b,
            args.qwen3_vl_8b,
            args.qwen3_vl_32b,
            args.setup_device,
        ]
    ):
        parser.print_help()


if __name__ == "__main__":
    main()
