"""Convert a pretrained YOLOv8n to CoreML for PhroverKit's on-device Detector.

Usage:
    python3 -m venv .venv && source .venv/bin/activate
    pip install ultralytics coremltools
    python3 convert_yolo_coreml.py

Produces RoverYOLO.mlpackage in this directory. This is the generic COCO-pretrained
YOLOv8n (person, and 79 other everyday object classes) exported directly to CoreML with
NMS baked in — NOT fine-tuned on airport-ramp imagery. There is no ramp-scene dataset
in this repo yet; eco/drone/models/ only has the depth->velocity flight-control policy
nets, not a detector. Fine-tuning on ramp classes (baggage carts/tugs, jet bridges,
aircraft, ground service equipment) is future work that needs a labeled dataset first —
see eco/rover/models/README.md "Synthetic data" for how to bootstrap one.

The "person" class (id 0) is what actually matters for obstacle/person avoidance today,
and that works out of the box with the generic COCO weights.

NOTE ON LICENSING: this script imports `ultralytics`, which is AGPL-3.0. It is a
build-time tool only — it downloads .pt weights and exports them to ONNX. Nothing
in the runtime perception path imports it, and it is not installed on the vehicle
by the platform installers. Install it yourself on a build machine if you need to
regenerate weights, or obtain equivalent .onnx files another way.
"""
from pathlib import Path

from ultralytics import YOLO

OUT_NAME = "RoverYOLO"


def main() -> None:
    model = YOLO("yolov8n.pt")  # auto-downloads pretrained COCO weights on first run
    exported = model.export(format="coreml", nms=True, int8=False)

    exported_path = Path(exported)
    dest = Path(__file__).parent / f"{OUT_NAME}.mlpackage"
    if dest.exists():
        import shutil
        shutil.rmtree(dest)
    exported_path.rename(dest)
    print(f"Wrote {dest}")


if __name__ == "__main__":
    main()
