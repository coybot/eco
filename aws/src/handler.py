import json
import os

import llm
import clients

AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6"
)
ANTHROPIC_VERSION = "bedrock-2023-05-31"
iot = clients.get_iot()
dynamodb = clients.get_dynamodb()

DRONE_TABLE = os.environ.get('DRONE_TABLE', 'drone-registry-dev')

QUADCOPTER_SYSTEM_PROMPT = """You generate Python code for quadcopter drone control. The code runs on a drone with these SDK functions pre-imported:

AVAILABLE FUNCTIONS (use these, do NOT import anything):
- motor_test(motor_num=None, throttle_pct=15, duration_sec=2) - Test motor 1-4 (or all if no motor_num)
- arm() - Arm motors (spin up)
- land() - Controlled stop: descends if airborne, then cuts motors. ALWAYS use this to stop motors.
- takeoff(altitude_m) - Fly to altitude (ONLY use outdoors when explicitly asked to fly)
- goto(lat, lon, alt) - Fly to GPS position
- set_velocity(vx, vy, vz) - Set velocity m/s
- set_yaw(angle_deg, relative=False) - Set heading
- wait(seconds) - Pause execution
- get_position() - Returns (lat, lon, alt_m) where alt_m is relative altitude above home
- get_attitude() - Returns (roll, pitch, yaw) degrees
- get_ceiling_distance() - Returns meters to ceiling (upward rangefinder), or None if unavailable
- start_ceiling_guard(min_clearance=0.5) - Starts background thread that freezes altitude if ceiling clearance drops below threshold
- stop_ceiling_guard() - Stops the ceiling guard
- capture_photo() - Take a photo and return local path
- look_around(directions=4) - Pan and take photos in N directions, returns list of paths
- start_recording(mode="video", fps=6, interval_s=3, max_seconds=120) - Begin recording in the BACKGROUND and return immediately; flight commands after this run while it records
- stop_recording() - Stop, upload, and return the list of media URLs (print them)
- record_video(seconds=10, fps=6) - Blocking one-shot clip, uploads and returns the URL
- battery_status() - Battery %, pack voltage, can_takeoff/takeoff_reason, actions_left, and any battery-sensor warnings. Print it when asked about battery, status or readiness
- battery_diagnostics() - Reads the flight controller's battery-monitor setup and returns findings on what is misconfigured (print it)
- calibrate_voltage(full_charge=True | use_esc=True | reference_v=V) - Fix the battery voltage reading without a meter; disarmed only
- calibrate_current(charger_mah) - Fix the current sensor scale from the mAh the charger put back after the last flight

PRE-DEFINED VARIABLES (always available, do NOT redefine):
- home_lat, home_lon, home_alt — GPS position captured at command time (alt relative to home)
- CONVERSATION_ID — for capture_photo()

RULES:
1. Use ONLY these SDK functions - they handle MAVLink internally
2. Do NOT import anything
3. For motor tests, use motor_test() to test all motors, or motor_test(1) for a specific motor
4. "disarm", "stop motors", "power off" always means land(). NEVER use safe_disarm().
5. To just arm and disarm: arm() → wait(N) → land()
6. NEVER call takeoff() unless the user explicitly asks to fly or take off outdoors
7. For flight (only outdoors, only when asked): arm() → takeoff() → ... → land()
8. Keep altitude under 20m. Always use start_ceiling_guard() before takeoff.
9. When asked to "see" or "look", use look_around() or capture_photo()
10. When the user asks for a VIDEO, or for pictures taken WHILE moving, bracket the flight with start_recording() ... stop_recording() and print(stop_recording()) — printing the returned URLs is how the media reaches the user, so recording without printing them delivers nothing. Use mode="photos" for stills at an interval. For one still where the vehicle is already standing, use capture_photo() instead.
11. takeoff(), goto() and start_recording() REFUSE (return False and print why) when the battery cannot cover them plus the trip home. Check the result: if takeoff() returns False, stop and print battery_status(); if goto() returns False while flying, land(). The drone also returns home or lands by itself when the battery runs low - do not fight it.

Output ONLY Python code. No markdown, no imports, no comments."""

ROVER_SYSTEM_PROMPT = """You generate Python code for ground rover control. The code runs on a rover with these SDK functions pre-imported:

AVAILABLE FUNCTIONS (use these, do NOT import anything):
- arm() - Enable rover motion
- disarm() - Stop motion and disable rover (alias: safe_disarm())
- safe_disarm() - Same as disarm()
- stop() - Immediately stop all motion
- drive(speed_mps=0.5, duration_sec=1.0) - Drive forward (positive) or backward (negative) for duration
- turn(angle_deg, speed_rad_s=0.5) - Turn in place by degrees (positive=left/CCW)
- set_velocity(vx, vy=0.0, omega=0.0) - Set continuous velocity: vx=forward m/s, omega=angular rad/s
- goto(lat, lon) - Navigate to GPS coordinates using Nav2 (blocks until arrived)
- wait(seconds) - Pause execution
- get_position() - Returns (lat, lon, alt_m) from GPS or odometry
- get_attitude() - Returns (roll, pitch, yaw) degrees
- capture_photo() - Take a photo and return URL
- look_around(directions=4) - Rotate and take photos in N directions, returns list of URLs
- start_recording(mode="video", fps=6, interval_s=3, max_seconds=120) - Begin recording in the BACKGROUND and return immediately; flight commands after this run while it records
- stop_recording() - Stop, upload, and return the list of media URLs (print them)
- record_video(seconds=10, fps=6) - Blocking one-shot clip, uploads and returns the URL

PRE-DEFINED VARIABLES (always available, do NOT redefine):
- home_lat, home_lon, home_alt — GPS position captured at command time
- CONVERSATION_ID — for capture_photo()

RULES:
1. Use ONLY these SDK functions — do NOT import anything
2. Max speed is 1.0 m/s. Max angular speed is 1.57 rad/s (90 deg/s).
3. For motion: arm() first, then drive()/turn()/set_velocity()/goto(), then disarm() when done
4. "stop" or "halt" means stop(). "disarm" means disarm() or safe_disarm() — both work.
5. Do NOT use takeoff(), land(), set_yaw(), motor_test() — those are quadcopter-only
6. When asked to "see", "look", or "what do you see", use look_around() or capture_photo()
7. When the user asks for a VIDEO, or for pictures taken WHILE moving, bracket the flight with start_recording() ... stop_recording() and print(stop_recording()) — printing the returned URLs is how the media reaches the user, so recording without printing them delivers nothing. Use mode="photos" for stills at an interval. For one still where the vehicle is already standing, use capture_photo() instead.
7. For timed motion, use drive(speed, duration) or set_velocity() + wait() + stop()

Output ONLY Python code. No markdown, no imports, no comments."""

FIXEDWING_SYSTEM_PROMPT = """You generate Python code for fixed-wing drone control. The code runs on a plane with these SDK functions pre-imported:

AVAILABLE FUNCTIONS (use these, do NOT import anything):
- arm() - Arm the motor
- takeoff(altitude_m) - Climb out to altitude (runway/hand launch happens before the command reaches you)
- land() - Fly the landing approach and cut the motor. ALWAYS use this to end a flight.
- goto(lat, lon, alt) - Fly to a GPS position (use a sequence of goto() calls to trace a circle/orbit — see RULES)
- set_velocity(vx, vy, vz) - Set airspeed/climb command; vx is forward airspeed, NEVER 0
- set_yaw(angle_deg, relative=False) - Set heading (turns are gradual, banked — not a spin in place)
- wait(seconds) - Pause execution
- get_position() - Returns (lat, lon, alt_m) where alt_m is relative altitude above home
- get_attitude() - Returns (roll, pitch, yaw) degrees
- capture_photo() - Take a photo and return local path
- look_around(directions=4) - Pan and take photos in N directions, returns list of paths
- start_recording(mode="video", fps=6, interval_s=3, max_seconds=120) - Begin recording in the BACKGROUND and return immediately; flight commands after this run while it records
- stop_recording() - Stop, upload, and return the list of media URLs (print them)
- record_video(seconds=10, fps=6) - Blocking one-shot clip, uploads and returns the URL

PRE-DEFINED VARIABLES (always available, do NOT redefine):
- home_lat, home_lon, home_alt — GPS position captured at command time (alt relative to home)
- CONVERSATION_ID — for capture_photo()

RULES:
1. Use ONLY these SDK functions - they handle MAVLink internally
2. Do NOT import anything
3. A fixed-wing plane CANNOT hover or stop moving — never set forward velocity to 0 and never pause mid-air waiting in place; use wait() only on the ground before arm()/after land()
4. To orbit or circle a point, issue a sequence of goto() waypoints around it (do NOT expect a single call to loiter)
5. Turns are gradual and banked — do not call set_yaw() for a sharp in-place turn like a rover/quad would
6. "land", "stop", "power off" always means land(). There is no in-air stop.
7. For flight: arm() → takeoff(altitude_m) → ... → land()
8. Do NOT use motor_test(), start_ceiling_guard(), or set_yaw() for anything but gentle heading changes — those are quadcopter/rover-only or inappropriate for a plane
9. When asked to "see" or "look", use look_around() or capture_photo()
10. When the user asks for a VIDEO, or for pictures taken WHILE moving, bracket the flight with start_recording() ... stop_recording() and print(stop_recording()) — printing the returned URLs is how the media reaches the user, so recording without printing them delivers nothing. Use mode="photos" for stills at an interval. For one still where the vehicle is already standing, use capture_photo() instead.

Output ONLY Python code. No markdown, no imports, no comments."""


def get_system_prompt(drone_id):
    """Return the appropriate system prompt based on the drone's vehicle type."""
    table = dynamodb.Table(DRONE_TABLE)
    try:
        response = table.scan(
            FilterExpression='droneId = :d',
            ExpressionAttributeValues={':d': drone_id}
        )
        items = response.get('Items', [])
        if items:
            vehicle_type = items[0].get('vehicleType', 'quadcopter')
            if vehicle_type == 'rover':
                return ROVER_SYSTEM_PROMPT
            if vehicle_type == 'fixedwing':
                return FIXEDWING_SYSTEM_PROMPT
    except Exception:
        pass
    return QUADCOPTER_SYSTEM_PROMPT


def get_user_id(event):
    """Extract user ID from Lambda authorizer context."""
    try:
        authorizer = event['requestContext']['authorizer']
        # Lambda authorizer puts userId in context
        return authorizer.get('userId') or authorizer.get('principalId')
    except (KeyError, TypeError):
        return None


def verify_ownership(user_id, drone_id):
    """Verify that the user owns the specified drone."""
    table = dynamodb.Table(DRONE_TABLE)
    try:
        response = table.get_item(
            Key={'userId': user_id, 'droneId': drone_id}
        )
        return 'Item' in response
    except Exception:
        return False


def json_response(status_code, body):
    """Create API Gateway response with CORS headers."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        },
        'body': json.dumps(body)
    }


def lambda_handler(event, context):
    """Handle API Gateway request, generate code, send to drone."""
    
    # Get authenticated user
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})
    
    # Parse request
    try:
        body = json.loads(event.get('body', '{}'))
    except json.JSONDecodeError:
        return json_response(400, {'error': 'Invalid JSON'})
    
    drone_id = body.get('drone_id', body.get('droneId'))
    command = body.get('command', '')
    
    if not drone_id:
        return json_response(400, {'error': 'Missing drone_id'})
    
    if not command:
        return json_response(400, {'error': 'Missing command'})
    
    # Verify user owns this drone
    if not verify_ownership(user_id, drone_id):
        return json_response(403, {'error': 'You do not own this drone'})
    
    # Generate code with the configured LLM (Bedrock by default, or a local
    # GCS model when LLM_PROVIDER=openai)
    try:
        system_prompt = get_system_prompt(drone_id)
        messages = [
            {"role": "user", "content": [{"type": "text", "text": f"Command: {command}"}]}
        ]
        result = llm.invoke(system_prompt, messages, max_tokens=1024, model=BEDROCK_MODEL_ID)
        code = ''.join(
            block.get('text', '') for block in result.get('content', [])
            if block.get('type') == 'text'
        ).strip()
        
        # Clean up code (remove markdown if present)
        if code.startswith('```'):
            lines = code.split('\n')
            code = '\n'.join(lines[1:-1] if lines[-1] == '```' else lines[1:])
        
    except Exception as e:
        return json_response(500, {'error': f'LLM error: {str(e)}'})
    
    # Publish to drone via IoT Core
    try:
        iot.publish(
            topic=f'drone/{drone_id}/command',
            qos=1,
            payload=json.dumps({
                'code': code,
                'original_command': command,
                'drone_id': drone_id,
                'user_id': user_id
            })
        )
    except Exception as e:
        return json_response(500, {'error': f'IoT publish error: {str(e)}'})
    
    return json_response(200, {
            'status': 'sent',
            'drone_id': drone_id,
            'original_command': command,
            'generated_code': code
        })
