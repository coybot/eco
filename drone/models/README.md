# Drone mission autonomy – models

Models are **not** committed to git. They are downloaded by `setup_models.py` (run by the Orin install script or manually).

## Filenames (same on all devices)

| File | Purpose |
|------|--------|
| `yolov8n.onnx` | Object detection (YOLOv8n). Shared by Nano, NX, and AGX. |
| `vlm.gguf` | Vision-language model (Qwen3-VL). **Content varies by variant.** |
| `vlm_mmproj.gguf` | Multimodal projector for the VLM. Pairs with `vlm.gguf`. |

So **Nano and AGX use the same filenames**; the actual VLM file is different (smaller on Nano, larger on AGX).

## Variant → models

| Variant | YOLO | VLM |
|--------|------|-----|
| **Orin Nano** | YOLOv8n (ONNX) | Qwen3-VL-2B Q4_K_M (~1.1GB + mmproj) |
| **Orin NX** | YOLOv8n (ONNX) | Qwen3-VL-8B Q4_K_M (~5GB + mmproj) |
| **AGX 32GB** | YOLOv8n or YOLOv8x (ONNX) | Qwen3-VL-8B Q4_K_M |
| **AGX 64GB** | YOLOv8x (ONNX) | Qwen3-VL-30B Q4_K_M (~18.6GB + mmproj) |

`--qwen3-vl-2b` = Qwen3-VL 2B for Nano.  
`--qwen3-vl-8b` = Qwen3-VL 8B for NX/AGX32.  
`--qwen3-vl-32b` = Qwen3-VL 30B for AGX64.

## Setup (manual)

From this directory (`drone/models/` or `~/drone-api/models/` after install):

```bash
pip install huggingface_hub ultralytics
python setup_models.py --yolo-only
python setup_models.py --qwen3-vl-2b   # Nano (Qwen3-VL 2B)
# or --qwen3-vl-8b (NX/AGX32), --qwen3-vl-32b (AGX64, 30B)
python setup_models.py --setup-device --variant nano  # or nx, agx32, agx64
```
