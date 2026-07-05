# Synthetic ramp data (HHR) — cut-paste compositing pipeline

Builds a YOLO fine-tuning dataset for the two ramp-specific classes generic COCO doesn't
have — **baggage_cart** and **tug** — by compositing real cutout objects onto a real
photo of Hawthorne Municipal Airport (HHR). `person` and `suitcase` are already COCO
classes and detect fine out of the box (see `../README.md`), so they aren't the target
here; a few suitcase cutouts are included only for scene clutter/realism.

## Pipeline

```bash
cd eco/rover/models
source .venv/bin/activate   # pip install ultralytics coremltools rembg onnxruntime
python3 synthetic/extract_cutouts.py    # crop + rembg-segment objects -> cutouts/*.png
python3 synthetic/generate_dataset.py   # composite -> dataset/ (YOLO format, 150 train / 30 val)

# Fine-tune (smoke-tested with 15 epochs, ~4 min on an M-series Mac CPU):
python3 -c "from ultralytics import YOLO; YOLO('yolov8n.pt').train(
    data='synthetic/dataset/data.yaml', epochs=15, imgsz=640, batch=8,
    project='synthetic/runs', name='ramp_finetune', exist_ok=True)"
```

## Real image sources (HHR + ramp equipment)

Fetched from Wikimedia Commons via `Special:FilePath` (stable direct-download redirect).
Only **one** photo is actually of HHR — see limitations below.

| File | Used for | License | Author |
|---|---|---|---|
| `Hawthorne_Municipal_Airport_-_Los_Angeles-01.jpg` | background (the only real HHR ground-level photo found) | Public domain (PD-self) | Priwo |
| `Menzies Aviation aircraft tug.jpg` | tug + baggage cart cutouts (generic ramp, not HHR) | CC BY-SA 4.0 | Oleg Yunakov |
| `Luggage awaiting loading at airport IMG 3140.JPG` | suitcase cutouts (generic, not HHR) | CC BY 3.0 | Billy Hathorn |

**License note:** the tug/cart source is CC BY-SA 4.0 (ShareAlike) — if this dataset,
the cutouts, or a model trained on them is ever published/open-sourced outside eco,
attribution + ShareAlike terms apply. Fine for internal model training as-is; revisit
before any external release.

## What was verified (not just generated)

- Viewed every downloaded source image directly to confirm it actually shows what its
  filename claimed before using it (the other two HHR Wikipedia images — a 1994 USGS
  aerial and a 1972 aerial — were low-res/grayscale top-down shots, useless for a
  ground-level detector, and correctly excluded).
- Viewed `rembg` cutout output directly — clean alpha-matted extraction, not a guess.
- Viewed rendered composites with bounding boxes overlaid — confirmed labels tightly
  track the pasted objects. Caught and fixed a real bug this way: far-placed objects
  were shrinking to a few pixels (near-invisible, useless training signal) — added a
  20px minimum box-size filter and re-generated.
- Ran an actual fine-tune (15 epochs, CPU, ~4 min): **val mAP50 ≈ 0.95–0.97**.
- Ran real inference on a held-out validation image afterward: correctly detected
  `tug: 0.94`.
- Exported the fine-tuned weights to CoreML (`runs/ramp_finetune/weights/best.mlpackage`,
  11.7 MB) — the same export path already proven for the base model.

## Honest limitations — this is a proof of the pipeline, not a production model

- **One real HHR photo.** Background "diversity" (6 variants) comes from cropping/flipping
  that single photograph, not genuinely different scenes. A production dataset needs
  photos from multiple angles, times of day, and weather at HHR itself.
- **Neither the tug nor the suitcases are HHR-specific** — sourced from other airports.
  Real HHR ramp equipment will look different; swap in real photos as they become
  available (same `extract_cutouts.py` pipeline, new crop boxes).
- **The 0.95+ mAP is not a real generalization measure.** Train and val composites are
  drawn from the *same* single background and the *same* small cutout library — the
  model is being asked to recognize objects it has effectively already seen, just in new
  positions. This proves the pipeline mechanics work end-to-end; it says nothing about
  accuracy on a real photo the model has never encountered.
- **Perspective is a 2D approximation**, not real 3D geometry — a hand-picked ground-plane
  trapezoid + linear scale-by-depth heuristic, not calibrated camera geometry.
- **Not wired into the app.** `best.mlpackage` here is a separate artifact from
  `../RoverYOLO.mlpackage` — nobody should assume this is safe to ship without a real,
  held-out test set from different photos first.

## Next steps toward something deployable

1. Collect real photos actually taken at the ramp at HHR (multiple angles, lighting,
   weather) — this is the single highest-leverage fix for every limitation above.
2. Add a held-out test set from genuinely different source photos (not just a different
   random split of the same compositing distribution) to get a real accuracy signal.
3. Add negative (no-object) backgrounds so the model also learns what *isn't* a cart/tug.
4. Consider the Isaac Sim route (see `../README.md`) once/if 3D asset investment makes
   sense — it removes the "2D approximation" and "single background" limitations at once.
