"""Auto-detection and factory for camera implementations."""

from typing import Dict, List, Optional, Tuple
from .base import Camera


def _check_oakd_available() -> bool:
    """Check if OAK-D camera is available."""
    try:
        import depthai as dai
        devices = dai.Device.getAllAvailableDevices()
        return len(devices) > 0
    except Exception:
        return False


def _check_realsense_available(serial: Optional[str] = None) -> bool:
    """Check if a RealSense camera is available, optionally a specific one."""
    try:
        import pyrealsense2 as rs
        devices = list(rs.context().query_devices())
        if serial is None:
            return len(devices) > 0
        return any(
            device.get_info(rs.camera_info.serial_number) == serial
            for device in devices
        )
    except Exception:
        return False


def list_available_cameras() -> List[Dict]:
    """
    List all available cameras.
    
    Returns:
        List of dicts with camera info: {"type": str, "name": str, "available": bool}
    """
    cameras = []
    
    # Check OAK-D
    try:
        import depthai as dai
        for device_info in dai.Device.getAllAvailableDevices():
            cameras.append({
                "type": "oakd",
                "name": f"OAK-D ({device_info.name})",
                "device_id": device_info.getDeviceId(),
                "available": True,
            })
    except Exception:
        pass
    
    # Check RealSense
    try:
        import pyrealsense2 as rs
        ctx = rs.context()
        for device in ctx.query_devices():
            cameras.append({
                "type": "realsense",
                "name": device.get_info(rs.camera_info.name),
                "serial": device.get_info(rs.camera_info.serial_number),
                "available": True,
            })
    except Exception:
        pass
    
    return cameras


def get_camera(
    preferred_type: Optional[str] = None,
    rgb_fps: int = 30,
    enable_depth: bool = True,
    rgb_resolution: Tuple[int, int] = (1280, 720),
    serial: Optional[str] = None,
) -> Optional[Camera]:
    """
    Get a camera instance, auto-detecting the available hardware.
    
    Args:
        preferred_type: Preferred camera type ("oakd", "realsense"). If None, auto-detect.
        rgb_fps: Target frame rate for RGB camera.
        enable_depth: Whether to enable depth output.
        rgb_resolution: Desired RGB resolution as (width, height).
        serial: Bind to one specific RealSense by serial number, as reported by
                list_available_cameras(). Required to reach a particular unit
                when several are attached -- without it librealsense picks one
                arbitrarily and the rest are unreachable.
    
    Returns:
        Camera instance if available, None otherwise.
    """
    # A serial identifies a specific RealSense, so it also settles the backend.
    if serial and preferred_type is None:
        preferred_type = "realsense"
    
    # Try preferred type first
    if preferred_type == "oakd":
        camera = _try_oakd(rgb_fps, enable_depth, rgb_resolution)
        if camera:
            return camera
    elif preferred_type == "realsense":
        camera = _try_realsense(rgb_fps, enable_depth, rgb_resolution, serial)
        if camera:
            return camera
    
    # An explicit serial is a request for one device, not a hint. Fall through
    # to some other camera and the caller silently gets the wrong hardware.
    if serial:
        return None
    
    # Auto-detect: try OAK-D first (preferred), then RealSense
    camera = _try_oakd(rgb_fps, enable_depth, rgb_resolution)
    if camera:
        return camera
    
    camera = _try_realsense(rgb_fps, enable_depth, rgb_resolution)
    if camera:
        return camera
    
    return None


def _try_oakd(rgb_fps: int, enable_depth: bool, rgb_resolution: Tuple[int, int]) -> Optional[Camera]:
    """Try to create an OAK-D camera instance."""
    if not _check_oakd_available():
        return None
    
    try:
        from ..oakdlite.camera import OakDLiteCamera
        return OakDLiteCamera(
            rgb_fps=rgb_fps,
            enable_depth=enable_depth,
            rgb_resolution=rgb_resolution,
        )
    except Exception as e:
        print(f"Failed to initialize OAK-D camera: {e}")
        return None


def _try_realsense(
    rgb_fps: int,
    enable_depth: bool,
    rgb_resolution: Tuple[int, int],
    serial: Optional[str] = None,
) -> Optional[Camera]:
    """Try to create a RealSense camera instance."""
    if not _check_realsense_available(serial):
        if serial:
            print(f"RealSense with serial {serial} is not connected")
        return None
    
    try:
        from ..intelD435i.camera import RealSenseCamera
        # RealSense defaults to 1280x720 which has better compatibility
        return RealSenseCamera(
            rgb_fps=min(rgb_fps, 30),  # Cap at 30fps for stability
            enable_depth=enable_depth,
            rgb_resolution=rgb_resolution,
            serial=serial,
        )
    except Exception as e:
        print(f"Failed to initialize RealSense camera: {e}")
        return None

