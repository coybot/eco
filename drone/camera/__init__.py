"""
Camera module with auto-detection for supported cameras.

Supports:
- OAK-D Lite (Luxonis/DepthAI)
- Intel RealSense (D435i and other colour units; depth-only units like the
  D430 are skipped when a colour image is needed)
- Any V4L2 camera: USB/UVC webcams, and v4l2loopback shares of a camera
  another process owns

Configure per aircraft with CAMERA_TYPE / CAMERA_SERIAL / CAMERA_DEVICE, or
leave unset to auto-detect. See common/auto.py.
"""

from .common.base import Camera, CameraFrame
from .common.auto import get_camera, list_available_cameras, last_camera_error

__all__ = ["Camera", "CameraFrame", "get_camera", "list_available_cameras", "last_camera_error"]
