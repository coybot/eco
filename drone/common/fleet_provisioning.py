#!/usr/bin/env python3
"""
Fleet Provisioning Client - Bootstrap drone with unique IoT certificate.

Uses AWS IoT Fleet Provisioning to exchange a claim certificate for a
unique device certificate. This is secure because the claim cert can
ONLY be used for provisioning, not for normal MQTT operations.

Uses AWS IoT Device SDK v2 for Python.
"""

import json
import os
import time
import uuid
import logging
import threading
from pathlib import Path

import yaml
from awscrt import io, mqtt
from awsiot import mqtt_connection_builder

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

# Configuration
DRONE_DIR = Path(__file__).parent.absolute()
CERTS_DIR = DRONE_DIR / 'certs'
CONFIG_PATH = DRONE_DIR / "config.yaml"
TEMPLATE_NAME = 'DroneFleetProvisioning'

def load_iot_endpoint() -> str | None:
    """Load IoT Data-ATS endpoint from env or config.yaml."""
    env_endpoint = os.environ.get("IOT_ENDPOINT")
    if env_endpoint:
        return env_endpoint

    if CONFIG_PATH.exists():
        try:
            with open(CONFIG_PATH, "r") as f:
                cfg = yaml.safe_load(f) or {}
            return cfg.get("iot_endpoint")
        except Exception as e:
            logger.warning(f"Failed reading config.yaml for iot_endpoint: {e}")

    return None


class FleetProvisioning:
    """Handle fleet provisioning to get unique device certificate."""
    
    def __init__(self):
        self.claim_cert = CERTS_DIR / 'claim-cert.pem'
        self.claim_key = CERTS_DIR / 'claim-private.key'
        self.root_ca = CERTS_DIR / 'root-ca.pem'
        
        self.device_cert = CERTS_DIR / 'device.pem'
        self.device_key = CERTS_DIR / 'private.key'
        
        self.cert_response = None
        self.provision_response = None
        self.error = None
        
        # For SDK v2 async handling
        self._response_event = threading.Event()
        self._response_data = None
        self._response_error = None
    
    def is_provisioned(self) -> bool:
        """Check if device already has its own certificate."""
        return self.device_cert.exists() and self.device_key.exists()
    
    def provision(self) -> bool:
        """Run fleet provisioning to get unique certificate."""
        if self.is_provisioned():
            logger.info("Device already provisioned, skipping")
            return True
        
        if not self.claim_cert.exists():
            logger.error(f"Claim certificate not found: {self.claim_cert}")
            return False
        
        logger.info("Starting fleet provisioning...")

        endpoint = load_iot_endpoint()
        if not endpoint:
            logger.error("Missing IoT endpoint. Set IOT_ENDPOINT env var or add iot_endpoint to config.yaml")
            return False
        
        # Generate unique serial number for this device
        serial = uuid.uuid4().hex[:12]
        logger.info(f"Device serial: {serial}")
        
        # Initialize SDK v2
        event_loop_group = io.EventLoopGroup(1)
        host_resolver = io.DefaultHostResolver(event_loop_group)
        client_bootstrap = io.ClientBootstrap(event_loop_group, host_resolver)
        
        # Create MQTT connection with claim cert
        client_id = f"provision-{serial}"
        
        mqtt_connection = mqtt_connection_builder.mtls_from_path(
            endpoint=endpoint,
            port=8883,
            cert_filepath=str(self.claim_cert),
            pri_key_filepath=str(self.claim_key),
            ca_filepath=str(self.root_ca),
            client_bootstrap=client_bootstrap,
            client_id=client_id,
            clean_session=True,
            keep_alive_secs=30
        )
        
        try:
            # Connect
            logger.info("Connecting to IoT Core...")
            connect_future = mqtt_connection.connect()
            connect_future.result()
            logger.info("Connected!")
            
            # Step 1: Create certificate from CSR (or get new keys)
            # Using CreateKeysAndCertificate for simplicity
            cert_data = self._subscribe_and_wait(
                mqtt_connection,
                "$aws/certificates/create/json/accepted",
                "$aws/certificates/create/json/rejected",
                "$aws/certificates/create/json",
                {}
            )
            
            if not cert_data:
                logger.error(f"Failed to create certificate: {self._response_error}")
                return False
            
            self.cert_response = cert_data
            
            # Save the new certificate
            cert_pem = cert_data['certificatePem']
            private_key = cert_data['privateKey']
            cert_id = cert_data['certificateId']
            cert_ownership_token = cert_data['certificateOwnershipToken']
            
            logger.info(f"Got certificate: {cert_id[:16]}...")
            
            # Step 2: Register thing using the provisioning template
            provision_data = self._subscribe_and_wait(
                mqtt_connection,
                f"$aws/provisioning-templates/{TEMPLATE_NAME}/provision/json/accepted",
                f"$aws/provisioning-templates/{TEMPLATE_NAME}/provision/json/rejected",
                f"$aws/provisioning-templates/{TEMPLATE_NAME}/provision/json",
                {
                    "certificateOwnershipToken": cert_ownership_token,
                    "parameters": {
                        "SerialNumber": serial
                    }
                }
            )
            
            if not provision_data:
                logger.error(f"Failed to provision device: {self._response_error}")
                return False
            
            self.provision_response = provision_data
            thing_name = provision_data['thingName']
            logger.info(f"Provisioned as: {thing_name}")
            
            # Save the certificate and key
            with open(self.device_cert, 'w') as f:
                f.write(cert_pem)
            with open(self.device_key, 'w') as f:
                f.write(private_key)
            
            # Save thing name to a file for the daemon
            with open(CERTS_DIR / 'thing-name.txt', 'w') as f:
                f.write(thing_name)
            
            logger.info("Fleet provisioning complete!")
            return True
            
        except Exception as e:
            logger.error(f"Provisioning failed: {e}")
            self.error = str(e)
            return False
        finally:
            try:
                disconnect_future = mqtt_connection.disconnect()
                disconnect_future.result()
            except:
                pass
    
    def _subscribe_and_wait(self, connection, accept_topic, reject_topic, publish_topic, payload):
        """Subscribe to response topics, publish request, wait for response."""
        self._response_event.clear()
        self._response_data = None
        self._response_error = None
        
        def on_accept(topic, payload_bytes, **kwargs):
            try:
                self._response_data = json.loads(payload_bytes)
            except Exception as e:
                self._response_error = str(e)
            self._response_event.set()
        
        def on_reject(topic, payload_bytes, **kwargs):
            try:
                data = json.loads(payload_bytes)
                self._response_error = data.get('errorMessage', 'Rejected')
            except:
                self._response_error = 'Rejected'
            self._response_event.set()
        
        # Subscribe to accept topic
        subscribe_future, _ = connection.subscribe(
            topic=accept_topic,
            qos=mqtt.QoS.AT_LEAST_ONCE,
            callback=on_accept
        )
        subscribe_future.result()
        
        # Subscribe to reject topic
        subscribe_future, _ = connection.subscribe(
            topic=reject_topic,
            qos=mqtt.QoS.AT_LEAST_ONCE,
            callback=on_reject
        )
        subscribe_future.result()
        
        time.sleep(0.5)
        
        # Publish
        publish_future, _ = connection.publish(
            topic=publish_topic,
            payload=json.dumps(payload),
            qos=mqtt.QoS.AT_LEAST_ONCE
        )
        publish_future.result()
        
        # Wait for response
        if not self._response_event.wait(timeout=30):
            self._response_error = "Timeout waiting for response"
            return None
        
        return self._response_data


def main():
    """Run fleet provisioning."""
    fp = FleetProvisioning()
    
    if fp.is_provisioned():
        print("✓ Device already provisioned")
        thing_name_file = CERTS_DIR / 'thing-name.txt'
        if thing_name_file.exists():
            print(f"  Thing name: {thing_name_file.read_text().strip()}")
        return 0
    
    print("Starting fleet provisioning...")
    if fp.provision():
        print("✓ Fleet provisioning successful!")
        return 0
    else:
        print(f"✗ Fleet provisioning failed: {fp.error}")
        return 1


if __name__ == "__main__":
    exit(main())
