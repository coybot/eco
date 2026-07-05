"""Step 1: crop known object regions out of the source photos and run rembg to get
clean RGBA cutouts (transparent background) for compositing.

Crop boxes were picked by eye against the downloaded originals. Run from this directory:
    source ../.venv/bin/activate  (after: pip install rembg onnxruntime)
    python3 extract_cutouts.py
"""
from pathlib import Path

from PIL import Image
from rembg import remove, new_session

SRC = Path(__file__).parent / "objects_source"
OUT = Path(__file__).parent / "cutouts"
OUT.mkdir(exist_ok=True)

# (source file, crop box in ORIGINAL pixel coords (l, t, r, b), output name, class name)
CROPS = [
    ("Menzies Aviation aircraft tug.jpg", (1900, 300, 3900, 1900), "tug_01", "tug"),
    ("Menzies Aviation aircraft tug.jpg", (750, 0, 1750, 380), "cart_01", "baggage_cart"),
    ("Menzies Aviation aircraft tug.jpg", (2050, 0, 3350, 460), "cart_02", "baggage_cart"),
    ("Luggage awaiting loading at airport IMG 3140.JPG", (1030, 730, 1950, 2300), "suitcase_black_01", "suitcase"),
    ("Luggage awaiting loading at airport IMG 3140.JPG", (1900, 400, 2900, 2300), "suitcase_red_01", "suitcase"),
    ("Luggage awaiting loading at airport IMG 3140.JPG", (1030, 2150, 3000, 2750), "duffel_black_01", "suitcase"),
]


def main() -> None:
    session = new_session("u2net")
    manifest = []
    for filename, box, name, cls in CROPS:
        img = Image.open(SRC / filename).convert("RGB")
        crop = img.crop(box)
        cutout = remove(crop, session=session)  # RGBA, background removed
        out_path = OUT / f"{name}.png"
        cutout.save(out_path)
        manifest.append((out_path.name, cls))
        print(f"wrote {out_path} ({cutout.size[0]}x{cutout.size[1]}) class={cls}")

    with open(OUT / "manifest.txt", "w") as f:
        for name, cls in manifest:
            f.write(f"{name}\t{cls}\n")


if __name__ == "__main__":
    main()
