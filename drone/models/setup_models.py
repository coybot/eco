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
            args.qwen3_vl_2b,
            args.qwen3_vl_8b,
            args.qwen3_vl_32b,
            args.setup_device,
        ]
    ):
        parser.print_help()


if __name__ == "__main__":
    main()
