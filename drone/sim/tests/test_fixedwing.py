#!/usr/bin/env python3
"""
Test script for FixedWingManager functionality.
This demonstrates the API and shows how the fixed-wing system would work.
"""

import json
import sys
import os

# Add the depot client path
sys.path.insert(0, os.path.join(os.path.dirname(__file__), 'eco/rover/sim'))

from depot_client import DepotClient

def test_fixedwing_api():
    """Test the FixedWingManager API through DepotClient."""
    
    print("=== FixedWingManager API Test ===")
    
    try:
        # Connect to the IPC server (assumes Godot is running with the FixedWingManager)
        client = DepotClient()
        print("Connected to Depot IPC server")
        
        # Test 1: Spawn a fixed-wing
        print("\n1. Spawning fixed-wing...")
        result = client.fw_spawn("fw001", (0.0, 0.0, 10.0), 0.0)
        print(f"Spawn result: {result}")
        
        # Test 2: Get state
        print("\n2. Getting fixed-wing state...")
        state = client.fw_state("fw001")
        print(f"State: {json.dumps(state, indent=2)}")
        
        # Test 3: Drive the fixed-wing
        print("\n3. Driving fixed-wing...")
        result = client.fw_drive("fw001", 20.0, 0.1)
        print(f"Drive result: {result}")
        
        # Test 4: Get detection
        print("\n4. Getting detection data...")
        detections = client.fw_detect("fw001")
        print(f"Detections count: {len(detections)}")
        if detections:
            print(f"First detection: {detections[0]}")
        
        # Test 5: Get grid
        print("\n5. Getting occupancy grid...")
        grid = client.fw_grid("fw001")
        if grid:
            print(f"Grid: res={grid['res']}, origin={grid['origin']}, w={grid['w']}, h={grid['h']}")
        
        # Test 6: Get events
        print("\n6. Getting events...")
        events = client.fw_events("fw001")
        print(f"Events count: {len(events)}")
        
        # Test 7: Stop the fixed-wing
        print("\n7. Stopping fixed-wing...")
        result = client.fw_stop("fw001")
        print(f"Stop result: {result}")
        
        # Test 8: Reset
        print("\n8. Resetting fixed-wing...")
        result = client.fw_reset("fw001")
        print(f"Reset result: {result}")
        
        # Test 9: Despawn
        print("\n9. Despawning fixed-wing...")
        result = client.fw_despawn("fw001")
        print(f"Despawn result: {result}")
        
        print("\n=== Test Completed Successfully ===")
        
    except Exception as e:
        print(f"Error during test: {e}")
        return False
    
    return True

if __name__ == "__main__":
    success = test_fixedwing_api()
    sys.exit(0 if success else 1)