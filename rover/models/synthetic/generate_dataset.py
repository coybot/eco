"""Step 2: composite cutouts onto the real airport ramp background to build a YOLO-format
fine-tuning dataset for ramp-specific classes (baggage_cart, tug) that generic COCO
doesn't have. `suitcase`/`person` are already COCO classes (id 28 / 0) and detect fine
out of the box — see eco/rover/models/README.md — so they aren't the fine-tuning target
here, but a couple of suitcase cutouts are included anyway for scene realism (clutter
near a cart looks more like a real ramp than an empty cart alone).

Run after extract_cutouts.py:
    python3 generate_dataset.py
"""
import random
from pathlib import Path

from PIL import Image, ImageEnhance, ImageFilter

ROOT = Path(__file__).parent
BG_DIR = ROOT / "bg_source"
# The background directory holds exactly one ground-level ramp photo (see README.md);
# pick it by extension rather than hardcoding a filename.
BG_SRC = next(
    (p for p in sorted(BG_DIR.glob("*")) if p.suffix.lower() in {".jpg", ".jpeg", ".png"}),
    None,
)
CUTOUTS = ROOT / "cutouts"
OUT = ROOT / "dataset"

CLASSES = ["baggage_cart", "tug", "suitcase"]

# Approximate real-world width (m) per class — used only to keep relative on-screen
# sizes plausible (tug > cart > suitcase); not a camera calibration.
REAL_WIDTH_M = {"baggage_cart": 1.2, "tug": 1.8, "suitcase": 0.55}
PPM_FAR, PPM_NEAR = 40.0, 130.0  # pixels-per-meter at the far/near end of the ground plane

# Ground-plane trapezoid in the ONE real background photo (2816x2112), picked by eye:
# far edge is near the horizon/hangar line, near edge is the bottom of the frame.
FAR_Y, NEAR_Y = 900, 2000
FAR_X_RANGE, NEAR_X_RANGE = (300, 1900), (-100, 2900)

N_TRAIN, N_VAL = 150, 30


def load_cutouts():
    manifest = (CUTOUTS / "manifest.txt").read_text().strip().splitlines()
    items = []
    for line in manifest:
        name, cls = line.split("\t")
        items.append((Image.open(CUTOUTS / name).convert("RGBA"), cls))
    return items


def lerp(a, b, t):
    return a + (b - a) * t


def place_object(bg: Image.Image, cutout: Image.Image, cls: str):
    """Paste one randomized instance of `cutout` onto `bg`; return YOLO bbox or None
    if it ended up fully off-frame."""
    t = random.random()  # 0 = far, 1 = near
    y_anchor = lerp(FAR_Y, NEAR_Y, t) + random.uniform(-40, 40)
    x_lo = lerp(FAR_X_RANGE[0], NEAR_X_RANGE[0], t)
    x_hi = lerp(FAR_X_RANGE[1], NEAR_X_RANGE[1], t)
    x_anchor = random.uniform(x_lo, x_hi)

    ppm = lerp(PPM_FAR, PPM_NEAR, t)
    target_w = REAL_WIDTH_M[cls] * ppm * random.uniform(0.85, 1.15)
    scale = target_w / cutout.width
    new_size = (max(1, int(cutout.width * scale)), max(1, int(cutout.height * scale)))
    obj = cutout.resize(new_size, Image.LANCZOS)

    if random.random() < 0.5:
        obj = obj.transpose(Image.FLIP_LEFT_RIGHT)
    angle = random.uniform(-6, 6)
    obj = obj.rotate(angle, expand=True, resample=Image.BICUBIC)

    # Light brightness jitter so pasted objects don't all match one fixed exposure.
    enhancer = ImageEnhance.Brightness(obj)
    obj = enhancer.enhance(random.uniform(0.85, 1.15))
    # Feather the alpha edge slightly to soften the rembg cutout boundary.
    r, g, b, a = obj.split()
    a = a.filter(ImageFilter.GaussianBlur(1.0))
    obj = Image.merge("RGBA", (r, g, b, a))

    # Anchor = bottom-center of the object (its ground-contact point).
    paste_x = int(x_anchor - obj.width / 2)
    paste_y = int(y_anchor - obj.height)

    bg.paste(obj, (paste_x, paste_y), obj)

    # Bounding box from the alpha mask, clipped to the canvas.
    x0, y0 = paste_x, paste_y
    x1, y1 = paste_x + obj.width, paste_y + obj.height
    cx0, cy0 = max(0, x0), max(0, y0)
    cx1, cy1 = min(bg.width, x1), min(bg.height, y1)
    if cx1 <= cx0 or cy1 <= cy0:
        return None
    # Require most of the object to still be on-frame.
    if (cx1 - cx0) * (cy1 - cy0) < 0.5 * obj.width * obj.height:
        return None
    # Discard near-invisible far-away instances — a handful of pixels is noise, not
    # useful training signal (and was actually happening at the far end of the range).
    if (cx1 - cx0) < 20 or (cy1 - cy0) < 20:
        return None
    return (cx0, cy0, cx1, cy1)


def to_yolo_line(class_id: int, box, img_w: int, img_h: int) -> str:
    x0, y0, x1, y1 = box
    cx = (x0 + x1) / 2 / img_w
    cy = (y0 + y1) / 2 / img_h
    w = (x1 - x0) / img_w
    h = (y1 - y0) / img_h
    return f"{class_id} {cx:.6f} {cy:.6f} {w:.6f} {h:.6f}"


def make_background_variants(n: int):
    """Cheap diversity from the single real source photo: a few different crop
    windows/flips. Still fundamentally one photograph — see README caveats."""
    if BG_SRC is None:
        raise SystemExit(f"No background image found in {BG_DIR} — see README.md")
    base = Image.open(BG_SRC).convert("RGB")
    variants = []
    w, h = base.size
    for i in range(n):
        crop_w, crop_h = int(w * random.uniform(0.75, 1.0)), int(h * random.uniform(0.75, 1.0))
        x0 = random.randint(0, w - crop_w)
        y0 = random.randint(0, h - crop_h)
        v = base.crop((x0, y0, x0 + crop_w, y0 + crop_h)).resize((w, h), Image.LANCZOS)
        if random.random() < 0.5:
            v = v.transpose(Image.FLIP_LEFT_RIGHT)
        variants.append(v)
    return variants


def generate_split(split: str, n: int, cutouts, backgrounds):
    img_dir = OUT / "images" / split
    lbl_dir = OUT / "labels" / split
    img_dir.mkdir(parents=True, exist_ok=True)
    lbl_dir.mkdir(parents=True, exist_ok=True)

    for i in range(n):
        bg = random.choice(backgrounds).copy()
        lines = []
        for _ in range(random.randint(1, 3)):
            cutout, cls = random.choice(cutouts)
            box = place_object(bg, cutout, cls)
            if box:
                lines.append(to_yolo_line(CLASSES.index(cls), box, bg.width, bg.height))
        name = f"{split}_{i:04d}"
        bg.convert("RGB").save(img_dir / f"{name}.jpg", quality=90)
        (lbl_dir / f"{name}.txt").write_text("\n".join(lines) + ("\n" if lines else ""))


def main():
    random.seed(0)
    cutouts = load_cutouts()
    backgrounds = make_background_variants(6)

    generate_split("train", N_TRAIN, cutouts, backgrounds)
    generate_split("val", N_VAL, cutouts, backgrounds)

    (OUT / "data.yaml").write_text(
        f"path: {OUT.resolve()}\n"
        f"train: images/train\n"
        f"val: images/val\n"
        f"names:\n" + "".join(f"  {i}: {c}\n" for i, c in enumerate(CLASSES))
    )
    print(f"Wrote dataset to {OUT} ({N_TRAIN} train, {N_VAL} val)")


if __name__ == "__main__":
    main()
