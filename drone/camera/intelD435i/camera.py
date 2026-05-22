"""Intel RealSense D435i camera implementation."""

import sys
from pathlib import Path
from typing import Dict, List, Optional, Tuple

import numpy as np

# Add parent paths for imports
sys.path.insert(0, str(Path(__file__).parent.parent.parent))

try:
    import pyrealsense2 as rs
except ImportError:
    raise ImportError("pyrealsense2 library required. Install with: pip install pyrealsense2")

from camera.common import Camera, CameraFrame


class RealSenseCamera(Camera):
    """
    Intel RealSense D435i camera implementation.
    
    The D435i has:
    - 1920x1080 RGB camera
    - Stereo depth from two IR cameras (up to 1280x720)
    - IMU (accelerometer + gyroscope)
    - USB 3.0 connection
    """
    
    CAMERA_TYPE = "realsense"
    
    # Common resolutions supported by D435i
    SUPPORTED_RESOLUTIONS = [
        (1920, 1080),
        (1280, 720),
        (848, 480),
        (640, 480),
        (640, 360),
        (424, 240),
    ]
    
    def __init__(
        self,
        rgb_fps: int = 30,
        enable_depth: bool = True,
        rgb_resolution: Tuple[int, int] = (1280, 720),
        depth_resolution: Tuple[int, int] = (640, 480),
    ):
        """
        Initialize RealSense D435i camera.
        
        Args:
            rgb_fps: Frame rate for RGB camera.
            enable_depth: Whether to enable depth output.
            rgb_resolution: RGB resolution as (width, height). 
                           Defaults to 1280x720 for good balance of quality/performance.
            depth_resolution: Depth resolution as (width, height).
        """
        self._rgb_fps = rgb_fps
        self._enable_depth = enable_depth
        self._rgb_resolution = rgb_resolution
        self._depth_resolution = depth_resolution
        
        self._pipeline: Optional[rs.pipeline] = None
        self._config: Optional[rs.config] = None
        self._align: Optional[rs.align] = None
        self._sequence_num = 0
    
    def start(self) -> None:
        """Start the camera pipeline."""
        if self._pipeline is not None:
            return  # Already started
        
        # Create pipeline but don't assign to self until successful
        pipeline = rs.pipeline()
        config = rs.config()
        
        # Try to configure RGB stream with fallback resolutions
        rgb_started = False
        resolutions_to_try = [self._rgb_resolution] + [
            r for r in self.SUPPORTED_RESOLUTIONS if r != self._rgb_resolution
        ]
        
        for resolution in resolutions_to_try:
            try:
                config = rs.config()  # Reset config for each attempt
                config.enable_stream(
                    rs.stream.color,
                    resolution[0],
                    resolution[1],
                    rs.format.rgb8,
                    self._rgb_fps
                )
                
                # Configure depth stream (if enabled)
                align = None
                if self._enable_depth:
                    # Use matching or lower resolution for depth
                    depth_res = self._depth_resolution
                    if depth_res[0] > resolution[0]:
                        depth_res = (resolution[0], resolution[1])
                    
                    config.enable_stream(
                        rs.stream.depth,
                        depth_res[0],
                        depth_res[1],
                        rs.format.z16,
                        self._rgb_fps
                    )
                    align = rs.align(rs.stream.color)
                
                # Try to start - only assign to self if this succeeds
                pipeline.start(config)
                
                # Success! Now assign to instance variables
                self._pipeline = pipeline
                self._config = config
                self._align = align
                self._rgb_resolution = resolution  # Update to actual resolution
                rgb_started = True
                print(f"RealSense started at {resolution[0]}x{resolution[1]}")
                break
                
            except RuntimeError as e:
                print(f"Failed at {resolution}: {e}")
                continue
        
        if not rgb_started:
            # Make sure pipeline is cleaned up on failure
            try:
                pipeline.stop()
            except:
                pass
            raise RuntimeError("Could not start RealSense camera at any supported resolution")
        
        # Let auto-exposure settle
        for _ in range(30):
            self._pipeline.wait_for_frames()
    
    def stop(self) -> None:
        """Stop the camera pipeline and release resources."""
        if self._pipeline is not None:
            self._pipeline.stop()
            self._pipeline = None
        self._config = None
        self._align = None
    
    def get_frame(self, timeout_ms: int = 1000) -> Optional[CameraFrame]:
        """
        Get the next frame from the camera.
        
        Args:
            timeout_ms: Maximum time to wait for a frame in milliseconds.
            
        Returns:
            CameraFrame if successful, None if timeout or error.
        """
        if self._pipeline is None:
            try:
                self.start()
            except Exception as e:
                print(f"Error starting camera pipeline: {e}")
                return None
        
        try:
            # Wait for frames with timeout
            frames = self._pipeline.wait_for_frames(timeout_ms)
            
            if not frames:
                return None
            
            # Align depth to color if enabled
            if self._enable_depth and self._align:
                frames = self._align.process(frames)
            
            frame = CameraFrame()
            
            # Get RGB frame
            color_frame = frames.get_color_frame()
            if color_frame:
                frame.rgb = np.asanyarray(color_frame.get_data())
                frame.timestamp_ms = color_frame.get_timestamp()
            
            # Get depth frame
            if self._enable_depth:
                depth_frame = frames.get_depth_frame()
                if depth_frame:
                    frame.depth = np.asanyarray(depth_frame.get_data())
            
            self._sequence_num += 1
            frame.sequence_num = self._sequence_num
            
            return frame
            
        except Exception as e:
            # If pipeline wasn't started or got reset, retry once
            msg = str(e)
            print(f"Error getting frame: {msg}")
            if "before start()" in msg or "not started" in msg.lower():
                try:
                    # Force reset pipeline state
                    self._pipeline = None
                    self._config = None
                    self._align = None
                    
                    # Wait briefly for USB device to settle
                    import time
                    time.sleep(0.5)
                    
                    # Try fresh start
                    self.start()
                    
                    if self._pipeline is None:
                        print("Failed to restart camera pipeline")
                        return None
                    
                    frames = self._pipeline.wait_for_frames(timeout_ms)
                    if not frames:
                        return None
                    if self._enable_depth and self._align:
                        frames = self._align.process(frames)
                    frame = CameraFrame()
                    color_frame = frames.get_color_frame()
                    if color_frame:
                        frame.rgb = np.asanyarray(color_frame.get_data())
                        frame.timestamp_ms = color_frame.get_timestamp()
                    if self._enable_depth:
                        depth_frame = frames.get_depth_frame()
                        if depth_frame:
                            frame.depth = np.asanyarray(depth_frame.get_data())
                    self._sequence_num += 1
                    frame.sequence_num = self._sequence_num
                    return frame
                except Exception as retry_err:
                    print(f"Error retrying frame capture: {retry_err}")
                    # Ensure clean state on failure
                    self._pipeline = None
                    self._config = None
                    self._align = None
            return None
    
    def is_connected(self) -> bool:
        """Check if camera is connected and operational."""
        try:
            ctx = rs.context()
            devices = ctx.query_devices()
            return len(devices) > 0
        except Exception:
            return False
    
    @property
    def resolution(self) -> Tuple[int, int]:
        """Return (width, height) of the RGB camera."""
        return self._rgb_resolution
    
    @staticmethod
    def list_devices() -> List[Dict]:
        """
        List all connected RealSense devices.
        
        Returns:
            List of device info dictionaries.
        """
        devices = []
        try:
            ctx = rs.context()
            for device in ctx.query_devices():
                devices.append({
                    "name": device.get_info(rs.camera_info.name),
                    "serial": device.get_info(rs.camera_info.serial_number),
                    "firmware": device.get_info(rs.camera_info.firmware_version),
                    "usb_type": device.get_info(rs.camera_info.usb_type_descriptor),
                })
        except Exception as e:
            print(f"Error listing devices: {e}")
        return devices


def test_camera():
    """Quick test to verify camera is working."""
    import cv2
    
    print("Looking for RealSense cameras...")
    devices = RealSenseCamera.list_devices()
    
    if not devices:
        print("No RealSense cameras found!")
        return False
    
    print(f"Found {len(devices)} device(s):")
    for dev in devices:
        print(f"  - {dev['name']} (serial: {dev['serial']})")
    
    print("\nInitializing camera...")
    camera = RealSenseCamera(rgb_fps=30, enable_depth=True)
    
    try:
        camera.start()
        print("Camera started successfully!")
        
        print("Capturing test frame...")
        frame = camera.get_frame(timeout_ms=5000)
        
        if frame and frame.rgb is not None:
            print(f"Got RGB frame: {frame.rgb.shape}")
            
            # Save test image
            bgr = cv2.cvtColor(frame.rgb, cv2.COLOR_RGB2BGR)
            cv2.imwrite("/tmp/realsense_test.jpg", bgr)
            print("Saved test image to /tmp/realsense_test.jpg")
            
            if frame.depth is not None:
                print(f"Got depth frame: {frame.depth.shape}")
            
            return True
        else:
            print("Failed to capture frame!")
            return False
            
    finally:
        camera.stop()
        print("Camera stopped.")


if __name__ == "__main__":
    test_camera()

