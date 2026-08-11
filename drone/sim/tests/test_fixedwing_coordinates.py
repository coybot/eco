"""Test fixed-wing drone coordinate system conversions and positioning."""

import pytest
from unittest.mock import Mock
import sys
import os

# Add the project root to Python path to import modules
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

def test_fixedwing_coordinate_conversion():
    """Test that FixedWingState correctly handles ENU->Godot coordinate conversion."""
    
    # Import the FixedWingState class
    from drone.sim.godot.scripts.fixedwing_manager import FixedWingState
    
    # Test the _from_dict method with sample data
    state = FixedWingState.new()
    
    # Sample test data - ENU coordinates (x=east, y=north, z=up)
    test_position_enu = [10.0, 20.0, 5.0]  # 10m east, 20m north, 5m up
    test_velocity_enu = [15.0, 25.0, 3.0]  # 15m/s east, 25m/s north, 3m/s up
    
    # Expected Godot coordinates (x=right, y=forward, z=up)
    # ENU: x=east, y=north, z=up -> Godot: x=east, z=north, y=-up
    expected_position_godot = [10.0, -5.0, 20.0]  # [x, z, -y]
    expected_velocity_godot = [15.0, -3.0, 25.0]  # [vx, vz, -vy]
    
    # Test data dictionary
    test_data = {
        "id": "test_drone",
        "position": test_position_enu,
        "velocity": test_velocity_enu,
        "airspeed": 20.0,
        "climb_rate": 2.0,
        "pitch": 0.1,
        "yaw": 0.2,
        "roll": 0.05,
        "altitude": 5.0,
        "battery_level": 95.0,
        "events": [],
        "detected_objects": {},
        "observed_grid": [],
        "timestamp": 1000.0
    }
    
    # Apply the data to state
    state._from_dict(test_data)
    
    # Verify the coordinates are correctly converted
    # ENU to Godot conversion:
    # Position: [x, y, z] -> [x, -z, y]
    # Velocity: [vx, vy, vz] -> [vx, -vz, vy]
    
    # Check position conversion
    assert state.position.x == pytest.approx(expected_position_godot[0], abs=1e-6)
    assert state.position.y == pytest.approx(expected_position_godot[1], abs=1e-6)
    assert state.position.z == pytest.approx(expected_position_godot[2], abs=1e-6)
    
    # Check velocity conversion
    assert state.velocity.x == pytest.approx(expected_velocity_godot[0], abs=1e-6)
    assert state.velocity.y == pytest.approx(expected_velocity_godot[1], abs=1e-6)
    assert state.velocity.z == pytest.approx(expected_velocity_godot[2], abs=1e-6)

def test_fixedwing_state_serialization():
    """Test that FixedWingState serialization preserves coordinate integrity."""
    
    from drone.sim.godot.scripts.fixedwing_manager import FixedWingState
    
    # Create a state with specific coordinates
    state = FixedWingState.new()
    state.id = "test_drone"
    state.position = [10.0, 20.0, 5.0]  # ENU coordinates
    state.velocity = [15.0, 25.0, 3.0]  # ENU velocities
    state.airspeed = 20.0
    state.climb_rate = 2.0
    state.pitch = 0.1
    state.yaw = 0.2
    state.roll = 0.05
    state.altitude = 5.0
    state.battery_level = 95.0
    state.timestamp = 1000.0
    
    # Serialize to dict
    data_dict = state._to_dict()
    
    # Deserialize
    new_state = FixedWingState.new()
    new_state._from_dict(data_dict)
    
    # Verify that the coordinates are preserved correctly after round trip
    assert new_state.position.x == pytest.approx(state.position.x, abs=1e-6)
    assert new_state.position.y == pytest.approx(state.position.y, abs=1e-6)
    assert new_state.position.z == pytest.approx(state.position.z, abs=1e-6)
    
    assert new_state.velocity.x == pytest.approx(state.velocity.x, abs=1e-6)
    assert new_state.velocity.y == pytest.approx(state.velocity.y, abs=1e-6)
    assert new_state.velocity.z == pytest.approx(state.velocity.z, abs=1e-6)

if __name__ == "__main__":
    pytest.main([__file__, "-v"])