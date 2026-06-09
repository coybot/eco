"""
Abstract base class for all vehicle types (quadcopter, rover, etc.).

Concrete implementations live alongside this file:
  quadcopter.py  — ArduPilot / MAVLink flight stack
  rover.py       — Waveshare UGV via ROS 2 cmd_vel
"""

from abc import ABC, abstractmethod


class BaseVehicle(ABC):
    # ---- lifecycle --------------------------------------------------------

    @abstractmethod
    def arm(self) -> bool:
        """Enable actuators. Returns True on success."""

    @abstractmethod
    def disarm(self) -> bool:
        """Disable actuators safely. Returns True on success."""

    @abstractmethod
    def is_armed(self) -> bool:
        """Return True if actuators are currently enabled."""

    # ---- movement ---------------------------------------------------------

    @abstractmethod
    def stop(self):
        """Immediately halt all motion."""

    @abstractmethod
    def get_position(self) -> tuple:
        """Return (lat, lon, alt_m) from GPS or odometry."""

    # ---- telemetry --------------------------------------------------------

    @abstractmethod
    def get_battery(self) -> dict:
        """Return {'voltage': V, 'remaining': %, 'current': A_or_None}."""

    @abstractmethod
    def get_telemetry(self) -> dict:
        """Return combined telemetry dict (position, attitude, battery, armed, mode)."""
