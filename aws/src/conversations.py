"""
Conversation handlers for drone chat functionality.

Implements an AI agent that can:
1. Understand natural language requests
2. Decide when to execute drone commands vs ask clarifying questions
3. Handle image analysis for visual queries
4. Manage multi-turn conversations with context
"""

import json
import os
import uuid
import base64
import urllib.request
from datetime import datetime, timezone
from decimal import Decimal

import boto3
from botocore.config import Config as BotoConfig

# AWS clients
dynamodb = boto3.resource('dynamodb')
AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"
STATUS_TTL_SECONDS = int(os.environ.get("STATUS_TTL_SECONDS", "30"))

# Bedrock model - Claude 4.5 Sonnet via cross-region inference profile
BEDROCK_MODEL_ID = os.environ.get(
    "BEDROCK_MODEL_ID", "us.anthropic.claude-sonnet-4-6"
)
# Fallback model for code generation (faster, cheaper)
BEDROCK_CODE_MODEL_ID = os.environ.get(
    "BEDROCK_CODE_MODEL_ID", "anthropic.claude-3-haiku-20240307-v1:0"
)
ANTHROPIC_VERSION = "bedrock-2023-05-31"
bedrock = boto3.client('bedrock-runtime', region_name=AWS_REGION)
iot = boto3.client('iot-data', region_name=AWS_REGION)
s3 = boto3.client(
    's3', 
    region_name=AWS_REGION,
    config=BotoConfig(signature_version='s3v4')
)

# Pre-signed URL expiration (24 hours)
PRESIGNED_URL_EXPIRATION = 86400


def generate_presigned_url(s3_url: str) -> str:
    """Convert an S3 URL to a pre-signed URL for temporary access."""
    if not s3_url or not s3_url.startswith('https://'):
        return s3_url
    
    try:
        # Parse S3 URL: https://bucket.s3.amazonaws.com/key or https://bucket.s3.region.amazonaws.com/key
        # Remove protocol
        url_path = s3_url.replace('https://', '')
        
        # Extract bucket and key
        parts = url_path.split('/', 1)
        if len(parts) < 2:
            return s3_url
        
        domain = parts[0]
        key = parts[1]
        
        # Extract bucket name from domain (bucket.s3.amazonaws.com or bucket.s3.region.amazonaws.com)
        bucket = domain.split('.s3')[0]
        
        # Generate pre-signed URL (using regional endpoint)
        presigned = s3.generate_presigned_url(
            'get_object',
            Params={'Bucket': bucket, 'Key': key},
            ExpiresIn=PRESIGNED_URL_EXPIRATION
        )
        return presigned
    except Exception as e:
        print(f"Error generating presigned URL for {s3_url}: {e}")
        return s3_url


# Environment
DRONE_TABLE = os.environ.get('DRONE_TABLE', 'drone-registry-dev')
STATUS_TABLE = os.environ.get('STATUS_TABLE', 'drone-status-dev')
LOGS_TABLE = os.environ.get('LOGS_TABLE', 'drone-logs-dev')
CONVERSATIONS_TABLE = os.environ.get('CONVERSATIONS_TABLE', 'drone-conversations-dev')
IMAGES_BUCKET = os.environ.get('IMAGES_BUCKET', 'drone-images-dev')
IOT_ENDPOINT = os.environ.get('IOT_ENDPOINT', '')

# Mission system prompt - For AGX drones with VLM (Qwen3-VL)
MISSION_SYSTEM_PROMPT = """You are an AI mission planner for an autonomous drone with onboard vision intelligence and full obstacle avoidance.

The drone has Qwen3-VL on NVIDIA Jetson Orin AGX and Nav2 navigation. All movement uses obstacle avoidance — the drone will never fly into something.

PHASE TYPES — each phase in the "phases" array is one of:

1. TYPED PRIMITIVES (use for metric/GPS commands):
   {"type": "arm_and_takeoff", "altitude_m": 5}
   {"type": "nav", "north_m": 10.0, "east_m": 0.0, "alt_m": 5, "description": "fly 10m north"}
   {"type": "go_to_gps", "lat": 37.7749, "lon": -122.4194, "alt_m": 15, "description": "123 Main St"}
   {"type": "fly_circle", "radius_m": 10, "altitude_m": 5, "waypoints": 8}
   {"type": "look_around", "directions": 4}
   {"type": "capture_photo", "description": "what to capture"}
   {"type": "return_home"}
   {"type": "land"}

2. VLM PHASES (use for visual/search tasks — drone's AI figures out how):
   {"objective": "Find the red car", "success": "Car located and photographed"}
   {"objective": "Survey trees for the most interesting one", "success": "Best tree identified", "evaluation_criteria": "shape and foliage"}

RULES:
- Always start with arm_and_takeoff, always end with return_home then land
- Use "nav" for any explicit distance/direction ("go 10m north", "move 5m east")
- Use "fly_circle" for radius/orbit commands ("look around a 10m radius", "circle the area")
- Use "go_to_gps" when you can resolve an address/landmark to approximate coordinates; include your best GPS estimate
- Use VLM phases for open-ended visual tasks ("find the nearest person", "photograph the prettiest tree")
- Mix typed and VLM phases freely in the same mission
- Keep altitude under 20m; use 15m+ for outdoor GPS navigation
- ceiling and forward obstacle avoidance are always active — no need to mention them

RESPONSE FORMAT:
{"action": "mission", "mission": {"phases": [...]}, "message": "Brief message to user"}
OR: {"action": "respond", "message": "..."}
OR: {"action": "ask", "message": "..."}

EXAMPLES:

User: "Look around a 10 meter radius"
{"action": "mission", "mission": {"phases": [
  {"type": "arm_and_takeoff", "altitude_m": 5},
  {"type": "fly_circle", "radius_m": 10, "altitude_m": 5, "waypoints": 8},
  {"type": "look_around", "directions": 4},
  {"type": "return_home"},
  {"type": "land"}
]}, "message": "I'll orbit a 10m radius, look around, and return."}

User: "Go to 123 Main St and take a photo"
{"action": "mission", "mission": {"phases": [
  {"type": "arm_and_takeoff", "altitude_m": 15},
  {"type": "go_to_gps", "lat": 37.7751, "lon": -122.4183, "alt_m": 15, "description": "123 Main St"},
  {"type": "capture_photo", "description": "street view"},
  {"type": "return_home"},
  {"type": "land"}
]}, "message": "Flying to 123 Main St, taking a photo, and returning."}

User: "Find the prettiest tree in the backyard and photograph it"
{"action": "mission", "mission": {"phases": [
  {"type": "arm_and_takeoff", "altitude_m": 5},
  {"objective": "Fly to backyard area", "success": "Multiple trees visible"},
  {"objective": "Survey trees and select the most visually interesting", "success": "Best tree identified", "evaluation_criteria": "shape, foliage, symmetry"},
  {"objective": "Approach and photograph selected tree", "success": "Photo captured"},
  {"type": "return_home"},
  {"type": "land"}
]}, "message": "I'll find the prettiest tree in the backyard, photograph it, and return."}

Output ONLY the JSON object, no markdown or extra text."""

# Legacy agent system prompt - Goal-based for non-VLM drones (Nano/NX)
AGENT_SYSTEM_PROMPT = """You are an AI that sets goals for an autonomous drone.

The drone has on-device intelligence that figures out HOW to achieve goals. You specify WHAT to accomplish.

CAPABILITIES (what you can ask the drone to do):
1. Search for objects/people ("find the nearest person", "locate the dog")
2. Explore areas ("photograph every room", "check the perimeter")
3. Navigate ("go to the backyard", "fly through that door")
4. Observe ("look around and describe what you see", "take a photo")
5. Simple movements ("take off", "land", "go up 2 meters")

RESPONSE FORMAT:
Respond with a JSON object containing one of these actions:

1. Set a goal (for complex autonomous tasks):
{
  "action": "set_goal",
  "goal": {
    "objective": "Clear description of what to accomplish",
    "success_criteria": ["condition 1", "condition 2"],
    "constraints": ["safety constraint 1", "behavioral constraint 2"],
    "context": {"key": "value"},
    "priority": "safety|thorough|fast"
  },
  "message": "Brief message to user about what drone will attempt"
}

2. Execute simple code (for basic commands like takeoff/land):
{"action": "execute", "code": "takeoff(5)\\nwait(2)", "message": "Taking off to 5 meters..."}

3. Ask a clarifying question:
{"action": "ask", "message": "I see two white buildings. Which one do you mean?"}

4. Provide information (no action needed):
{"action": "respond", "message": "Based on what I can see, the building appears to be a warehouse."}

5. Request camera action to gather info:
{"action": "look", "message": "Let me take a photo..."}

WHEN TO USE "set_goal" vs "execute":
- set_goal: Complex tasks requiring reasoning (search, explore, navigate to objects)
- execute: Simple commands (takeoff, land, basic movements with specific values)

EXAMPLES:

User: "Take off to 3 meters"
{"action": "execute", "code": "takeoff(3)", "message": "Taking off to 3 meters..."}

User: "Find the nearest person"
{
  "action": "set_goal",
  "goal": {
    "objective": "Locate the nearest person",
    "success_criteria": ["person detected", "distance and direction reported"],
    "constraints": ["maintain safe distance from people", "stay in current area"],
    "context": {"search_type": "nearest", "target": "person"},
    "priority": "fast"
  },
  "message": "I'll look around to find the nearest person."
}

User: "Go inside this house and take pictures of every room"
{
  "action": "set_goal",
  "goal": {
    "objective": "Enter the building and photograph each distinct room",
    "success_criteria": ["entered building", "all accessible rooms photographed", "exited building"],
    "constraints": ["avoid damaging property", "respect privacy if people present", "maintain safe altitude indoors"],
    "context": {"environment": "residential building", "task": "documentation"},
    "priority": "thorough"
  },
  "message": "I'll find an entrance, explore the building room by room, and photograph each one."
}

User: "Search for anyone who might need help"
{
  "action": "set_goal",
  "goal": {
    "objective": "Search the area for people who may need assistance",
    "success_criteria": ["all accessible areas checked", "any concerning situations reported"],
    "constraints": ["prioritize safety", "do not interfere unless reporting", "check thoroughly"],
    "context": {"search_type": "welfare check", "target": "people in distress"},
    "priority": "safety"
  },
  "message": "I'll thoroughly search the area and report anyone who appears to need help."
}

User: "Land"
{"action": "execute", "code": "land()", "message": "Landing now..."}

CRITICAL RULES:
1. For visual requests ("look", "show me", "what do you see"), use action="look"
2. The drone figures out HOW to achieve goals - don't specify exact movements
3. Keep simple commands simple (use "execute"), complex tasks as goals (use "set_goal")
4. Be conversational and helpful

Output ONLY the JSON object, no markdown or extra text."""

# Code generation prompt (simpler, just for drone commands)
CODE_SYSTEM_PROMPT = """You generate Python code for drone control. The code runs on a drone with these SDK functions pre-imported:

AVAILABLE FUNCTIONS:
- motor_test(motor_num, throttle_pct=15, duration_sec=2)
- arm() - Arm motors
- safe_disarm() - Disarm motors (only when on the ground; raises error if airborne)
- takeoff(altitude_m) / land() - land() handles disarming automatically
- goto(lat, lon, alt)
- set_velocity(vx, vy, vz)
- set_yaw(angle_deg, relative=False)
- wait(seconds)
- get_position() - returns tuple (lat, lon, alt_m) — alt_m is RELATIVE altitude above home (same frame as goto/takeoff); index with [0],[1],[2] NOT dict keys
- get_attitude() - returns tuple (roll, pitch, yaw) in degrees
- capture_photo(upload=True) - Take photo and upload to S3, return URL

PRE-DEFINED VARIABLES (always available, do NOT redefine):
- home_lat, home_lon, home_alt — GPS position at the moment the command was received (relative alt above home)
- CONVERSATION_ID — for capture_photo()

RULES:
1. Use ONLY these SDK functions
2. Do NOT import anything - all functions are pre-imported
3. ALWAYS call arm() before takeoff(), then wait(2) for motors to spin up
4. "disarm" always means safe_disarm() — never substitute land(). "land" means land().
5. For flight: arm() → takeoff() → ... → land(). land() handles disarming automatically.
6. Keep throttle under 30%, altitude under 20m
7. Use home_lat/home_lon for relative navigation (e.g. home_lat + 0.00001 ≈ 1.1m north)

Output ONLY Python code. No markdown, no comments unless necessary."""


def get_user_id(event):
    """Extract user ID from Lambda authorizer context."""
    try:
        authorizer = event['requestContext']['authorizer']
        return authorizer.get('userId') or authorizer.get('principalId')
    except (KeyError, TypeError):
        return None


def verify_ownership(user_id, drone_id):
    """Verify that the user owns the specified drone."""
    table = dynamodb.Table(DRONE_TABLE)
    try:
        response = table.get_item(Key={'userId': user_id, 'droneId': drone_id})
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
        'body': json.dumps(body, default=str)
    }


def save_message(conversation_id, drone_id, sender, content_type, content, image_urls=None):
    """Save a message to the conversation history."""
    table = dynamodb.Table(CONVERSATIONS_TABLE)
    
    timestamp = datetime.now(timezone.utc).isoformat()
    message_id = str(uuid.uuid4())
    
    item = {
        'PK': f'CONV#{drone_id}#{conversation_id}',
        'SK': f'MSG#{timestamp}#{message_id}',
        'messageId': message_id,
        'conversationId': conversation_id,
        'droneId': drone_id,
        'sender': sender,
        'contentType': content_type,
        'content': content,
        'timestamp': timestamp,
        'ttl': int(datetime.now(timezone.utc).timestamp()) + (7 * 24 * 60 * 60)  # 7 days
    }
    
    if image_urls:
        item['imageUrls'] = image_urls
    
    table.put_item(Item=item)
    
    return {
        'id': message_id,
        'conversationId': conversation_id,
        'sender': sender,
        'content': {'type': content_type, 'text': content},
        'timestamp': timestamp
    }


def get_conversation_history(drone_id, conversation_id, limit=20):
    """Get recent messages from a conversation."""
    table = dynamodb.Table(CONVERSATIONS_TABLE)
    
    response = table.query(
        KeyConditionExpression='PK = :pk AND begins_with(SK, :sk_prefix)',
        ExpressionAttributeValues={
            ':pk': f'CONV#{drone_id}#{conversation_id}',
            ':sk_prefix': 'MSG#'
        },
        ScanIndexForward=True,  # Oldest first
        Limit=limit
    )
    
    messages = []
    for item in response.get('Items', []):
        msg = {
            'role': 'user' if item['sender'] == 'user' else 'assistant',
            'content': item['content']
        }
        if item.get('imageUrls'):
            msg['images'] = item['imageUrls']
        messages.append(msg)
    
    return messages


def fetch_image_as_base64(url: str) -> tuple[str, str]:
    """Fetch an image from URL and return (base64_data, media_type)."""
    try:
        print(f"📸 Fetching image: {url[:100]}...")
        
        # Handle S3 URLs - need to use pre-signed URL or fetch directly
        req = urllib.request.Request(url, headers={'User-Agent': 'DroneAgent/1.0'})
        with urllib.request.urlopen(req, timeout=30) as response:
            image_data = response.read()
            content_type = response.headers.get('Content-Type', 'image/jpeg')
            
            # Map content type to Claude format
            if 'png' in content_type:
                media_type = 'image/png'
            elif 'gif' in content_type:
                media_type = 'image/gif'
            elif 'webp' in content_type:
                media_type = 'image/webp'
            else:
                media_type = 'image/jpeg'
            
            encoded = base64.b64encode(image_data).decode('utf-8')
            print(f"✅ Image fetched: {len(image_data)} bytes, {media_type}")
            return encoded, media_type
            
    except Exception as e:
        print(f"❌ Failed to fetch image: {e}")
        return None, None


def call_agent(conversation_history, user_message, pending_images=None):
    """Call the AI agent to decide on action."""
    
    # Build messages for Bedrock (Claude format)
    messages = []
    
    # Add conversation history
    for msg in conversation_history:
        messages.append({
            'role': msg['role'],
            'content': [{'type': 'text', 'text': msg['content']}]
        })
    
    # Add current user message with images if available
    user_content = [{'type': 'text', 'text': user_message}]
    
    # Add any pending images (from drone) - actually encode and include them
    if pending_images:
        for img_url in pending_images:
            # Generate pre-signed URL first if it's an S3 URL
            presigned_url = generate_presigned_url(img_url)
            
            # Fetch and encode the image
            img_base64, media_type = fetch_image_as_base64(presigned_url)
            
            if img_base64:
                # Add image to the message content for Claude vision
                user_content.append({
                    'type': 'image',
                    'source': {
                        'type': 'base64',
                        'media_type': media_type,
                        'data': img_base64
                    }
                })
            else:
                # Fallback to text reference if fetch failed
                user_content[0]['text'] += f"\n[Image could not be loaded: {img_url}]"
    
    messages.append({'role': 'user', 'content': user_content})
    
    try:
        response = bedrock.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps({
                'anthropic_version': ANTHROPIC_VERSION,
                'system': AGENT_SYSTEM_PROMPT,
                'messages': messages,
                'max_tokens': 1024
            })
        )
        
        result = json.loads(response['body'].read())
        response_text = ''.join(
            block.get('text', '') for block in result.get('content', [])
            if block.get('type') == 'text'
        ).strip()
        
        # Parse JSON response
        try:
            # Handle potential markdown wrapping
            if response_text.startswith('```'):
                lines = response_text.split('\n')
                response_text = '\n'.join(lines[1:-1] if lines[-1] == '```' else lines[1:])
            
            return json.loads(response_text)
        except json.JSONDecodeError:
            # If not valid JSON, treat as simple response
            return {'action': 'respond', 'message': response_text}
            
    except Exception as e:
        print(f"❌ Agent error: {e}")
        return {'action': 'respond', 'message': f'Error processing request: {str(e)}'}


def generate_code(instruction, conversation_id):
    """Generate Python code for a drone command."""
    try:
        response = bedrock.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps({
                'anthropic_version': ANTHROPIC_VERSION,
                'system': CODE_SYSTEM_PROMPT,
                'messages': [
                    {
                        'role': 'user',
                        'content': [{
                            'type': 'text',
                            'text': f"CONVERSATION_ID = '{conversation_id}'\n\nCommand: {instruction}"
                        }]
                    }
                ],
                'max_tokens': 1024
            })
        )
        
        result = json.loads(response['body'].read())
        code = ''.join(
            block.get('text', '') for block in result.get('content', [])
            if block.get('type') == 'text'
        ).strip()
        
        # Clean up code - extract from markdown code block if present
        if '```' in code:
            # Find code between ``` markers
            import re
            # Match ```python or ``` followed by code and closing ```
            match = re.search(r'```(?:python)?\s*\n(.*?)```', code, re.DOTALL)
            if match:
                code = match.group(1).strip()
            else:
                # Fallback: just strip leading ``` line
                lines = code.split('\n')
                start = 0
                end = len(lines)
                for i, line in enumerate(lines):
                    if line.strip().startswith('```'):
                        start = i + 1
                        break
                for i in range(len(lines) - 1, -1, -1):
                    if lines[i].strip() == '```':
                        end = i
                        break
                code = '\n'.join(lines[start:end]).strip()
        
        # Remove any preamble text before actual code (lines not starting with valid Python)
        lines = code.split('\n')
        code_start = 0
        for i, line in enumerate(lines):
            stripped = line.strip()
            # Skip empty lines at start
            if not stripped:
                continue
            # Check if line looks like Python code (starts with identifier, keyword, or comment)
            if (stripped.startswith('#') or 
                stripped.startswith('import ') or
                stripped.startswith('from ') or
                any(stripped.startswith(f'{func}(') for func in ['motor_test', 'arm', 'safe_disarm', 'disarm', 'takeoff', 'land', 'goto', 'wait', 'get_position', 'capture_photo', 'set_velocity', 'set_yaw']) or
                stripped.startswith('CONVERSATION_ID')):
                code_start = i
                break

        code = '\n'.join(lines[code_start:]).strip()

        # If the user said "disarm" (but not "land"), replace any land() the LLM
        # generated with safe_disarm() — the LLM stubbornly maps disarm→land().
        import re as _re
        if 'disarm' in instruction.lower() and 'land' not in instruction.lower():
            code = _re.sub(r'\bland\s*\(\s*\)', 'safe_disarm()', code)

        return code
        
    except Exception as e:
        error_msg = str(e)
        # Surface the error to the user instead of sending a comment as "code"
        raise RuntimeError(f"Failed to generate code: {error_msg}") from e


def publish_to_drone(drone_id, conversation_id, payload):
    """Send command to drone via IoT Core."""
    topic = f'drone/{drone_id}/chat/{conversation_id}/command'
    print(f"📤 Publishing to drone topic: {topic}")
    print(f"📤 Payload: {json.dumps(payload)[:500]}")
    
    iot.publish(
        topic=topic,
        qos=1,
        payload=json.dumps(payload)
    )
    print(f"✅ Published to drone")


def publish_to_app(drone_id, conversation_id, message_type, text, image_urls=None, image_options=None):
    """Send message to app via IoT Core."""
    topic = f'drone/{drone_id}/chat/{conversation_id}'
    
    payload = {
        'droneId': drone_id,
        'conversation_id': conversation_id,
        'message_type': message_type,
        'text': text,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }
    
    if image_urls:
        # Generate pre-signed URLs for images
        payload['image_urls'] = [generate_presigned_url(url) for url in image_urls]
    if image_options:
        # Generate pre-signed URLs for image options too
        payload['image_options'] = [
            {**opt, 'url': generate_presigned_url(opt['url'])}
            for opt in image_options
        ]
    
    print(f"📤 Publishing to app topic: {topic}, type: {message_type}")
    iot.publish(
        topic=topic,
        qos=1,
        payload=json.dumps(payload)
    )


def publish_log(drone_id, level, message, code=None, source="aws"):
    """Publish a log entry to the drone logs topic for UI visibility."""
    topic = f"drone/{drone_id}/logs"
    log_entry = {
        "timestamp": datetime.now(timezone.utc).isoformat(),
        "level": level,
        "source": source,
        "message": message
    }
    if code:
        log_entry["code"] = code
    try:
        iot.publish(
            topic=topic,
            qos=1,
            payload=json.dumps(log_entry)
        )
    except Exception as e:
        print(f"⚠️ Failed to publish log: {e}")


def get_drone_capabilities(drone_id):
    """
    Get drone capabilities from status table.
    
    Returns dict with:
    - has_vlm: bool - True if drone has Qwen3-VL
    - variant: str - 'nano', 'nx', 'agx32', 'agx64', or 'unknown'
    - nav2_available: bool - True if Nav2 is running
    """
    table = dynamodb.Table(STATUS_TABLE)
    try:
        response = table.get_item(Key={'droneId': drone_id})
        if 'Item' not in response:
            return {'has_vlm': False, 'variant': 'unknown', 'nav2_available': False}
        
        item = response['Item']
        
        # Check capabilities field (populated by drone heartbeat)
        capabilities = item.get('capabilities', {})
        
        # Determine if drone has VLM based on variant
        variant = capabilities.get('variant', item.get('variant', 'unknown'))
        has_vlm = variant in ('agx32', 'agx64')
        
        # Check if VLM is explicitly reported
        if capabilities.get('vlm_available'):
            has_vlm = True
        
        nav2_available = capabilities.get('nav2_available', has_vlm)  # AGX implies Nav2
        
        return {
            'has_vlm': has_vlm,
            'variant': variant,
            'nav2_available': nav2_available,
        }
        
    except Exception as e:
        print(f"Error getting drone capabilities: {e}")
        return {'has_vlm': False, 'variant': 'unknown', 'nav2_available': False}


def call_mission_agent(conversation_history, user_message, pending_images=None):
    """
    Call the AI agent for mission planning (AGX drones with VLM).
    
    This uses the mission-based approach for drones with on-device VLM.
    """
    messages = []
    
    # Add conversation history
    for msg in conversation_history:
        messages.append({
            'role': msg['role'],
            'content': [{'type': 'text', 'text': msg['content']}]
        })
    
    # Add current user message with images if available
    user_content = [{'type': 'text', 'text': user_message}]
    
    if pending_images:
        for img_url in pending_images:
            presigned_url = generate_presigned_url(img_url)
            img_base64, media_type = fetch_image_as_base64(presigned_url)
            
            if img_base64:
                user_content.append({
                    'type': 'image',
                    'source': {
                        'type': 'base64',
                        'media_type': media_type,
                        'data': img_base64
                    }
                })
    
    messages.append({'role': 'user', 'content': user_content})
    
    try:
        response = bedrock.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps({
                'anthropic_version': ANTHROPIC_VERSION,
                'system': MISSION_SYSTEM_PROMPT,
                'messages': messages,
                'max_tokens': 2048  # Missions can be longer
            })
        )
        
        result = json.loads(response['body'].read())
        response_text = ''.join(
            block.get('text', '') for block in result.get('content', [])
            if block.get('type') == 'text'
        ).strip()
        
        # Parse JSON response
        try:
            if response_text.startswith('```'):
                lines = response_text.split('\n')
                response_text = '\n'.join(lines[1:-1] if lines[-1] == '```' else lines[1:])
            
            return json.loads(response_text)
        except json.JSONDecodeError:
            return {'action': 'respond', 'message': response_text}
            
    except Exception as e:
        print(f"❌ Mission agent error: {e}")
        return {'action': 'respond', 'message': f'Error processing request: {str(e)}'}


def is_drone_online(drone_id):
    """Check if drone is online by querying recent heartbeat from status table."""
    import time
    table = dynamodb.Table(STATUS_TABLE)
    try:
        # Get the status record for this drone (written by IoT Rule from heartbeats)
        response = table.get_item(Key={'droneId': drone_id})
        
        if 'Item' not in response:
            print(f"No status record found for drone {drone_id}")
            return False
        
        item = response['Item']
        
        # Prefer TTL when present (seconds since epoch)
        ttl = item.get('ttl')
        current_time = int(time.time())
        if ttl:
            try:
                ttl_int = int(ttl)
                is_online = ttl_int > current_time
                print(f"Drone {drone_id} status check: ttl={ttl_int}, current={current_time}, online={is_online}")
                return is_online
            except (ValueError, TypeError):
                pass

        # Fallback: lastUpdate epoch ms within STATUS_TTL_SECONDS
        last_update = item.get('lastUpdate')
        if last_update:
            try:
                last_update_ms = float(last_update)
                now_ms = time.time() * 1000
                age_seconds = (now_ms - last_update_ms) / 1000
                is_online = age_seconds < STATUS_TTL_SECONDS
                print(f"Drone {drone_id} status check: age={age_seconds:.1f}s, online={is_online}")
                return is_online
            except (ValueError, TypeError):
                pass

        print(f"Drone {drone_id} status check: missing ttl/lastUpdate, checking logs table")
        logs_table = dynamodb.Table(LOGS_TABLE)
        logs_resp = logs_table.query(
            KeyConditionExpression=boto3.dynamodb.conditions.Key('droneId').eq(drone_id),
            ScanIndexForward=False,
            Limit=1
        )
        items = logs_resp.get('Items') or []
        if items:
            last_ts = items[0].get('timestamp', '')
            # Timestamp suffix is millis since epoch (see store_logs_handler)
            if isinstance(last_ts, str) and "_" in last_ts:
                try:
                    last_ms = int(last_ts.split("_")[-1])
                    age_seconds = (time.time() * 1000 - last_ms) / 1000
                    is_online = age_seconds < STATUS_TTL_SECONDS
                    print(f"Drone {drone_id} logs check: age={age_seconds:.1f}s, online={is_online}")
                    return is_online
                except Exception:
                    pass
        return False
    except Exception as e:
        print(f"Error checking drone status: {e}")
        # If we can't check, assume online and let the timeout handle it
        return True


# ============================================
# Lambda Handlers
# ============================================

def create_handler(event, context):
    """Create a new conversation."""
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})
    
    drone_id = event['pathParameters']['droneId']
    
    if not verify_ownership(user_id, drone_id):
        return json_response(403, {'error': 'You do not own this drone'})
    
    # Create conversation
    conversation_id = str(uuid.uuid4())
    timestamp = datetime.now(timezone.utc).isoformat()
    
    table = dynamodb.Table(CONVERSATIONS_TABLE)
    table.put_item(Item={
        'PK': f'CONV#{drone_id}#{conversation_id}',
        'SK': 'META',
        'conversationId': conversation_id,
        'droneId': drone_id,
        'userId': user_id,
        'createdAt': timestamp,
        'ttl': int(datetime.now(timezone.utc).timestamp()) + (7 * 24 * 60 * 60)
    })
    
    return json_response(200, {
        'conversation_id': conversation_id,
        'drone_id': drone_id,
        'created_at': timestamp
    })


def get_handler(event, context):
    """Get conversation history."""
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})
    
    drone_id = event['pathParameters']['droneId']
    conversation_id = event['pathParameters']['conversationId']
    
    if not verify_ownership(user_id, drone_id):
        return json_response(403, {'error': 'You do not own this drone'})
    
    # Get messages
    table = dynamodb.Table(CONVERSATIONS_TABLE)
    
    response = table.query(
        KeyConditionExpression='PK = :pk AND begins_with(SK, :sk_prefix)',
        ExpressionAttributeValues={
            ':pk': f'CONV#{drone_id}#{conversation_id}',
            ':sk_prefix': 'MSG#'
        },
        ScanIndexForward=True
    )
    
    messages = []
    for item in response.get('Items', []):
        # Skip "loading" messages - they're temporary placeholders
        if item['contentType'] == 'loading':
            continue
            
        msg = {
            'id': item['messageId'],
            'conversationId': conversation_id,
            'sender': item['sender'],
            'content': {
                'type': item['contentType'],
                'text': item['content']
            },
            'timestamp': item['timestamp']
        }
        
        if item.get('imageUrls'):
            # Generate pre-signed URLs for all images
            print(f"Processing message with {len(item['imageUrls'])} images")
            signed_urls = [generate_presigned_url(url) for url in item['imageUrls']]
            print(f"Generated {len(signed_urls)} signed URLs")
            
            if item['contentType'] == 'image_choice':
                msg['content']['options'] = [
                    {'id': i, 'url': url, 'description': None}
                    for i, url in enumerate(signed_urls)
                ]
                print(f"Added {len(msg['content']['options'])} options to message")
            else:
                msg['content']['url'] = signed_urls[0]
        
        messages.append(msg)
    
    print(f"Returning {len(messages)} messages")
    
    return json_response(200, {
        'conversation_id': conversation_id,
        'messages': messages
    })


def is_visual_request(message: str) -> bool:
    """Check if the message is asking for visual information (camera use)."""
    message_lower = message.lower()
    
    # Keywords that indicate the user wants to see something
    visual_keywords = [
        'what do you see', 'what can you see', 'tell me what you see',
        'show me', 'take a photo', 'take a picture', 'take photo', 'take picture',
        'capture', 'look around', 'look at', 'what is around', "what's around",
        'can you see', 'do you see', 'see anything', 'what is there',
        'describe what', 'what is in front', "what's in front",
        'look for', 'find the', 'spot the', 'locate',
        'snap a', 'grab a photo', 'get a picture',
    ]
    
    return any(keyword in message_lower for keyword in visual_keywords)


def is_movement_request(message: str) -> bool:
    """Check if the message includes explicit movement/flight instructions."""
    message_lower = message.lower()
    movement_keywords = [
        'take off', 'takeoff', 'lift off', 'lift-off',
        'go up', 'rise', 'climb', 'ascend', 'descend',
        'move', 'fly', 'hover', 'land', 'return', 'rtl',
        'motor test', 'test motor', 'test motors', 'motor check',
        'spin motor', 'spin motors',
        'meters', 'meter', 'feet', 'foot', 'ft', 'm ',
        'forward', 'backward', 'left', 'right',
        'north', 'south', 'east', 'west'
    ]
    return any(keyword in message_lower for keyword in movement_keywords)


def message_handler(event, context):
    """Handle a new chat message from user."""
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})
    
    drone_id = event['pathParameters']['droneId']
    conversation_id = event['pathParameters']['conversationId']
    
    if not verify_ownership(user_id, drone_id):
        return json_response(403, {'error': 'You do not own this drone'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except json.JSONDecodeError:
        return json_response(400, {'error': 'Invalid JSON'})
    
    message = body.get('message', '')
    if not message:
        return json_response(400, {'error': 'Missing message'})
    
    # Save user message
    save_message(conversation_id, drone_id, 'user', 'text', message)
    
    # Get conversation history
    history = get_conversation_history(drone_id, conversation_id)
    
    # Check drone capabilities to decide which agent to use
    capabilities = get_drone_capabilities(drone_id)
    has_vlm = capabilities.get('has_vlm', False)
    variant = capabilities.get('variant', 'unknown')
    
    print(f"🤖 Drone {drone_id} capabilities: variant={variant}, has_vlm={has_vlm}")
    
    # Call appropriate agent based on drone capabilities
    if has_vlm:
        # AGX drone with VLM - use mission-based approach
        agent_response = call_mission_agent(history, message)
    else:
        # Legacy drone (Nano/NX) - use goal-based approach
        agent_response = call_agent(history, message)
    
    action = agent_response.get('action', 'respond')
    print(f"🤖 Agent response: action={action}, message={agent_response.get('message', '')[:100]}")
    publish_log(drone_id, "INFO", f"Agent action={action} for message: {message[:200]} (variant={variant})")

    movement_request = is_movement_request(message)
    visual_request = is_visual_request(message)
    
    # SAFETY NET: Override to 'look' for pure visual requests (no movement) on non-VLM drones
    if not has_vlm and action in ('respond', 'ask') and visual_request and not movement_request:
        print(f"⚠️ Overriding action from '{action}' to 'look' - detected visual request: {message}")
        action = 'look'
        agent_response['action'] = 'look'
        agent_response['message'] = 'Let me take a look...'
        publish_log(drone_id, "INFO", f"Overrode action from '{action}' to look (visual request without movement)")

    # SAFETY NET: Force execution for explicit movement requests on non-VLM drones only.
    # VLM/AGX drones use the mission planner which routes all movement through Nav2 obstacle avoidance.
    if movement_request and not has_vlm:
        if action in ('respond', 'look'):
            print(f"⚠️ Overriding action from '{action}' to 'execute' - detected movement request: {message}")
        else:
            print(f"⚠️ Forcing execute - detected movement request: {message}")
        action = 'execute'
        agent_response['action'] = 'execute'
        agent_response['message'] = 'Executing flight command...'
        publish_log(drone_id, "INFO", f"Forced action=execute (movement request, non-VLM), prev={action}")
    
    # Check if drone is online before sending commands
    drone_online = is_drone_online(drone_id)
    if not drone_online:
        publish_log(drone_id, "WARNING", "Drone appears offline (no recent heartbeat)")
    
    # Handle different agent actions
    if action == 'mission':
        # New mission-based approach for AGX drones with VLM
        if not drone_online:
            error_msg = save_message(
                conversation_id, drone_id, 'drone', 'text',
                'The drone appears to be offline. Please check the connection and try again.'
            )
            publish_to_app(drone_id, conversation_id, 'error', 
                'The drone appears to be offline. Please check the connection and try again.')
            publish_log(drone_id, "ERROR", "Rejected mission: drone offline")
            return json_response(200, {
                'status': 'error',
                'message_id': error_msg['id'],
                'immediate_response': error_msg
            })
        
        mission = agent_response.get('mission', {})
        phases = mission.get('phases', [])
        publish_log(drone_id, "INFO", f"Starting mission with {len(phases)} phases")
        
        # Save loading message
        loading_msg = save_message(
            conversation_id, drone_id, 'drone', 'loading',
            agent_response.get('message', 'Starting mission...')
        )
        
        # Send ack via MQTT so app knows request is being processed
        publish_to_app(drone_id, conversation_id, 'ack', 
            agent_response.get('message', 'Starting mission...'))
        
        # Generate mission ID
        import uuid
        mission_id = str(uuid.uuid4())[:8]
        
        # Send mission to drone
        publish_to_drone(drone_id, conversation_id, {
            'action': 'mission',
            'mission_id': mission_id,
            'phases': phases,
            'conversation_id': conversation_id,
            'original_message': message
        })
        
        return json_response(200, {
            'status': 'sent',
            'mission_id': mission_id,
            'message_id': loading_msg['id'],
            'immediate_response': loading_msg
        })
    
    elif action == 'set_goal':
        # New goal-based approach - send goal to drone's on-device intelligence
        if not drone_online:
            error_msg = save_message(
                conversation_id, drone_id, 'drone', 'text',
                'The drone appears to be offline. Please check the connection and try again.'
            )
            publish_to_app(drone_id, conversation_id, 'error', 
                'The drone appears to be offline. Please check the connection and try again.')
            publish_log(drone_id, "ERROR", "Rejected set_goal: drone offline")
            return json_response(200, {
                'status': 'error',
                'message_id': error_msg['id'],
                'immediate_response': error_msg
            })
        
        goal = agent_response.get('goal', {})
        publish_log(drone_id, "INFO", f"Setting goal: {goal.get('objective', 'unknown')}")
        
        # Save loading message
        loading_msg = save_message(
            conversation_id, drone_id, 'drone', 'loading',
            agent_response.get('message', 'Working on it...')
        )
        
        # Send ack via MQTT so app knows request is being processed
        publish_to_app(drone_id, conversation_id, 'ack', 
            agent_response.get('message', 'Working on it...'))
        
        # Send goal to drone
        publish_to_drone(drone_id, conversation_id, {
            'action': 'set_goal',
            'goal': goal,
            'conversation_id': conversation_id,
            'original_message': message
        })
        
        return json_response(200, {
            'status': 'sent',
            'message_id': loading_msg['id'],
            'immediate_response': loading_msg
        })
    
    elif action == 'execute':
        # Check if drone is online
        if not drone_online:
            error_msg = save_message(
                conversation_id, drone_id, 'drone', 'text',
                'The drone appears to be offline. Please check the connection and try again.'
            )
            publish_to_app(drone_id, conversation_id, 'error', 
                'The drone appears to be offline. Please check the connection and try again.')
            publish_log(drone_id, "ERROR", "Rejected execute: drone offline")
            return json_response(200, {
                'status': 'error',
                'message_id': error_msg['id'],
                'immediate_response': error_msg
            })
        
        # Generate and send code to drone
        code = agent_response.get('code', '')
        if not code:
            # Use the original user message to preserve details
            try:
                code = generate_code(message, conversation_id)
            except Exception as e:
                error_text = f"Sorry, I couldn't process that command. AI service error: {str(e)}"
                error_msg = save_message(conversation_id, drone_id, 'drone', 'text', error_text)
                publish_to_app(drone_id, conversation_id, 'error', error_text)
                publish_log(drone_id, "ERROR", f"Code generation failed: {e}")
                return json_response(200, {
                    'status': 'error',
                    'message_id': error_msg['id'],
                    'immediate_response': error_msg
                })
        publish_log(drone_id, "INFO", "Dispatching execute command to drone", code=code)
        
        # Save loading message
        loading_msg = save_message(
            conversation_id, drone_id, 'drone', 'loading',
            agent_response.get('message', 'Working on it...')
        )
        
        # Send ack via MQTT so app knows request is being processed
        publish_to_app(drone_id, conversation_id, 'ack', 
            agent_response.get('message', 'Working on it...'))
        
        # Send to drone
        publish_to_drone(drone_id, conversation_id, {
            'action': 'execute',
            'code': code,
            'conversation_id': conversation_id,
            'original_message': message,
            'message_id': loading_msg['id']
        })
        
        return json_response(200, {
            'status': 'sent',
            'message_id': loading_msg['id'],
            'immediate_response': loading_msg
        })
    
    elif action == 'ask':
        # Agent needs clarification
        pending_images = agent_response.get('pending_images', [])
        
        if pending_images:
            # Save as image choice
            msg = save_message(
                conversation_id, drone_id, 'drone', 'image_choice',
                agent_response.get('message', 'Which one?'),
                image_urls=pending_images
            )
            msg['content'] = {
                'type': 'image_choice',
                'text': agent_response.get('message', 'Which one?'),
                'options': [
                    {'id': i, 'url': url, 'description': None}
                    for i, url in enumerate(pending_images)
                ]
            }
        else:
            msg = save_message(
                conversation_id, drone_id, 'drone', 'text',
                agent_response.get('message', 'Could you clarify?')
            )
        
        return json_response(200, {
            'status': 'sent',
            'message_id': msg['id'],
            'immediate_response': msg
        })
    
    elif action == 'look':
        # Check if drone is online
        if not drone_online:
            error_msg = save_message(
                conversation_id, drone_id, 'drone', 'text',
                'The drone appears to be offline. Please check the connection and try again.'
            )
            publish_to_app(drone_id, conversation_id, 'error', 
                'The drone appears to be offline. Please check the connection and try again.')
            publish_log(drone_id, "ERROR", "Rejected look: drone offline")
            return json_response(200, {
                'status': 'error',
                'message_id': error_msg['id'],
                'immediate_response': error_msg
            })
        
        # Agent wants to use camera - capture_photo uploads to S3 automatically
        code = 'url = capture_photo(upload=True)\nprint(f"Photo: {url}")'
        
        loading_msg = save_message(
            conversation_id, drone_id, 'drone', 'loading',
            agent_response.get('message', 'Looking around...')
        )
        
        # Send ack via MQTT so app knows request is being processed
        publish_to_app(drone_id, conversation_id, 'ack', 
            agent_response.get('message', 'Looking around...'))
        
        publish_to_drone(drone_id, conversation_id, {
            'action': 'look',
            'code': code,
            'conversation_id': conversation_id,
            'original_message': message,
            'follow_up': True  # Tell drone to send images back for analysis
        })
        publish_log(drone_id, "INFO", "Dispatching look command to drone", code=code)
        
        return json_response(200, {
            'status': 'sent',
            'message_id': loading_msg['id'],
            'immediate_response': loading_msg
        })
    
    else:  # 'respond'
        # Simple response, no drone action needed
        msg = save_message(
            conversation_id, drone_id, 'drone', 'text',
            agent_response.get('message', 'I understand.')
        )
        
        # Also publish via MQTT for real-time
        publish_to_app(drone_id, conversation_id, 'text', msg['content']['text'])
        
        return json_response(200, {
            'status': 'sent',
            'message_id': msg['id'],
            'immediate_response': msg
        })


def select_handler(event, context):
    """Handle image selection from user."""
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})
    
    drone_id = event['pathParameters']['droneId']
    conversation_id = event['pathParameters']['conversationId']
    
    if not verify_ownership(user_id, drone_id):
        return json_response(403, {'error': 'You do not own this drone'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except json.JSONDecodeError:
        return json_response(400, {'error': 'Invalid JSON'})
    
    option_id = body.get('option_id')
    if option_id is None:
        return json_response(400, {'error': 'Missing option_id'})
    
    # Save selection as user message
    save_message(conversation_id, drone_id, 'user', 'text', f'Option {option_id + 1}')
    
    # Get history to find the pending images/context
    history = get_conversation_history(drone_id, conversation_id)
    
    # Call agent with selection context
    selection_message = f"User selected option {option_id + 1}. Continue with the task."
    agent_response = call_agent(history, selection_message)
    
    action = agent_response.get('action', 'respond')
    
    if action == 'execute':
        code = agent_response.get('code', '')
        if not code:
            try:
                code = generate_code(agent_response.get('message', ''), conversation_id)
            except Exception as e:
                error_text = f"Sorry, I couldn't process that command. AI service error: {str(e)}"
                error_msg = save_message(conversation_id, drone_id, 'drone', 'text', error_text)
                publish_to_app(drone_id, conversation_id, 'error', error_text)
                return json_response(200, {'status': 'error', 'message_id': error_msg['id'], 'immediate_response': error_msg})
        
        loading_msg = save_message(
            conversation_id, drone_id, 'drone', 'loading',
            agent_response.get('message', 'On it...')
        )
        
        publish_to_drone(drone_id, conversation_id, {
            'action': 'execute',
            'code': code,
            'conversation_id': conversation_id,
            'selected_option': option_id
        })
        
        return json_response(200, {
            'status': 'sent',
            'message_id': loading_msg['id'],
            'immediate_response': loading_msg
        })
    
    else:
        msg = save_message(
            conversation_id, drone_id, 'drone', 'text',
            agent_response.get('message', 'Understood.')
        )
        
        publish_to_app(drone_id, conversation_id, 'text', msg['content']['text'])
        
        return json_response(200, {
            'status': 'sent',
            'message_id': msg['id'],
            'immediate_response': msg
        })


def response_handler(event, context):
    """Handle response from drone (via IoT Rule)."""
    # This is triggered by IoT Rule when drone publishes to chat response topic
    
    # The payload comes directly from IoT
    payload = event
    print(f"📥 Drone response received: {json.dumps(payload)[:500]}")
    
    drone_id = payload.get('droneId')
    conversation_id = payload.get('conversation_id')
    
    if not drone_id or not conversation_id:
        print(f"❌ Missing drone_id or conversation_id in payload: {payload}")
        return
    
    result = payload.get('result', {})
    image_urls = payload.get('image_urls', [])
    original_message = payload.get('original_message', '')
    follow_up = payload.get('follow_up', False)
    
    print(f"📋 Processing: drone={drone_id}, conv={conversation_id}, images={len(image_urls)}, follow_up={follow_up}")
    print(f"📋 Result: success={result.get('success')}, stdout={result.get('stdout', '')[:200]}")
    
    # Get conversation history
    history = get_conversation_history(drone_id, conversation_id)
    
    if follow_up and image_urls:
        # Drone sent images for analysis - call agent to decide what to show user
        analysis_prompt = f"""The drone has captured these images while looking around:
{json.dumps(image_urls)}

Original user request: {original_message}
Execution result: {json.dumps(result)}

Analyze the situation and respond appropriately. If multiple options match what the user was looking for, present them as choices."""
        
        agent_response = call_agent(history, analysis_prompt, pending_images=image_urls)
        action = agent_response.get('action', 'respond')
        
        if action == 'ask' or len(image_urls) > 1:
            # Show options to user
            save_message(
                conversation_id, drone_id, 'drone', 'image_choice',
                agent_response.get('message', 'I found these. Which one?'),
                image_urls=image_urls
            )
            
            publish_to_app(
                drone_id, conversation_id, 'image_choice',
                agent_response.get('message', 'I found these. Which one?'),
                image_urls=image_urls,
                image_options=[
                    {'id': i, 'url': url, 'description': None}
                    for i, url in enumerate(image_urls)
                ]
            )
        else:
            # Single result or just text response
            msg_content = agent_response.get('message', 'Here\'s what I found.')
            content_type = 'image' if image_urls else 'text'
            
            save_message(
                conversation_id, drone_id, 'drone', content_type,
                msg_content,
                image_urls=image_urls if image_urls else None
            )
            
            publish_to_app(
                drone_id, conversation_id, content_type,
                msg_content,
                image_urls=image_urls if image_urls else None
            )
    
    elif image_urls:
        # Drone sent a single image (e.g., photo of the sign)
        # Analyze the image if we have vision capability
        analysis_prompt = f"""The drone has captured this image: {image_urls[0]}
Original user request: {original_message}
Execution result: {json.dumps(result)}

Describe what you see in the image and respond to the user's request."""
        
        agent_response = call_agent(history, analysis_prompt, pending_images=image_urls)
        
        save_message(
            conversation_id, drone_id, 'drone', 'image',
            agent_response.get('message', 'Here\'s the photo.'),
            image_urls=image_urls
        )
        
        publish_to_app(
            drone_id, conversation_id, 'image',
            agent_response.get('message', 'Here\'s the photo.'),
            image_urls=image_urls
        )
    
    else:
        # No images, just execution result
        if result.get('success'):
            msg = f"Done! {result.get('stdout', '')}"
        else:
            msg = f"There was an issue: {result.get('stderr', result.get('error', 'Unknown error'))}"
        
        print(f"💬 Saving text message: {msg[:100]}")
        save_message(conversation_id, drone_id, 'drone', 'text', msg)
        publish_to_app(drone_id, conversation_id, 'text', msg)
        print(f"✅ Message saved and published to app")


def advice_handler(event, context):
    """Handle advice request from drone's on-device LLM (via IoT Rule).
    
    When the drone's on-device intelligence is stuck and asks for help,
    this handler processes the request and returns advice.
    """
    payload = event
    print(f"📥 Advice request received: {json.dumps(payload)[:500]}")
    
    drone_id = payload.get('droneId') or payload.get('drone_id')
    conversation_id = payload.get('conversation_id')
    question = payload.get('question', '')
    current_state = payload.get('current_state', '')
    goal = payload.get('goal', {})
    
    if not drone_id or not conversation_id:
        print(f"❌ Missing drone_id or conversation_id in advice request")
        return
    
    publish_log(drone_id, "INFO", f"On-device LLM asking for advice: {question[:200]}")
    
    # Build prompt for Claude to provide advice
    advice_prompt = f"""The drone's on-device AI is executing a goal and needs your advice.

GOAL:
Objective: {goal.get('objective', 'unknown')}
Success criteria: {goal.get('success_criteria', [])}
Constraints: {goal.get('constraints', [])}
Priority: {goal.get('priority', 'safety')}

CURRENT STATE:
{current_state}

THE DRONE IS ASKING:
{question}

Provide brief, actionable advice (1-3 sentences). Don't give specific code - give guidance the drone can use to figure out what to do next."""

    try:
        response = bedrock.invoke_model(
            modelId=BEDROCK_MODEL_ID,
            body=json.dumps({
                'anthropic_version': ANTHROPIC_VERSION,
                'system': 'You are helping a drone that is stuck while executing an autonomous task. Provide brief, practical advice.',
                'messages': [
                    {'role': 'user', 'content': [{'type': 'text', 'text': advice_prompt}]}
                ],
                'max_tokens': 256
            })
        )
        
        result = json.loads(response['body'].read())
        advice = ''.join(
            block.get('text', '') for block in result.get('content', [])
            if block.get('type') == 'text'
        ).strip()
        
        print(f"💬 Generated advice: {advice[:200]}")
        
    except Exception as e:
        print(f"❌ Error generating advice: {e}")
        advice = "Continue with your best judgment based on the goal and current state."
    
    # Send advice back to drone
    response_topic = f'drone/{drone_id}/advice/response'
    advice_payload = {
        'droneId': drone_id,
        'conversation_id': conversation_id,
        'advice': advice,
        'timestamp': datetime.now(timezone.utc).isoformat()
    }
    
    iot.publish(
        topic=response_topic,
        qos=1,
        payload=json.dumps(advice_payload)
    )
    
    publish_log(drone_id, "INFO", f"Advice sent: {advice[:100]}")


def upload_url_handler(event, context):
    """Generate pre-signed URL for drone to upload images."""
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})
    
    drone_id = event['pathParameters']['droneId']
    conversation_id = event['pathParameters']['conversationId']
    
    if not verify_ownership(user_id, drone_id):
        return json_response(403, {'error': 'You do not own this drone'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except json.JSONDecodeError:
        return json_response(400, {'error': 'Invalid JSON'})
    
    filename = body.get('filename', f'{uuid.uuid4()}.jpg')
    
    # Generate S3 key
    timestamp = datetime.now(timezone.utc).strftime('%Y%m%d_%H%M%S')
    s3_key = f'drones/{drone_id}/conversations/{conversation_id}/{timestamp}_{filename}'
    
    # Generate pre-signed upload URL
    upload_url = s3.generate_presigned_url(
        'put_object',
        Params={
            'Bucket': IMAGES_BUCKET,
            'Key': s3_key,
            'ContentType': 'image/jpeg'
        },
        ExpiresIn=300  # 5 minutes
    )
    
    # Public URL for viewing
    image_url = f'https://{IMAGES_BUCKET}.s3.amazonaws.com/{s3_key}'
    
    return json_response(200, {
        'upload_url': upload_url,
        'image_url': image_url
    })

