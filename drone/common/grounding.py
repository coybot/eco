"""Grounding DINO wrapper (open-vocabulary detection).

Uses `IDEA-Research/grounding-dino-tiny` via HuggingFace transformers.
Matches the "Track A" pipeline from the papers.
"""

from dataclasses import dataclass
from typing import List, Optional

import numpy as np


@dataclass
class GDetection:
    label: str
    score: float
    bbox: tuple  # (x1, y1, x2, y2) in pixels, corresponds to the input image

    @property
    def center(self) -> tuple:
        x1, y1, x2, y2 = self.bbox
        return (int((x1 + x2) / 2), int((y1 + y2) / 2))

    @property
    def area(self) -> float:
        x1, y1, x2, y2 = self.bbox
        return max(0.0, x2 - x1) * max(0.0, y2 - y1)


class GroundingDINO:
    """Lazy-loaded open-vocab detector."""

    MODEL_ID = "IDEA-Research/grounding-dino-tiny"

    def __init__(
        self,
        device: Optional[str] = None,
        image_shortest_edge: int = 300,
        image_longest_edge: int = 400,
        use_fp16: Optional[bool] = None,
    ):
        import torch
        self._torch = torch
        self.device = device or ("cuda" if torch.cuda.is_available() else "cpu")
        # Skip upsample-to-800 default; D435i is 640x480 native.
        self.image_shortest_edge = image_shortest_edge
        self.image_longest_edge = image_longest_edge
        # fp16 autocast on CUDA by default
        self.use_fp16 = use_fp16 if use_fp16 is not None else self.device.startswith("cuda")
        self._model = None
        self._processor = None

    def _lazy_load(self):
        if self._model is not None:
            return
        from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection
        self._processor = AutoProcessor.from_pretrained(self.MODEL_ID)
        self._model = AutoModelForZeroShotObjectDetection.from_pretrained(
            self.MODEL_ID
        ).to(self.device).eval()

    def detect(
        self,
        image_rgb: np.ndarray,
        queries: List[str],
        box_threshold: float = 0.35,
        text_threshold: float = 0.25,
    ) -> List[GDetection]:
        """Detect each query string in the image. Returns all detections, highest-score first.

        `image_rgb`: HxWx3 uint8, RGB order.
        `queries`: list of noun phrases, e.g. ["person", "chair"].
        """
        self._lazy_load()
        torch = self._torch

        # GroundingDINO expects a single period-joined string per image
        text = ". ".join(q.strip().rstrip(".") for q in queries) + "."
        from PIL import Image
        pil = Image.fromarray(image_rgb)
        size = {"shortest_edge": self.image_shortest_edge,
                "longest_edge": self.image_longest_edge}
        inputs = self._processor(
            images=pil, text=text, return_tensors="pt", size=size,
        ).to(self.device)
        with torch.inference_mode():
            if self.use_fp16:
                with torch.autocast("cuda", dtype=torch.float16):
                    outputs = self._model(**inputs)
            else:
                outputs = self._model(**inputs)

        h, w = image_rgb.shape[:2]
        # transformers renamed `box_threshold` -> `threshold` in v5; support both.
        ppgod = self._processor.post_process_grounded_object_detection
        try:
            results = ppgod(
                outputs,
                inputs.input_ids,
                threshold=box_threshold,
                text_threshold=text_threshold,
                target_sizes=[(h, w)],
            )
        except TypeError:
            results = ppgod(
                outputs,
                inputs.input_ids,
                box_threshold=box_threshold,
                text_threshold=text_threshold,
                target_sizes=[(h, w)],
            )
        out: List[GDetection] = []
        r = results[0]
        boxes = r.get("boxes")
        scores = r.get("scores")
        labels = r.get("labels") or r.get("text_labels") or []
        if boxes is None or scores is None:
            return out
        boxes = boxes.detach().cpu().numpy()
        scores = scores.detach().cpu().numpy()
        for bb, sc, lb in zip(boxes, scores, labels):
            out.append(GDetection(
                label=str(lb),
                score=float(sc),
                bbox=(float(bb[0]), float(bb[1]), float(bb[2]), float(bb[3])),
            ))
        out.sort(key=lambda d: d.score, reverse=True)
        return out
