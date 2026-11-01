"""
Generic V4L2 camera implementation.

Covers what the vendor SDK backends do not: plain USB/UVC webcams, CSI
cameras exposed through V4L2, and v4l2loopback devices that another process
feeds. The last case is the one that matters on a shared aircraft. When a
streaming service already owns a RealSense's colour stream (as cam-split does
on the quadcopter, splitting /dev/video4 into /dev/video10 and /dev/video11),
librealsense cannot open it again -- "Device or resource busy" -- but the
loopback copy reads fine alongside the stream.

RGB only: V4L2 carries no depth.
"""

import glob
import os
import sys
import time
from typing import Dict, List, Optional, Tuple, Union

import numpy as np

from camera.common import Camera, CameraFrame

_SYSFS = "/sys/class/video4linux"


class V4L2Camera(Camera):
    """A colour camera read through OpenCV's V4L2 backend."""

    CAMERA_TYPE = "v4l2"

    def __init__(
        self,
        device: Union[str, int],
        rgb_fps: int = 30,
        rgb_resolution: Tuple[int, int] = (1280, 720),
    ):
        self._device = device
        self._rgb_fps = rgb_fps
        self._requested_resolution = rgb_resolution
        self._actual_resolution = rgb_resolution
        self._cap = None
        self._seq = 0

    @property
    def device(self) -> Union[str, int]:
        return self._device

    def start(self) -> None:
        if self._cap is not None:
            return  # Already started
        import cv2

        backend = cv2.CAP_V4L2 if sys.platform.startswith("linux") else cv2.CAP_ANY
        cap = cv2.VideoCapture(self._device, backend)
        if not cap.isOpened():
            cap.release()
            raise RuntimeError(
                f"could not open V4L2 device {self._device}: {explain_open_failure(self._device)}")

        # Best effort: a real webcam honours these, a loopback device keeps
        # whatever format its producer negotiated. Read back what we got.
        width, height = self._requested_resolution
        cap.set(cv2.CAP_PROP_FRAME_WIDTH, width)
        cap.set(cv2.CAP_PROP_FRAME_HEIGHT, height)
        cap.set(cv2.CAP_PROP_FPS, self._rgb_fps)
        # Without this a stalled producer blocks read() for OpenCV's default
        # 10 s, which is a long time to hold a mission phase.
        read_timeout = getattr(cv2, "CAP_PROP_READ_TIMEOUT_MSEC", None)
        if read_timeout is not None:
            cap.set(read_timeout, 3000)

        self._cap = cap
        self._actual_resolution = (
            int(cap.get(cv2.CAP_PROP_FRAME_WIDTH)) or width,
            int(cap.get(cv2.CAP_PROP_FRAME_HEIGHT)) or height,
        )

    def stop(self) -> None:
        if self._cap is not None:
            self._cap.release()
            self._cap = None

    def get_frame(self, timeout_ms: int = 1000) -> Optional[CameraFrame]:
        if self._cap is None:
            return None
        import cv2

        deadline = time.time() + max(timeout_ms, 1) / 1000.0
        while True:
            ok, bgr = self._cap.read()
            if ok and bgr is not None and bgr.size:
                break
            if time.time() >= deadline:
                return None
            time.sleep(0.01)

        if bgr.ndim == 2:
            rgb = cv2.cvtColor(bgr, cv2.COLOR_GRAY2RGB)
        else:
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
        self._actual_resolution = (rgb.shape[1], rgb.shape[0])
        self._seq += 1
        return CameraFrame(
            rgb=np.ascontiguousarray(rgb),
            timestamp_ms=time.time() * 1000.0,
            sequence_num=self._seq,
        )

    def is_connected(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    @property
    def resolution(self) -> Tuple[int, int]:
        return self._actual_resolution

    @staticmethod
    def list_devices() -> List[Dict]:
        """V4L2 capture nodes, from sysfs, in /dev/videoN order.

        Skipped on purpose:
        - nodes whose index is not 0: a UVC camera exposes a second node for
          metadata that opens but never delivers an image;
        - RealSense nodes: a RealSense publishes several V4L2 nodes (depth,
          IR, colour, metadata) that only make sense through librealsense,
          which the RealSense backend already covers.
        """
        devices = []
        paths = glob.glob(os.path.join(_SYSFS, "video*"))
        for path in sorted(paths, key=_node_number):
            node = os.path.basename(path)
            name = _read(os.path.join(path, "name")) or node
            index = _read(os.path.join(path, "index"))
            if index not in (None, "0"):
                continue
            if "realsense" in name.lower():
                continue
            devices.append({
                "type": "v4l2",
                "name": name,
                "device": f"/dev/{node}",
                "loopback": _is_loopback(os.path.basename(path), name),
                "available": True,
            })
        return devices


def explain_open_failure(device: Union[str, int], sysfs: str = _SYSFS) -> str:
    """Why a V4L2 node will not open, from what the filesystem says about it.

    OpenCV only reports that it could not open the device. On the quadcopter
    that one line covered four different faults with four different fixes: the
    node was gone, it had been renumbered onto a RealSense, nobody was writing
    to the loopback, or the user could not read it.
    """
    path = f"/dev/video{device}" if isinstance(device, int) else str(device)
    if not os.path.exists(path):
        if os.path.islink(path):
            return (f"{path} is a symlink to a node that no longer exists "
                    f"(-> {os.readlink(path)}); the camera or its loopback is not attached")
        return (f"{path} does not exist; the v4l2loopback module is not loaded, "
                f"or its nodes were numbered differently this boot (ls -l /dev/video*)")
    node = os.path.basename(os.path.realpath(path))
    name = _read(os.path.join(sysfs, node, "name")) or "unknown"
    if not os.access(path, os.R_OK | os.W_OK):
        return f"permission denied on {path}; this user needs the 'video' group"
    if "realsense" in name.lower():
        return (f"{path} is '{name}', a RealSense node, not a colour feed. /dev/videoN "
                f"numbers move when USB cameras enumerate; use a stable name (a udev "
                f"symlink on the loopback's card label) instead of a number")
    if _is_loopback(node, name, sysfs):
        return (f"{path} is the loopback '{name}' but nothing is writing to it, so it "
                f"has no image to read; the service that feeds it (cam-split) is "
                f"stopped or failing (journalctl -u cam-split)")
    return f"{path} ('{name}') exists but is busy or is not a capture device"


def _is_loopback(node: str, name: str, sysfs: str = _SYSFS) -> bool:
    """A v4l2loopback node, whatever its card_label says."""
    if "dummy video device" in name.lower() or "loopback" in name.lower():
        return True
    try:
        return "v4l2loopback" in os.path.realpath(os.path.join(sysfs, node, "device"))
    except OSError:
        return False


def _node_number(path: str) -> int:
    """7 for .../video7, so /dev/video10 sorts after /dev/video9."""
    digits = path.rsplit("video", 1)[-1]
    return int(digits) if digits.isdigit() else 0


def _read(path: str) -> Optional[str]:
    try:
        with open(path) as f:
            return f.read().strip()
    except OSError:
        return None
