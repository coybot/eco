"""
Perception Service - On-device object detection with distance estimation.

Provides YOLOv8-based object detection running on Jetson Orin GPU via TensorRT,
with fallback to ONNX Runtime for development machines.

Works with either OAK-D or RealSense cameras via the camera abstraction.
"""

import sys
import time
import numpy as np
from dataclasses import dataclass
from pathlib import Path
from typing import Optional

# Add parent for imports
COMMON_DIR = Path(__file__).parent.absolute()
DRONE_DIR = COMMON_DIR.parent
sys.path.insert(0, str(DRONE_DIR))

# COCO class names (80 classes) — used by the stock yolov8n.onnx baseline.
COCO_CLASSES = [
    'person', 'bicycle', 'car', 'motorcycle', 'airplane', 'bus', 'train', 'truck',
    'boat', 'traffic light', 'fire hydrant', 'stop sign', 'parking meter', 'bench',
    'bird', 'cat', 'dog', 'horse', 'sheep', 'cow', 'elephant', 'bear', 'zebra',
    'giraffe', 'backpack', 'umbrella', 'handbag', 'tie', 'suitcase', 'frisbee',
    'skis', 'snowboard', 'sports ball', 'kite', 'baseball bat', 'baseball glove',
    'skateboard', 'surfboard', 'tennis racket', 'bottle', 'wine glass', 'cup',
    'fork', 'knife', 'spoon', 'bowl', 'banana', 'apple', 'sandwich', 'orange',
    'broccoli', 'carrot', 'hot dog', 'pizza', 'donut', 'cake', 'chair', 'couch',
    'potted plant', 'bed', 'dining table', 'toilet', 'tv', 'laptop', 'mouse',
    'remote', 'keyboard', 'cell phone', 'microwave', 'oven', 'toaster', 'sink',
    'refrigerator', 'book', 'clock', 'vase', 'scissors', 'teddy bear', 'hair drier',
    'toothbrush'
]

# Domain class names for the presidio-trained detector (v1+).
# Matches the category order in drone-data/dataset.yaml / sim_dataset_recorder.CLASS_NAMES.
# Switch to this by loading yolov8n_domain_v1.onnx (or .engine) instead of yolov8n.
DOMAIN_CLASSES = [
    'person', 'person_aerial', 'vehicle', 'bicycle_motorcycle',
    'drone', 'landing_pad', 'powerline_pole', 'animal', 'boat',
]


@dataclass
class Detection:
    """A single detected object with estimated distance and direction."""
    
    label: str              # COCO class name: person, door, car, chair, etc.
    confidence: float       # Detection confidence 0.0 - 1.0
    bbox: tuple             # Bounding box (x1, y1, x2, y2) in pixels
    distance_m: float       # Estimated distance in meters (from object size)
    direction_deg: float    # Direction relative to drone heading (-180 to 180)
    
    def to_dict(self) -> dict:
        """Convert to dictionary for JSON serialization."""
        return {
            'label': self.label,
            'confidence': round(self.confidence, 2),
            'bbox': self.bbox,
            'distance_m': round(self.distance_m, 1),
            'direction_deg': round(self.direction_deg, 0)
        }


class DistanceEstimator:
    """
    Estimate distance to objects from their apparent size in the image.
    
    Uses the pinhole camera model: distance = (real_height * focal_length) / pixel_height
    """
    
    # Known real-world heights in meters for common COCO objects
    KNOWN_HEIGHTS = {
        'person': 1.7,
        'bicycle': 1.0,
        'car': 1.5,
        'motorcycle': 1.1,
        'bus': 3.0,
        'truck': 2.5,
        'chair': 0.9,
        'couch': 0.85,
        'bed': 0.6,
        'dining table': 0.75,
        'toilet': 0.4,
        'tv': 0.6,
        'laptop': 0.25,
        'refrigerator': 1.8,
        'door': 2.0,  # Note: 'door' is not in COCO, but useful
        'potted plant': 0.5,
        'clock': 0.3,
        'vase': 0.3,
        'bottle': 0.25,
        'cup': 0.1,
        'bowl': 0.08,
        'book': 0.25,
        'cell phone': 0.15,
        'backpack': 0.5,
        'umbrella': 1.0,
        'suitcase': 0.7,
        'dog': 0.5,
        'cat': 0.25,
        'bird': 0.15,
        # Wider-than-COCO size priors — a target noun that isn't in COCO
        # would otherwise silently
        # fall through to the generic 1.0m default below, which is a
        # meaningfully worse distance estimate for something car-sized or
        # building-sized than a real (if rough) per-class guess. Rough
        # estimates in the same spirit as the entries above, not measured.
        'vehicle': 1.5,
        'boat': 2.0,
        'building': 6.0,
        'tree': 6.0,
        'animal': 0.6,
    }
    
    # Default focal length in pixels (approximate for 60-70° FOV at 720p)
    # This should be calibrated per camera for accuracy
    DEFAULT_FOCAL_LENGTH_PX = 600
    
    def __init__(self, focal_length_px: float = None):
        """
        Initialize distance estimator.
        
        Args:
            focal_length_px: Camera focal length in pixels. If None, uses default.
        """
        self.focal_length_px = focal_length_px or self.DEFAULT_FOCAL_LENGTH_PX
    
    def estimate(self, label: str, bbox: tuple, image_shape: tuple) -> float:
        """
        Estimate distance to detected object.
        
        Args:
            label: Object class label
            bbox: Bounding box (x1, y1, x2, y2) in pixels
            image_shape: Image shape (height, width, channels)
        
        Returns:
            Estimated distance in meters, or float('inf') if cannot estimate
        """
        known_height = self.KNOWN_HEIGHTS.get(label, 1.0)  # Default 1m for unknown
        
        x1, y1, x2, y2 = bbox
        bbox_height_px = y2 - y1
        
        if bbox_height_px < 10:  # Too small to estimate reliably
            return float('inf')
        
        # Pinhole camera model
        distance = (known_height * self.focal_length_px) / bbox_height_px
        
        return max(0.1, min(distance, 100.0))  # Clamp to reasonable range
    
    def estimate_direction(self, bbox: tuple, image_width: int, hfov_deg: float = 70.0) -> float:
        """
        Estimate direction to object relative to camera center.
        
        Args:
            bbox: Bounding box (x1, y1, x2, y2)
            image_width: Image width in pixels
            hfov_deg: Horizontal field of view in degrees
        
        Returns:
            Direction in degrees (-180 to 180), where 0 is straight ahead
        """
        x1, y1, x2, y2 = bbox
        bbox_center_x = (x1 + x2) / 2
        image_center_x = image_width / 2
        
        # Calculate angle from center
        offset_ratio = (bbox_center_x - image_center_x) / image_width
        direction_deg = offset_ratio * hfov_deg
        
        return direction_deg


class TensorRTDetector:
    """
    YOLOv8 object detector using TensorRT on Jetson Orin.
    
    Falls back to ONNX Runtime if TensorRT is not available (for development).
    """
    
    def __init__(self, model_path: str = None, confidence_threshold: float = 0.5):
        """
        Initialize detector.
        
        Args:
            model_path: Path to model file (.engine for TensorRT, .onnx for ONNX Runtime)
            confidence_threshold: Minimum confidence for detections
        """
        self.confidence_threshold = confidence_threshold
        self.model_path = model_path or self._find_model()
        self.engine = None
        self.session = None
        self.use_tensorrt = False
        
        self._load_model()
    
    def _find_model(self) -> str:
        """Find model file. Preference order: domain TRT engine → domain ONNX → stock ONNX."""
        # Install layout: script in ~/drone-api, models in ~/drone-api/models
        # Repo layout: script in drone/common, models in drone/models
        models_dir = COMMON_DIR / 'models' if (COMMON_DIR / 'models').exists() else DRONE_DIR / 'models'

        # Domain-trained detector (presidio v1): try TRT then ONNX
        for name in ('yolov8n_domain_v3.engine', 'yolov8n_domain_v3.onnx',
                     'yolov8n_domain_v1.engine', 'yolov8n_domain_v1.onnx'):
            p = models_dir / name
            if p.exists():
                return str(p)

        # Stock COCO baseline
        for name in ('yolov8n.engine', 'yolov8n.onnx'):
            p = models_dir / name
            if p.exists():
                return str(p)

        raise FileNotFoundError(
            f"No YOLOv8 model found in {models_dir}. "
            "Run 'python setup_models.py --domain-detector' to download."
        )
    
    def _load_model(self):
        """Load model, trying TensorRT first, then ONNX Runtime."""
        if self.model_path.endswith('.engine'):
            self._load_tensorrt()
        else:
            self._load_onnx()
    
    def _load_tensorrt(self):
        """Load TensorRT engine."""
        try:
            import tensorrt as trt
            import pycuda.driver as cuda
            import pycuda.autoinit  # noqa: F401
            
            logger = trt.Logger(trt.Logger.WARNING)
            
            with open(self.model_path, 'rb') as f:
                runtime = trt.Runtime(logger)
                self.engine = runtime.deserialize_cuda_engine(f.read())
            
            self.context = self.engine.create_execution_context()
            self.use_tensorrt = True
            print(f"Loaded TensorRT engine: {self.model_path}")
            
        except ImportError:
            print("TensorRT not available, falling back to ONNX")
            # Try ONNX fallback
            onnx_path = self.model_path.replace('.engine', '.onnx')
            if Path(onnx_path).exists():
                self.model_path = onnx_path
                self._load_onnx()
            else:
                raise RuntimeError("Neither TensorRT nor ONNX model available")
    
    def _load_onnx(self):
        """Load ONNX model with ONNX Runtime."""
        try:
            import onnxruntime as ort
            
            # Prefer GPU execution
            providers = ['CUDAExecutionProvider', 'CPUExecutionProvider']
            self.session = ort.InferenceSession(self.model_path, providers=providers)
            self.use_tensorrt = False
            
            # Get actual provider being used
            actual_provider = self.session.get_providers()[0]
            print(f"Loaded ONNX model: {self.model_path} (using {actual_provider})")
            
        except ImportError:
            raise RuntimeError("ONNX Runtime not available. Install with: pip install onnxruntime-gpu")
    
    def detect(self, rgb_frame: np.ndarray) -> list[tuple]:
        """
        Run YOLOv8 detection on an RGB frame.
        
        Args:
            rgb_frame: RGB image as numpy array (H, W, 3)
        
        Returns:
            List of raw detections: (label, confidence, bbox)
        """
        # Preprocess
        input_tensor = self._preprocess(rgb_frame)
        
        # Run inference
        if self.use_tensorrt:
            outputs = self._infer_tensorrt(input_tensor)
        else:
            outputs = self._infer_onnx(input_tensor)
        
        # Postprocess
        detections = self._postprocess(outputs, rgb_frame.shape)
        
        return detections
    
    def _preprocess(self, rgb_frame: np.ndarray) -> np.ndarray:
        """Preprocess image for YOLOv8 (resize to 640x640, normalize)."""
        import cv2
        
        # Resize to 640x640
        img = cv2.resize(rgb_frame, (640, 640))
        
        # Convert to float and normalize
        img = img.astype(np.float32) / 255.0
        
        # Transpose to CHW format
        img = img.transpose(2, 0, 1)
        
        # Add batch dimension
        img = np.expand_dims(img, axis=0)
        
        return np.ascontiguousarray(img)
    
    def _infer_tensorrt(self, input_tensor: np.ndarray) -> np.ndarray:
        """Run inference with TensorRT."""
        import pycuda.driver as cuda
        
        # Allocate buffers
        d_input = cuda.mem_alloc(input_tensor.nbytes)
        output_shape = (1, 84, 8400)  # YOLOv8n output shape
        output = np.empty(output_shape, dtype=np.float32)
        d_output = cuda.mem_alloc(output.nbytes)
        
        # Copy input to device
        cuda.memcpy_htod(d_input, input_tensor)
        
        # Run inference
        self.context.execute_v2([int(d_input), int(d_output)])
        
        # Copy output to host
        cuda.memcpy_dtoh(output, d_output)
        
        return output
    
    def _infer_onnx(self, input_tensor: np.ndarray) -> np.ndarray:
        """Run inference with ONNX Runtime."""
        input_name = self.session.get_inputs()[0].name
        outputs = self.session.run(None, {input_name: input_tensor})
        return outputs[0]
    
    def _postprocess(self, outputs: np.ndarray, original_shape: tuple) -> list[tuple]:
        """
        Postprocess YOLOv8 outputs to get detections.
        
        Args:
            outputs: Raw model outputs (1, 84, 8400) for YOLOv8n
            original_shape: Original image shape for coordinate scaling
        
        Returns:
            List of (label, confidence, bbox) tuples
        """
        # YOLOv8 output format: (1, 84, 8400)
        # 84 = 4 (bbox) + 80 (class scores)
        # 8400 = number of predictions
        
        predictions = outputs[0].T  # (8400, 84)
        
        # Extract boxes and scores
        boxes = predictions[:, :4]  # x_center, y_center, width, height
        scores = predictions[:, 4:]  # 80 class scores
        
        # Get max score and class for each prediction
        max_scores = np.max(scores, axis=1)
        class_ids = np.argmax(scores, axis=1)
        
        # Filter by confidence
        mask = max_scores > self.confidence_threshold
        boxes = boxes[mask]
        max_scores = max_scores[mask]
        class_ids = class_ids[mask]
        
        if len(boxes) == 0:
            return []
        
        # Convert from center format to corner format
        x_center, y_center, width, height = boxes[:, 0], boxes[:, 1], boxes[:, 2], boxes[:, 3]
        x1 = x_center - width / 2
        y1 = y_center - height / 2
        x2 = x_center + width / 2
        y2 = y_center + height / 2
        
        # Scale coordinates to original image size
        orig_h, orig_w = original_shape[:2]
        scale_x = orig_w / 640
        scale_y = orig_h / 640
        
        x1 = (x1 * scale_x).astype(int)
        y1 = (y1 * scale_y).astype(int)
        x2 = (x2 * scale_x).astype(int)
        y2 = (y2 * scale_y).astype(int)
        
        # Apply NMS
        detections = []
        for i in range(len(boxes)):
            label = COCO_CLASSES[class_ids[i]] if class_ids[i] < len(COCO_CLASSES) else f"class_{class_ids[i]}"
            confidence = float(max_scores[i])
            bbox = (int(x1[i]), int(y1[i]), int(x2[i]), int(y2[i]))
            detections.append((label, confidence, bbox))
        
        # Simple NMS by removing overlapping boxes
        detections = self._nms(detections)
        
        return detections
    
    def _nms(self, detections: list, iou_threshold: float = 0.5) -> list:
        """Apply non-maximum suppression."""
        if not detections:
            return []
        
        # Sort by confidence
        detections = sorted(detections, key=lambda x: x[1], reverse=True)
        
        keep = []
        while detections:
            best = detections.pop(0)
            keep.append(best)
            
            # Remove overlapping detections of same class
            detections = [
                d for d in detections
                if d[0] != best[0] or self._iou(best[2], d[2]) < iou_threshold
            ]
        
        return keep
    
    def _iou(self, box1: tuple, box2: tuple) -> float:
        """Calculate intersection over union."""
        x1 = max(box1[0], box2[0])
        y1 = max(box1[1], box2[1])
        x2 = min(box1[2], box2[2])
        y2 = min(box1[3], box2[3])
        
        if x2 < x1 or y2 < y1:
            return 0.0
        
        intersection = (x2 - x1) * (y2 - y1)
        area1 = (box1[2] - box1[0]) * (box1[3] - box1[1])
        area2 = (box2[2] - box2[0]) * (box2[3] - box2[1])
        union = area1 + area2 - intersection

        return intersection / union if union > 0 else 0.0


# Open-vocabulary detection is not shipped.
#
# This previously ran YOLO-World through the `ultralytics` package. That
# package is AGPL-3.0: importing it here made the distributed result a combined
# work under a licence incompatible with this project's Apache-2.0 terms.
# Detection now runs entirely through TensorRTDetector above, which uses ONNX
# Runtime (or TensorRT) over exported weights and carries no such restriction.
#
# Re-adding open-vocabulary detection on ONNX Runtime is possible, but it is not
# a drop-in swap. An open-vocab head scores image regions against *text*
# embeddings, so it needs both the vision model and a text encoder exported to
# ONNX, and set_classes() has to become "recompute text embeddings for the new
# class list" rather than a cheap attribute update. Choose a model whose licence
# permits redistribution: exporting weights to ONNX does not change their
# licence.


class PerceptionService:
    """
    High-level perception service combining detection and distance estimation.
    
    Provides both structured detections and natural language descriptions
    for the on-device reasoning LLM.
    """
    
    def __init__(self, confidence_threshold: float = 0.5, prefer_open_vocab: bool = False):
        """
        Initialize perception service.

        Args:
            confidence_threshold: Minimum confidence for detections
            prefer_open_vocab: Accepted for backward compatibility and
                ignored — open-vocabulary detection is not shipped.
        """
        self.detector = None
        self.distance_estimator = DistanceEstimator()
        self.confidence_threshold = confidence_threshold
        self.prefer_open_vocab = prefer_open_vocab
        self._camera = None
        self._last_detections = []
        self._last_detection_time = 0

    def _get_detector(self):
        """Lazy-load the ONNX Runtime / TensorRT detector."""
        if self.detector is None:
            if self.prefer_open_vocab:
                # Kept in the signature so existing callers don't break, but it
                # can no longer be honoured — see the note above PerceptionService.
                print("Open-vocabulary detection is not available in this build; "
                      "using the fixed-class detector.")
            if self.detector is None:
                try:
                    self.detector = TensorRTDetector(
                        confidence_threshold=self.confidence_threshold
                    )
                except FileNotFoundError as e:
                    print(f"Warning: {e}")
                    print("Object detection disabled - no model available")
                    return None
        return self.detector
    
    def _get_camera(self):
        """Get camera instance."""
        if self._camera is None:
            from camera.common.auto import get_camera
            self._camera = get_camera(enable_depth=False)
            if self._camera:
                self._camera.start()
        return self._camera
    
    def detect(
        self,
        rgb_frame: np.ndarray,
        depth: np.ndarray = None,
        intrinsics: dict = None,
        pose: dict = None,
    ) -> list[Detection]:
        """
        Run detection on an RGB frame.

        Args:
            rgb_frame: RGB image as numpy array (H, W, 3)
            depth: optional aligned depth map (H, W) uint16 mm, logged for training only
            intrinsics: optional camera intrinsics dict, logged for training only
            pose: optional vehicle pose dict, logged for training only

        Returns:
            List of Detection objects with distances and directions
        """
        detector = self._get_detector()
        if detector is None:
            return []
        
        raw_detections = detector.detect(rgb_frame)
        
        detections = []
        for label, confidence, bbox in raw_detections:
            distance_m = self.distance_estimator.estimate(
                label, bbox, rgb_frame.shape
            )
            direction_deg = self.distance_estimator.estimate_direction(
                bbox, rgb_frame.shape[1]
            )
            
            detections.append(Detection(
                label=label,
                confidence=confidence,
                bbox=bbox,
                distance_m=distance_m,
                direction_deg=direction_deg
            ))
        
        # Sort by distance
        detections.sort(key=lambda d: d.distance_m)
        
        self._last_detections = detections
        self._last_detection_time = time.time()

        # Optional training-data capture (no-op unless a recorder is enabled).
        from data_recorder import get_default
        recorder = get_default()
        if recorder.enabled:
            recorder.record_detection(
                rgb_frame, detections, depth=depth, intrinsics=intrinsics, pose=pose
            )

        return detections
    
    def detect_from_camera(self) -> list[Detection]:
        """
        Capture frame from camera and run detection.
        
        Returns:
            List of Detection objects
        """
        camera = self._get_camera()
        if camera is None:
            print("Warning: No camera available")
            return []
        
        frame = camera.get_frame(timeout_ms=2000)
        if frame is None or frame.rgb is None:
            print("Warning: Failed to capture frame")
            return []
        
        return self.detect(frame.rgb)
    
    def get_scene_description(self) -> str:
        """
        Generate natural language description of current view for LLM.
        
        Returns:
            Human-readable description of detected objects
        """
        detections = self.detect_from_camera()
        
        if not detections:
            return "I see an open area with no notable objects."
        
        desc_lines = ["Objects in view:"]
        for d in detections:
            direction_str = self._direction_to_text(d.direction_deg)
            desc_lines.append(
                f"- {d.label} ({d.distance_m:.1f}m {direction_str}, {d.confidence:.0%} confident)"
            )
        
        return "\n".join(desc_lines)
    
    def _direction_to_text(self, deg: float) -> str:
        """Convert direction in degrees to human-readable text."""
        if abs(deg) < 10:
            return "ahead"
        elif deg < -30:
            return "to the left"
        elif deg < -10:
            return "slightly left"
        elif deg > 30:
            return "to the right"
        elif deg > 10:
            return "slightly right"
        else:
            return "ahead"
    
    def find_nearest(self, class_name: str) -> Optional[Detection]:
        """
        Find the nearest object of a specific class.
        
        Args:
            class_name: Object class to find (e.g., 'person', 'door', 'car')
        
        Returns:
            Detection if found, None otherwise
        """
        detections = self.detect_from_camera()
        matches = [d for d in detections if d.label == class_name]
        
        if not matches:
            return None
        
        return min(matches, key=lambda d: d.distance_m)
    
    def get_last_detections(self) -> list[Detection]:
        """Get detections from the last detect() call."""
        return self._last_detections

    def get_current_frame(self):
        """Capture and return the current camera frame (RGB numpy array), or None.

        Public counterpart to detect_from_camera()'s internal capture step — added
        so callers (e.g. backends.HardwareBackend) can grab a frame once and pass it
        to both detect() and a VLM without capturing twice.
        """
        camera = self._get_camera()
        if camera is None:
            return None
        frame = camera.get_frame(timeout_ms=2000)
        if frame is None or frame.rgb is None:
            return None
        return frame.rgb
    
    def release(self):
        """Release camera resources."""
        if self._camera is not None:
            self._camera.stop()
            self._camera = None


# Convenience function for quick testing
def test_perception():
    """Test perception service with live camera."""
    print("Testing perception service...")
    
    service = PerceptionService()
    
    try:
        print("\nCapturing and analyzing frame...")
        description = service.get_scene_description()
        print(description)
        
        detections = service.get_last_detections()
        print(f"\nFound {len(detections)} objects:")
        for d in detections:
            print(f"  {d.to_dict()}")
        
    finally:
        service.release()
        print("\nPerception test complete.")


if __name__ == "__main__":
    test_perception()
