"""Auto-detection and factory for camera implementations.

get_camera() returns a camera that has already produced a colour frame, not
merely one that was constructed. The difference is the whole bug this module
used to have: construction always succeeded, and the failures came later, in
start(), where no caller had a fallback. Observed on the quadcopter, which
carries a RealSense D435I and a D430 while a streaming service owns the D435I
colour stream: auto-detection handed back whichever RealSense librealsense
listed first, start() then failed with "Device or resource busy" (D435I) or
"Couldn't resolve requests" (the D430 has no RGB sensor), and every photo
phase logged a failure while the mission reported success. A usable colour
feed was sitting on /dev/video11 the whole time.

Selection, in order:
  1. Explicit settings, from arguments or the environment -- CAMERA_TYPE
     (oakd | realsense | v4l2), CAMERA_SERIAL (a RealSense serial),
     CAMERA_DEVICE (a V4L2 path or index). An explicit request names one
     camera and is never silently substituted: flying with a different
     camera than the one configured is worse than reporting that it is gone.
  2. Otherwise every attached camera, most capable first: OAK-D, then
     RealSense units that have an RGB sensor, then V4L2 devices (USB/UVC
     webcams and v4l2loopback shares).

Each candidate is started and must deliver a frame with RGB data before it
is returned. start() is idempotent on every backend, so callers that go on to
call start() themselves are unaffected. Why nothing worked is kept in
LAST_ERROR, for the caller to report.
"""

import os
from typing import Callable, Dict, List, Optional, Tuple

from .base import Camera

# One line per candidate that was tried and rejected by the last get_camera()
# call, or None if it succeeded. Read it to say why there is no camera.
LAST_ERROR: Optional[str] = None

_PROBE_TIMEOUT_MS = 3000


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


def _realsense_devices() -> List[Dict]:
    """Attached RealSense units, with whether each has an RGB sensor."""
    found = []
    try:
        import pyrealsense2 as rs
        for device in rs.context().query_devices():
            has_rgb = False
            for sensor in device.sensors:
                try:
                    if sensor.get_info(rs.camera_info.name) == "RGB Camera":
                        has_rgb = True
                except Exception:
                    continue
            found.append({
                "type": "realsense",
                "name": device.get_info(rs.camera_info.name),
                "serial": device.get_info(rs.camera_info.serial_number),
                "has_rgb": has_rgb,
                "available": True,
            })
    except Exception:
        pass
    return found


def _v4l2_devices() -> List[Dict]:
    try:
        from ..v4l2.camera import V4L2Camera
        return V4L2Camera.list_devices()
    except Exception:
        return []


def last_camera_error() -> Optional[str]:
    """Why the last get_camera() found nothing usable, or None if it succeeded."""
    return LAST_ERROR


def list_available_cameras() -> List[Dict]:
    """
    List all available cameras.

    Returns:
        List of dicts with camera info: {"type": str, "name": str, "available": bool},
        plus "serial"/"has_rgb" for RealSense and "device" for V4L2.
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

    cameras.extend(_realsense_devices())
    cameras.extend(_v4l2_devices())
    return cameras


def get_camera(
    preferred_type: Optional[str] = None,
    rgb_fps: int = 30,
    enable_depth: bool = True,
    rgb_resolution: Tuple[int, int] = (1280, 720),
    serial: Optional[str] = None,
    device: Optional[str] = None,
    require_rgb: bool = True,
) -> Optional[Camera]:
    """
    Get a started camera that has delivered a frame.

    Args:
        preferred_type: "oakd", "realsense" or "v4l2". Defaults to $CAMERA_TYPE,
                        else auto-detect.
        rgb_fps: Target frame rate for RGB camera.
        enable_depth: Whether to enable depth output (OAK-D/RealSense only).
        rgb_resolution: Desired RGB resolution as (width, height).
        serial: Bind to one specific RealSense by serial number, as reported by
                list_available_cameras(). Defaults to $CAMERA_SERIAL. Required to
                reach a particular unit when several are attached.
        device: A V4L2 device path (e.g. "/dev/video11") or index. Defaults to
                $CAMERA_DEVICE.
        require_rgb: Reject cameras that start but deliver no colour image
                     (a D430 runs depth-only). Default True, since every
                     current caller wants photos or detections.

    Returns:
        Camera instance, already started, or None. On None, LAST_ERROR says why.
    """
    global LAST_ERROR

    preferred_type = (preferred_type or os.environ.get("CAMERA_TYPE") or "").strip().lower() or None
    serial = serial or (os.environ.get("CAMERA_SERIAL") or "").strip() or None
    device = device or (os.environ.get("CAMERA_DEVICE") or "").strip() or None

    # A serial identifies a specific RealSense and a device a specific V4L2
    # node, so each also settles the backend.
    if preferred_type is None and serial:
        preferred_type = "realsense"
    if preferred_type is None and device:
        preferred_type = "v4l2"
    explicit = bool(preferred_type or serial or device)

    candidates = _candidates(preferred_type, serial, device, rgb_fps,
                             enable_depth, rgb_resolution, require_rgb)
    rejected = []
    for label, build, skip_reason in candidates:
        if skip_reason:
            rejected.append(f"{label}: {skip_reason}")
            continue
        camera, reason = _probe(build, require_rgb)
        if camera is not None:
            LAST_ERROR = None
            print(f"Camera selected: {label}")
            return camera
        rejected.append(f"{label}: {reason}")

    if not candidates:
        if explicit:
            wanted = " ".join(x for x in (preferred_type, serial or device) if x)
            rejected.append(f"configured camera ({wanted}) is not attached")
        else:
            rejected.append("no cameras attached")
    LAST_ERROR = "; ".join(rejected)
    print(f"No usable camera: {LAST_ERROR}")
    return None


def _candidates(
    preferred_type: Optional[str],
    serial: Optional[str],
    device: Optional[str],
    rgb_fps: int,
    enable_depth: bool,
    rgb_resolution: Tuple[int, int],
    require_rgb: bool,
) -> List[Tuple[str, Optional[Callable[[], Camera]], Optional[str]]]:
    """(label, constructor, skip_reason) in the order to try them.

    skip_reason is set for a camera already known to be unusable, so the
    reason is reported without paying for a start() that must fail.
    """
    out: List[Tuple[str, Optional[Callable[[], Camera]], Optional[str]]] = []
    want = preferred_type

    if want in (None, "oakd") and not serial and not device:
        if _check_oakd_available():
            out.append(("oakd", lambda: _new_oakd(rgb_fps, enable_depth, rgb_resolution), None))

    if want in (None, "realsense") and not device:
        for rs_dev in _realsense_devices():
            if serial and rs_dev["serial"] != serial:
                continue
            label = f"realsense {rs_dev['name']} ({rs_dev['serial']})"
            if require_rgb and not rs_dev["has_rgb"]:
                out.append((label, None, "no RGB sensor"))
                continue
            out.append((label, lambda s=rs_dev["serial"]: _new_realsense(
                rgb_fps, enable_depth, rgb_resolution, s), None))

    if want in (None, "v4l2") and not serial:
        if device is not None:
            # An explicit node is tried even if sysfs does not list it: a
            # numeric index, or a path on a machine without /sys.
            out.append((f"v4l2 {device}", lambda d=device: _new_v4l2(d, rgb_fps, rgb_resolution), None))
        else:
            for v_dev in _v4l2_devices():
                label = f"v4l2 {v_dev['device']} ({v_dev['name']})"
                out.append((label, lambda d=v_dev["device"]: _new_v4l2(d, rgb_fps, rgb_resolution), None))

    return out


def _probe(build: Callable[[], Camera], require_rgb: bool) -> Tuple[Optional[Camera], str]:
    """Start a candidate and read one frame. Returns (camera, "") or (None, why)."""
    camera = None
    try:
        camera = build()
        camera.start()
        frame = camera.get_frame(timeout_ms=_PROBE_TIMEOUT_MS)
        if frame is None:
            reason = "started but delivered no frame"
        elif require_rgb and frame.rgb is None:
            reason = "started but delivered no colour image"
        else:
            return camera, ""
    except Exception as e:
        reason = str(e) or type(e).__name__
    if camera is not None:
        try:
            camera.stop()
        except Exception:
            pass
    return None, reason


def _new_oakd(rgb_fps: int, enable_depth: bool, rgb_resolution: Tuple[int, int]) -> Camera:
    from ..oakdlite.camera import OakDLiteCamera
    return OakDLiteCamera(rgb_fps=rgb_fps, enable_depth=enable_depth, rgb_resolution=rgb_resolution)


def _new_realsense(rgb_fps: int, enable_depth: bool, rgb_resolution: Tuple[int, int],
                   serial: Optional[str]) -> Camera:
    from ..intelD435i.camera import RealSenseCamera
    return RealSenseCamera(
        rgb_fps=min(rgb_fps, 30),  # Cap at 30fps for stability
        enable_depth=enable_depth,
        rgb_resolution=rgb_resolution,
        serial=serial,
    )


def _new_v4l2(device, rgb_fps: int, rgb_resolution: Tuple[int, int]) -> Camera:
    from ..v4l2.camera import V4L2Camera
    if isinstance(device, str) and device.isdigit():
        device = int(device)
    return V4L2Camera(device, rgb_fps=rgb_fps, rgb_resolution=rgb_resolution)


def _try_oakd(rgb_fps: int, enable_depth: bool, rgb_resolution: Tuple[int, int]) -> Optional[Camera]:
    """Try to create an OAK-D camera instance (unstarted). Kept for callers
    that construct a specific backend themselves."""
    if not _check_oakd_available():
        return None
    try:
        return _new_oakd(rgb_fps, enable_depth, rgb_resolution)
    except Exception as e:
        print(f"Failed to initialize OAK-D camera: {e}")
        return None


def _try_realsense(
    rgb_fps: int,
    enable_depth: bool,
    rgb_resolution: Tuple[int, int],
    serial: Optional[str] = None,
) -> Optional[Camera]:
    """Try to create a RealSense camera instance (unstarted). Kept for callers
    that construct a specific backend themselves."""
    if not _check_realsense_available(serial):
        if serial:
            print(f"RealSense with serial {serial} is not connected")
        return None
    try:
        return _new_realsense(rgb_fps, enable_depth, rgb_resolution, serial)
    except Exception as e:
        print(f"Failed to initialize RealSense camera: {e}")
        return None
