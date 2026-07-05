# On-device models

CoreML model artifacts and conversion scripts for `RoverOperator`'s on-device `Detector`.
Like `eco/drone/models/`, model binaries are **not committed to git** (see `.gitignore`)
— generate them locally with the script below.

## RoverYOLO — person/obstacle detector (working today)

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install ultralytics coremltools
python3 convert_yolo_coreml.py
```

Produces `RoverYOLO.mlpackage` (~12 MB): a generic **COCO-pretrained YOLOv8n**, exported
directly to CoreML with NMS baked in. Verified with a real prediction run (not just "the
file exists") — it correctly detected 4 people (class 0) and 1 bus (class 5) on a sample
street photo.

**This is NOT fine-tuned on airport-ramp imagery.** There is no ramp/tarmac dataset in
this repo — `eco/drone/models/` only has the learned depth→velocity flight-control policy
nets, not an object detector, so there was nothing eco-specific to build on. The COCO
"person" class (id 0) is what actually matters for people-avoidance today, and that works
out of the box. Ramp-specific classes (baggage carts/tugs, jet bridges, aircraft, ground
service equipment) are future work that needs a labeled dataset first — see "Synthetic
data" below for how to bootstrap one without hand-labeling real ramp photos.

### Synthetic data for ramp-class fine-tuning — `synthetic/` (built, proof-of-concept)

**Cut-paste compositing is built and verified** — see `synthetic/README.md` for the full
pipeline, sources, and honest limitations. Short version: real cutouts (a tug, baggage
carts, suitcases — sourced from Wikimedia Commons, only one photo is actually of HHR)
composited onto the one real ground-level HHR photo found, with randomized
position/scale/rotation. Fine-tuned YOLOv8n for 15 epochs (val mAP50 ≈ 0.95, though that
number reflects the synthetic distribution, not real-world generalization — see the
limitations section), verified with real inference on a held-out image, exported to
CoreML. **Not wired into the app** — treat as a validated pipeline, not a shippable model;
the biggest next step is real photos taken at HHR itself.

Two other options remain open, roughly in order of effort beyond compositing:
1. **Isaac Sim procedural rendering** — eco already has an Isaac Sim pipeline (see the L5
   sim autonomy work); extending it with airport/ramp/cart 3D assets gives higher-fidelity
   renders with perfect ground truth, at the cost of needing those assets and running on
   the sim host (hoopoe), not locally.
2. **Auto-labeling real photos** with an open-vocabulary detector (e.g. Grounding DINO) to
   pseudo-label real ramp stock photos for ramp-specific classes — best realism, needs a
   source of real images and a licensing check.

Add `RoverYOLO.mlpackage` to the `RoverOperator` Xcode target (Xcode auto-compiles it to
`RoverYOLO.mlmodelc` at build time) — `Detector.swift` already loads it by that name.

## Other candidates (not yet built)

- **VLM (optional, iPad Pro)** — small vision-language model via **MLX** for on-device
  scene Q&A; in the hybrid split the heavy VLM stays cloud-side (Claude) by default.
- **Speech** — using Apple's on-device `SFSpeechRecognizer` directly (see `Voice/SpeechIn.swift`);
  no separate model artifact needed.
