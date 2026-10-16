#!/usr/bin/env python3 -u
"""
Test script to publish simulated telemetry to AWS IoT Core.
Use this for testing the iOS app without actual drone hardware.

Uses AWS IoT Device SDK v2 for Python.
"""

import sys
sys.stdout.reconfigure(line_buffering=True)

import json
import time
import math
import yaml
from pathlib import Path
from datetime import datetime, timezone

from awscrt import io, mqtt
from awsiot import mqtt_connection_builder

# Load config
DRONE_DIR = Path(__file__).parent.absolute()
config_path = DRONE_DIR / 'config.yaml'

with open(config_path, 'r') as f:
    config = yaml.safe_load(f) or {}

# Override for testing - use the actual registered drone
DRONE_ID = 'drone-90a0ce828131'
IOT_ENDPOINT = config.get('iot_endpoint')

# Certificate paths
cert_dir = DRONE_DIR / 'certs'
root_ca = cert_dir / 'root-ca.pem'
private_key = cert_dir / 'private.key'
certificate = cert_dir / 'device.pem'


def get_simulated_telemetry():
    """Generate simulated telemetry."""
    base_lat = 37.7749  # San Francisco
    base_lon = -122.4194
    t = time.time()
    
    return {
        'position': {
            'latitude': base_lat + 0.0001 * math.sin(t / 30),
            'longitude': base_lon + 0.0001 * math.cos(t / 30),
            'altitude': 10.0 + 2.0 * math.sin(t / 10)
        },
        'attitude': {
            'roll': 2.0 * math.sin(t / 5),
            'pitch': 1.5 * math.cos(t / 7),
            'yaw': (t * 5) % 360 - 180
        },
        'battery': max(20, 85 - (t % 3600) / 60),
        'armed': False,
        'mode': 'STABILIZE'
    }


def main():
    print(f"🚁 Starting telemetry simulator for {DRONE_ID}")
    print(f"📡 IoT Endpoint: {IOT_ENDPOINT}")
    
    # Check certs
    for path in [root_ca, private_key, certificate]:
        if not path.exists():
            print(f"❌ Missing certificate: {path}")
            return
    
    # Initialize SDK v2
    event_loop_group = io.EventLoopGroup(1)
    host_resolver = io.DefaultHostResolver(event_loop_group)
    client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)
    
    # Create MQTT connection
    client_id = f"{DRONE_ID}-test-{int(time.time())}"
    
    mqtt_connection = mqtt_connection_builder.mtls_from_path(
        endpoint=IOT_ENDPOINT,
        port=8883,
        cert_filepath=str(certificate),
        pri_key_filepath=str(private_key),
        ca_filepath=str(root_ca),
        client_bootstrap=client_bootstrap,
        client_id=client_id,
        clean_session=True,
        keep_alive_secs=30
    )
    
    print("🔌 Connecting to AWS IoT Core...")
    connect_future = mqtt_connection.connect()
    connect_future.result()
    print("✅ Connected!")
    
    status_topic = f"drone/{DRONE_ID}/status"
    print(f"📤 Publishing to: {status_topic}")
    print("Press Ctrl+C to stop\n")
    
    try:
        while True:
            telemetry = get_simulated_telemetry()
            
            heartbeat = {
                'droneId': DRONE_ID,
                'status': 'online',
                'lastUpdate': datetime.now(timezone.utc).isoformat(),
                'ttl': int(time.time()) + 10,
                **telemetry
            }
            
            mqtt_connection.publish(
                topic=status_topic,
                payload=json.dumps(heartbeat),
                qos=mqtt.QoS.AT_MOST_ONCE
            )
            
            print(f"📡 Published: battery={telemetry['battery']:.1f}%, "
                  f"alt={telemetry['position']['altitude']:.1f}m, "
                  f"yaw={telemetry['attitude']['yaw']:.1f}°")
            
            time.sleep(5)  # Heartbeat every 5 seconds
            
    except KeyboardInterrupt:
        print("\n👋 Stopping...")
        disconnect_future = mqtt_connection.disconnect()
        disconnect_future.result()
        print("Disconnected")


if __name__ == "__main__":
    main()
