import json
import os
import boto3

AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6"
)
ANTHROPIC_VERSION = "bedrock-2023-05-31"
bedrock = boto3.client('bedrock-runtime', region_name=AWS_REGION)
iot = boto3.client('iot-data', region_name=AWS_REGION)
dynamodb = boto3.resource('dynamodb')

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
7. For timed motion, use drive(speed, duration) or set_velocity() + wait() + stop()

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
    
    # Generate code with Claude via Bedrock
    try:
        system_prompt = get_system_prompt(drone_id)
        response = bedrock.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps({
                "anthropic_version": ANTHROPIC_VERSION,
                "system": system_prompt,
                "messages": [
                    {"role": "user", "content": [{"type": "text", "text": f"Command: {command}"}]}
                ],
                "max_tokens": 1024
            })
        )
        
        result = json.loads(response['body'].read())
        code = ''.join(
            block.get('text', '') for block in result.get('content', [])
            if block.get('type') == 'text'
        ).strip()
        
        # Clean up code (remove markdown if present)
        if code.startswith('```'):
            lines = code.split('\n')
            code = '\n'.join(lines[1:-1] if lines[-1] == '```' else lines[1:])
        
    except Exception as e:
        return json_response(500, {'error': f'Bedrock error: {str(e)}'})
    
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
