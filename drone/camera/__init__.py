"""
Camera module with auto-detection for supported cameras.

Supports:
- OAK-D Lite (Luxonis/DepthAI)
- Intel RealSense D435i
"""

from .common.base import Camera, CameraFrame
from .common.auto import get_camera, list_available_cameras

__all__ = ["Camera", "CameraFrame", "get_camera", "list_available_cameras"]

