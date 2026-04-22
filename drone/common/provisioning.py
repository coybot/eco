#!/usr/bin/env python3
"""
Drone Provisioning Service

Handles initial WiFi setup via a temporary secure hotspot.
Flow:
1. Boot with no WiFi configured → start WPA2/WPA3 AP for 60 seconds
2. User connects to AP using the displayed password
3. User sends WiFi credentials via HTTP with setup token from hotspot password
4. Drone saves credentials, connects to WiFi, registers with cloud
"""

import json
import logging
import os
import time
import threading
import uuid
import secrets
import hmac
from http.server import HTTPServer, BaseHTTPRequestHandler
from pathlib import Path
from typing import Optional, Callable

logger = logging.getLogger(__name__)

# Configuration
PROVISIONING_TIMEOUT = 300  # 5 minutes
PROVISIONING_PORT = 80
DRONE_ID_FILE = "/etc/drone-id"

# Setup token is derived from the hotspot password for authentication
_setup_token: Optional[str] = None


def get_or_create_drone_id() -> str:
    """Get existing drone ID or generate a new one."""
    if os.path.exists(DRONE_ID_FILE):
        with open(DRONE_ID_FILE, 'r') as f:
            return f.read().strip()
    
    # Generate new ID from UUID
    drone_id = f"drone-{uuid.uuid4().hex[:12]}"
    
    # Try to save it (may fail if not root)
    try:
        with open(DRONE_ID_FILE, 'w') as f:
            f.write(drone_id)
    except PermissionError:
        # Save to local config instead
        local_path = Path(__file__).parent / ".drone-id"
        with open(local_path, 'w') as f:
            f.write(drone_id)
    
    logger.info(f"Generated new drone ID: {drone_id}")
    return drone_id


class ProvisioningResult:
    """Result of provisioning attempt."""
    def __init__(self):
        self.success = False
        self.ssid: Optional[str] = None
        self.password: Optional[str] = None
        self.user_id: Optional[str] = None
        self.error: Optional[str] = None


class ProvisioningHandler(BaseHTTPRequestHandler):
    """HTTP handler for provisioning requests."""
    
    # Shared state
    result: Optional[ProvisioningResult] = None
    drone_id: str = ""
    shutdown_event: Optional[threading.Event] = None
    setup_token: str = ""  # Authentication token (hotspot password)
    
    def log_message(self, format, *args):
        """Override to use our logger."""
        logger.info(f"HTTP: {args[0]}")
    
    def _send_response(self, status: int, data: dict):
        """Send JSON response."""
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        # CORS restricted to same-origin during provisioning (local network only)
        self.send_header("Access-Control-Allow-Origin", "http://192.168.4.1")
        self.send_header("Access-Control-Allow-Methods", "GET, POST, OPTIONS")
        self.send_header("Access-Control-Allow-Headers", "Content-Type, Authorization")
        self.end_headers()
        self.wfile.write(json.dumps(data).encode())
    
    def _authorize(self) -> bool:
        """Check Authorization header for setup token."""
        auth = self.headers.get("Authorization", "")
        if auth.startswith("Bearer "):
            token = auth[7:]
            return hmac.compare_digest(token, self.setup_token)
        return False
    
    def do_OPTIONS(self):
        """Handle CORS preflight."""
        self._send_response(200, {})
    
    def do_GET(self):
        """Handle GET requests."""
        if self.path == "/" or self.path == "/status":
            # Return drone info
            self._send_response(200, {
                "status": "provisioning",
                "drone_id": self.drone_id,
                "message": "Ready to receive WiFi configuration"
            })
        elif self.path == "/info":
            # More detailed info
            from wifi_manager import WiFiManager
            wm = WiFiManager()
            self._send_response(200, {
                "drone_id": self.drone_id,
                "mac_address": wm.get_mac_address(),
                "hotspot_name": wm.get_hotspot_name()
            })
        else:
            self._send_response(404, {"error": "Not found"})
    
    def do_POST(self):
        """Handle POST requests."""
        if self.path == "/configure":
            self._handle_configure()
        else:
            self._send_response(404, {"error": "Not found"})
    
    def _handle_configure(self):
        """Handle WiFi configuration request (requires authentication)."""
        # Require authentication with the hotspot password as the Bearer token
        if not self._authorize():
            logger.warning("Unauthorized /configure attempt")
            self._send_response(401, {
                "error": "Unauthorized",
                "message": "Include 'Authorization: Bearer <hotspot_password>' header"
            })
            return
        
        try:
            # Read request body
            content_length = int(self.headers.get('Content-Length', 0))
            body = self.rfile.read(content_length).decode()
            data = json.loads(body)
            
            ssid = data.get("ssid")
            password = data.get("password")
            user_id = data.get("user_id")
            
            if not ssid:
                self._send_response(400, {"error": "Missing ssid"})
                return
            
            if not password:
                self._send_response(400, {"error": "Missing password"})
                return
            
            # Validate SSID length (max 32 chars per WiFi spec)
            if len(ssid) > 32:
                self._send_response(400, {"error": "SSID too long (max 32 chars)"})
                return
            
            # Validate password length (WPA2 requires 8-63 chars)
            if len(password) < 8 or len(password) > 63:
                self._send_response(400, {"error": "Password must be 8-63 characters"})
                return
            
            logger.info(f"Received WiFi config for SSID: {ssid}")
            
            # Store the result
            self.result.ssid = ssid
            self.result.password = password
            self.result.user_id = user_id
            self.result.success = True
            
            # Send success response
            self._send_response(200, {
                "status": "success",
                "message": "Configuration received. Drone will now connect to WiFi.",
                "drone_id": self.drone_id
            })
            
            # Signal to stop the server
            if self.shutdown_event:
                self.shutdown_event.set()
            
        except json.JSONDecodeError:
            self._send_response(400, {"error": "Invalid JSON"})
        except Exception as e:
            logger.error(f"Configuration error: {e}")
            self._send_response(500, {"error": str(e)})


class ProvisioningService:
    """
    WiFi provisioning service for drones.
    
    Usage:
        service = ProvisioningService()
        if service.needs_provisioning():
            result = service.run_provisioning()
            if result.success:
                # WiFi configured, proceed with normal operation
                pass
    """
    
    def __init__(self, timeout: int = PROVISIONING_TIMEOUT):
        self.timeout = timeout
        self.drone_id = get_or_create_drone_id()
        
        # Import here to avoid circular imports
        from wifi_manager import WiFiManager
        self.wifi = WiFiManager()
    
    def needs_provisioning(self) -> bool:
        """Check if provisioning is needed (no WiFi configured)."""
        return not self.wifi.is_wifi_configured()
    
    def run_provisioning(self) -> ProvisioningResult:
        """
        Run the provisioning flow:
        1. Start open hotspot
        2. Run HTTP server for 60 seconds
        3. If credentials received, save them and connect
        """
        result = ProvisioningResult()
        
        logger.info("=" * 50)
        logger.info("STARTING WIFI PROVISIONING")
        logger.info(f"Drone ID: {self.drone_id}")
        logger.info(f"Hotspot: {self.wifi.get_hotspot_name()}")
        logger.info(f"Hotspot password: {self.wifi.get_hotspot_password()}")
        logger.info(f"Timeout: {self.timeout} seconds")
        logger.info("=" * 50)
        
        # Start the hotspot (WPA2/WPA3)
        hotspot_name = self.wifi.get_hotspot_name()
        if not self.wifi.start_hotspot(ssid=hotspot_name, password=None):
            result.error = "Failed to start hotspot"
            logger.error(result.error)
            return result
        
        logger.info(f"Hotspot started: {hotspot_name} (WPA2/WPA3)")
        
        try:
            # Run HTTP server with timeout
            result = self._run_http_server()
            
            if result.success:
                logger.info(f"Received config for SSID: {result.ssid}")
                
                # Stop hotspot
                self.wifi.stop_hotspot()
                
                # Save credentials
                if self.wifi.save_wifi_credentials(result.ssid, result.password):
                    logger.info("WiFi credentials saved")
                    
                    # Regenerate hotspot password for next provisioning session
                    self.wifi.regenerate_hotspot_password()
                    
                    # Connect to WiFi
                    time.sleep(2)
                    if self.wifi.connect_to_wifi(result.ssid):
                        logger.info("Connected to WiFi!")
                        
                        # Wait for IP
                        for _ in range(10):
                            ip = self.wifi.get_ip_address()
                            if ip:
                                logger.info(f"Got IP address: {ip}")
                                break
                            time.sleep(1)
                    else:
                        result.success = False
                        result.error = "Failed to connect to WiFi"
                else:
                    result.success = False
                    result.error = "Failed to save WiFi credentials"
            else:
                logger.info("Provisioning timed out - no configuration received")
                result.error = "Timeout waiting for configuration"
        
        finally:
            # Ensure hotspot is stopped
            self.wifi.stop_hotspot()
        
        return result
    
    def _run_http_server(self) -> ProvisioningResult:
        """Run HTTP server for provisioning with timeout."""
        result = ProvisioningResult()
        shutdown_event = threading.Event()
        
        # Use the hotspot password as the setup token for authentication
        setup_token = self.wifi.get_hotspot_password()
        
        # Configure handler
        ProvisioningHandler.result = result
        ProvisioningHandler.drone_id = self.drone_id
        ProvisioningHandler.shutdown_event = shutdown_event
        ProvisioningHandler.setup_token = setup_token
        
        # Create server
        server = HTTPServer(("0.0.0.0", PROVISIONING_PORT), ProvisioningHandler)
        server.timeout = 1  # Check for shutdown every second
        
        logger.info(f"HTTP server listening on port {PROVISIONING_PORT}")
        logger.info(f"Setup token (hotspot password): {setup_token}")
        
        start_time = time.time()
        
        try:
            while True:
                # Check timeout
                elapsed = time.time() - start_time
                if elapsed >= self.timeout:
                    logger.info("Provisioning timeout reached")
                    break
                
                # Check if we received config
                if shutdown_event.is_set():
                    logger.info("Configuration received, shutting down server")
                    break
                
                # Handle one request (with 1 second timeout)
                server.handle_request()
                
                # Log remaining time every 10 seconds
                remaining = int(self.timeout - elapsed)
                if remaining % 10 == 0 and remaining > 0:
                    logger.info(f"Provisioning window: {remaining} seconds remaining")
        
        finally:
            server.server_close()
        
        return result


def run_provisioning_if_needed(on_complete: Optional[Callable[[ProvisioningResult], None]] = None) -> bool:
    """
    Convenience function to run provisioning if WiFi is not configured.
    
    Returns True if WiFi is now configured (either already was, or just provisioned).
    """
    service = ProvisioningService()
    
    if not service.needs_provisioning():
        logger.info("WiFi already configured, skipping provisioning")
        return True
    
    result = service.run_provisioning()
    
    if on_complete:
        on_complete(result)
    
    return result.success


if __name__ == "__main__":
    # Test provisioning
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s - %(levelname)s - %(message)s'
    )
    
    service = ProvisioningService(timeout=120)  # 2 minutes for testing
    
    print(f"Drone ID: {service.drone_id}")
    print(f"Hotspot will be: {service.wifi.get_hotspot_name()}")
    print(f"Needs provisioning: {service.needs_provisioning()}")
    
    if service.needs_provisioning():
        print("\nStarting provisioning...")
        result = service.run_provisioning()
        print(f"\nResult: success={result.success}, error={result.error}")
    else:
        print("\nWiFi already configured!")

