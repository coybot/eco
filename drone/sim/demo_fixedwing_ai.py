#!/usr/bin/env python3
"""
FixedWing AI Integration Demo
Shows how the FixedWingManager would work with AI decision making.
"""

import json
import sys
import time
import random
from depot_client import DepotClient

class FixedWingAIDemo:
    def __init__(self):
        self.client = DepotClient()
        self.fw_id = "demo-fw-001"
        
    def demo_flight_pattern(self):
        """Demonstrate a simple flight pattern with AI-like decision making."""
        print("=== FixedWing AI Integration Demo ===\n")
        
        # Spawn fixed-wing
        print("1. Spawning fixed-wing...")
        self.client.fw_spawn(self.fw_id, (0.0, 0.0, 20.0), 0.0)
        time.sleep(0.5)
        
        # Simulate AI decision making loop
        for step in range(5):
            print(f"\n--- Step {step + 1} ---")
            
            # Get current state
            state = self.client.fw_state(self.fw_id)
            if not state.get('ok'):
                print("Failed to get state")
                continue
                
            print(f"Position: ({state['position'][0]:.1f}, {state['position'][1]:.1f}, {state['position'][2]:.1f})")
            print(f"Airspeed: {state['airspeed']:.1f} m/s")
            print(f"Altitude: {state['altitude']:.1f} m")
            
            # Get detection data (simulating AI perception)
            detections = self.client.fw_detect(self.fw_id)
            print(f"Detections: {len(detections)} objects detected")
            
            # Simple AI decision making
            if len(detections) > 0:
                # If obstacle detected, adjust flight path
                target_yaw = random.uniform(-0.2, 0.2)  # Gentle turn
                target_airspeed = min(state['airspeed'] + random.uniform(-1, 1), 25.0)
                print(f"AI Decision: Adjusting course, airspeed to {target_airspeed:.1f}")
            else:
                # Otherwise maintain course
                target_yaw = 0.0
                target_airspeed = 20.0  # Maintain cruising speed
                print("AI Decision: Continuing straight, maintaining speed")
                
            # Execute AI action
            self.client.fw_drive(self.fw_id, target_airspeed, target_yaw)
            print("Action executed")
            
            time.sleep(1)
            
        # Final state
        final_state = self.client.fw_state(self.fw_id)
        print(f"\nFinal Position: ({final_state['position'][0]:.1f}, {final_state['position'][1]:.1f}, {final_state['position'][2]:.1f})")
        print(f"Final Airspeed: {final_state['airspeed']:.1f} m/s")
        print(f"Final Battery: {final_state['battery_level']:.1f}%")
        
        # Clean up
        print("\nCleaning up...")
        self.client.fw_despawn(self.fw_id)
        print("Demo completed!")
        
    def demo_autonomous_flight(self):
        """Demonstrate autonomous flight with obstacle avoidance."""
        print("\n=== Autonomous Flight Demo ===\n")
        
        # Spawn with a more interesting initial position
        self.client.fw_spawn(self.fw_id, (0.0, 0.0, 30.0), 0.0)
        time.sleep(0.5)
        
        # Simulated mission: fly to a target while avoiding obstacles
        target_positions = [
            (20.0, 20.0, 30.0),
            (40.0, 0.0, 25.0),
            (20.0, -20.0, 20.0),
            (-20.0, -20.0, 25.0)
        ]
        
        for i, target in enumerate(target_positions):
            print(f"\nTarget {i+1}: {target}")
            
            # Get current state
            state = self.client.fw_state(self.fw_id)
            current_pos = state['position']
            
            # Calculate heading to target
            dx = target[0] - current_pos[0]
            dy = target[1] - current_pos[1]
            target_yaw = 0.0  # Simplified for demo
            
            # Maintain speed
            target_airspeed = 20.0
            
            # Execute flight command
            self.client.fw_drive(self.fw_id, target_airspeed, target_yaw)
            print(f"Flight command: airspeed={target_airspeed}, yaw_rate={target_yaw}")
            
            # Check for obstacles
            detections = self.client.fw_detect(self.fw_id)
            if detections:
                print(f"Obstacle detected! {len(detections)} items in view")
                # Simulate obstacle avoidance
                avoidance_yaw = random.uniform(-0.3, 0.3)
                self.client.fw_drive(self.fw_id, target_airspeed, avoidance_yaw)
                print(f"Executing avoidance maneuver: yaw_rate={avoidance_yaw}")
                
            time.sleep(2)
            
        # Return to base
        print("\nReturning to base...")
        self.client.fw_drive(self.fw_id, 15.0, 0.0)
        time.sleep(2)
        
        # Final state
        final_state = self.client.fw_state(self.fw_id)
        print(f"\nFinal Position: ({final_state['position'][0]:.1f}, {final_state['position'][1]:.1f}, {final_state['position'][2]:.1f})")
        print(f"Final Battery: {final_state['battery_level']:.1f}%")
        
        # Cleanup
        self.client.fw_despawn(self.fw_id)
        print("Autonomous flight demo completed!")

def main():
    demo = FixedWingAIDemo()
    
    try:
        # Run the basic demo
        demo.demo_flight_pattern()
        
        # Run the autonomous flight demo
        demo.demo_autonomous_flight()
        
        print("\n" + "="*50)
        print("DEMO SUCCESSFUL!")
        print("The FixedWingManager implements:")
        print("- Full flight dynamics (speed, yaw, altitude)")
        print("- Obstacle detection with 80m range")
        print("- Occupancy grid mapping")
        print("- Battery management")
        print("- Event logging")
        print("- API-ready for AI integration")
        print("="*50)
        
    except Exception as e:
        print(f"Demo failed with error: {e}")
        return 1
        
    return 0

if __name__ == "__main__":
    sys.exit(main())