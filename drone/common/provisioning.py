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
# Used when /etc is not writable, which is the normal case: drone-api runs as the
# install user, not root. Kept as a module constant so it can be pointed elsewhere
# in tests.
DRONE_ID_FALLBACK_FILE = Path(__file__).parent / ".drone-id"

# Setup token is derived from the hotspot password for authentication
_setup_token: Optional[str] = None


def _read_drone_id(path) -> Optional[str]:
    """Read a stored drone ID, treating an empty or unreadable file as absent.

    An empty file previously yielded "" and was returned as the drone's identity,
    which would have it publish to drone//... topics.
    """
    try:
        value = Path(path).read_text().strip()
    except OSError:
        return None
    return value or None


def get_or_create_drone_id(configured_id: Optional[str] = None) -> str:
    """Resolve this drone's stable identity.

    In order: config.yaml's drone_id, /etc/drone-id, the fallback file, then a
    freshly generated one which is persisted to the first writable location.

    The identity has to survive restarts. It is what the app registers, what the
    IoT Thing is named, and what every MQTT topic embeds, so a drone that picks a
    new ID on each boot orphans its registration every time and cannot be
    controlled from the app at all.

    That is what used to happen. The fallback file was written when /etc was not
    writable -- the normal case, since the service does not run as root -- but
    nothing ever read it back, so every restart minted a new ID.
    """
    if configured_id is not None:
        configured_id = str(configured_id).strip()
    if configured_id:
        stored = _read_drone_id(DRONE_ID_FILE) or _read_drone_id(DRONE_ID_FALLBACK_FILE)
        if stored and stored != configured_id:
            # Worth shouting about: config.yaml gets copied between drones far more
            # readily than /etc/drone-id does, and two aircraft sharing an ID share
            # a command topic.
            logger.warning(
                "config.yaml drone_id %s overrides the ID stored on this device (%s). "
                "If this config was copied from another drone, both will answer on the "
                "same command topic.", configured_id, stored)
        else:
            logger.info("Drone ID %s (from config.yaml)", configured_id)
        return configured_id

    for path in (DRONE_ID_FILE, DRONE_ID_FALLBACK_FILE):
        existing = _read_drone_id(path)
        if existing:
            logger.info("Drone ID %s (from %s)", existing, path)
            return existing

    drone_id = f"drone-{uuid.uuid4().hex[:12]}"
    for path in (DRONE_ID_FILE, DRONE_ID_FALLBACK_FILE):
        try:
            Path(path).write_text(drone_id + "\n")
            logger.info("Generated new drone ID %s, stored at %s", drone_id, path)
            return drone_id
        except OSError as exc:
            logger.debug("Could not store drone ID at %s: %s", path, exc)

    # Nowhere to persist it: say so loudly rather than churning silently, which is
    # how this went unnoticed before.
    logger.error(
        "Generated drone ID %s but could not persist it to %s or %s. It will change "
        "on the next restart and any registration made with it will be orphaned.",
        drone_id, DRONE_ID_FILE, DRONE_ID_FALLBACK_FILE)
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

