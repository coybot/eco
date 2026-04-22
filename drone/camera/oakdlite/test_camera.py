#!/usr/bin/env python3
"""
Test script for OAK-D Lite camera (DepthAI 3.x).

Usage:
    python test_camera.py              # Basic test - capture frames
    python test_camera.py --save       # Save frames to disk
    python test_camera.py --frames 50  # Capture 50 frames
"""

import argparse
import time
from pathlib import Path

import numpy as np

try:
    import cv2
    HAS_OPENCV = True
except ImportError:
    HAS_OPENCV = False

try:
    import depthai as dai
except ImportError:
    print("Error: depthai library not installed.")
    print("Install with: pip install depthai")
    exit(1)


def check_device() -> bool:
    """Check if OAK-D Lite is connected."""
    devices = dai.Device.getAllAvailableDevices()
    if not devices:
        print("No OAK devices found!")
        print("\nTroubleshooting:")
        print("  1. Make sure the OAK-D Lite is plugged in via USB")
        print("  2. Try a different USB port (preferably USB 3.0)")
        print("  3. Check if the camera shows up with 'lsusb | grep 03e7'")
        print("  4. Ensure udev rules are installed:")
        print("     echo 'SUBSYSTEM==\"usb\", ATTRS{idVendor}==\"03e7\", MODE=\"0666\"' | sudo tee /etc/udev/rules.d/80-movidius.rules")
        print("     sudo udevadm control --reload-rules && sudo udevadm trigger")
        return False
    
    print(f"Found {len(devices)} OAK device(s):")
    for dev in devices:
        print(f"  - {dev.name} (ID: {dev.getDeviceId()})")
    return True


def run_camera_test(save_frames: bool = False, num_frames: int = 100):
    """Run the camera test."""
    from camera import OakDLiteCamera
    
    output_dir = Path("test_output")
    if save_frames:
        output_dir.mkdir(exist_ok=True)
        print(f"Saving frames to {output_dir}/")
    
    print("\nStarting OAK-D Lite camera...")
    
    with OakDLiteCamera(
        rgb_fps=30,
        enable_depth=True,
        rgb_resolution=(1920, 1080),
        depth_resolution=(640, 400),
    ) as camera:
        print(f"Camera resolution: {camera.resolution}")
        print(f"Capturing {num_frames} frames...")
        print("Press Ctrl+C to stop\n")
        
        frame_count = 0
        start_time = time.time()
        
        try:
            while frame_count < num_frames:
                frame = camera.get_frame()
                
                if frame is None:
                    print("Failed to get frame")
                    continue
                
                frame_count += 1
                
                # Calculate FPS
                elapsed = time.time() - start_time
                fps = frame_count / elapsed if elapsed > 0 else 0
                
                # Print stats every 10 frames
                if frame_count % 10 == 0:
                    depth_stats = ""
                    if frame.depth is not None:
                        valid_depth = frame.depth[frame.depth > 0]
                        if len(valid_depth) > 0:
                            depth_min = np.min(valid_depth)
                            depth_max = np.max(valid_depth)
                            depth_stats = f" | Depth: {depth_min:.0f}-{depth_max:.0f}mm"
                        else:
                            depth_stats = " | Depth: no valid data"
                    
                    rgb_shape = frame.rgb.shape if frame.rgb is not None else "N/A"
                    print(f"Frame {frame_count}: {fps:.1f} FPS | RGB: {rgb_shape}{depth_stats}")
                
                # Save frames
                if save_frames and HAS_OPENCV:
                    if frame.rgb is not None:
                        cv2.imwrite(str(output_dir / f"rgb_{frame_count:04d}.jpg"), 
                                   cv2.cvtColor(frame.rgb, cv2.COLOR_RGB2BGR))
                    if frame.depth is not None:
                        # Normalize depth for visualization
                        depth_vis = (frame.depth / 10000 * 255).astype(np.uint8)
                        cv2.imwrite(str(output_dir / f"depth_{frame_count:04d}.png"), depth_vis)
                        
        except KeyboardInterrupt:
            print("\nStopped by user")
        
        finally:
            total_time = time.time() - start_time
            if frame_count > 0:
                print(f"\nCaptured {frame_count} frames in {total_time:.1f}s ({frame_count/total_time:.1f} FPS)")


def main():
    parser = argparse.ArgumentParser(description="Test OAK-D Lite camera")
    parser.add_argument("--save", action="store_true", help="Save frames to disk")
    parser.add_argument("--frames", type=int, default=100, help="Number of frames to capture")
    args = parser.parse_args()
    
    print("OAK-D Lite Camera Test")
    print("=" * 40)
    
    if not check_device():
        exit(1)
    
    run_camera_test(
        save_frames=args.save,
        num_frames=args.frames,
    )


if __name__ == "__main__":
    main()
