"""
Drone registration and management Lambda handlers.
"""

import json
import os
import time
import boto3
from datetime import datetime, timezone
from decimal import Decimal
import uuid

dynamodb = boto3.resource('dynamodb')
AWS_REGION = os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"
iot_data = boto3.client('iot-data', region_name=AWS_REGION)
iot = boto3.client('iot', region_name=AWS_REGION)
DRONE_TABLE = os.environ.get('DRONE_TABLE', 'drone-registry-dev')
STATUS_TABLE = os.environ.get('STATUS_TABLE', 'drone-status-dev')
LOGS_TABLE = os.environ.get('LOGS_TABLE', 'drone-logs-dev')
IOT_POLICY_NAME = os.environ.get('IOT_POLICY_NAME', 'drone-cognito-policy-dev')
STATUS_TTL_SECONDS = int(os.environ.get("STATUS_TTL_SECONDS", "30"))


def get_user_id(event):
    """Extract user ID from Lambda authorizer context."""
    try:
        authorizer = event['requestContext']['authorizer']
        # Lambda authorizer puts userId in context
        return authorizer.get('userId') or authorizer.get('principalId')
    except (KeyError, TypeError):
        return None


def decimal_to_num(obj):
    """Convert Decimal to int or float for JSON serialization."""
    if isinstance(obj, Decimal):
        if obj % 1 == 0:
            return int(obj)
        return float(obj)
    return str(obj)  # Fallback for other types like datetime


def _to_dynamodb_types(value):
    """Convert floats to Decimal recursively for DynamoDB."""
    if isinstance(value, float):
        return Decimal(str(value))
    if isinstance(value, dict):
        return {k: _to_dynamodb_types(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_to_dynamodb_types(v) for v in value]
    return value


def json_response(status_code, body):
    """Create API Gateway response."""
    return {
        'statusCode': status_code,
        'headers': {
            'Content-Type': 'application/json',
            'Access-Control-Allow-Origin': '*',
            'Access-Control-Allow-Headers': 'Content-Type,Authorization',
        },
        'body': json.dumps(body, default=decimal_to_num)
    }


def register_handler(event, context):
    """
    POST /drones - Register a drone to the authenticated user.
    
    Body: { "droneId": "drone-001", "name": "My Drone" }
    """
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except json.JSONDecodeError:
        return json_response(400, {'error': 'Invalid JSON'})
    
    drone_id = body.get('droneId')
    drone_name = body.get('name', drone_id)
    
    if not drone_id:
        return json_response(400, {'error': 'Missing droneId'})
    
    # Check if drone is already registered to someone else
    table = dynamodb.Table(DRONE_TABLE)
    
    try:
        # Query the GSI to see if this drone is already registered
        response = table.query(
            IndexName='droneId-index',
            KeyConditionExpression='droneId = :did',
            ExpressionAttributeValues={':did': drone_id}
        )
        
        if response.get('Items'):
            existing = response['Items'][0]
            if existing['userId'] != user_id:
                return json_response(409, {'error': 'Drone already registered to another user'})
            else:
                return json_response(200, {
                    'message': 'Drone already registered to you',
                    'drone': existing
                })
        
        # Register the drone
        item = {
            'userId': user_id,
            'droneId': drone_id,
            'name': drone_name,
            'registeredAt': datetime.utcnow().isoformat(),
            'status': 'registered'
        }
        
        table.put_item(Item=item)
        
        return json_response(201, {
            'message': 'Drone registered successfully',
            'drone': item
        })
        
    except Exception as e:
        return json_response(500, {'error': f'Database error: {str(e)}'})


def list_handler(event, context):
    """
    GET /drones - List all drones registered to the authenticated user.
    """
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})
    
    table = dynamodb.Table(DRONE_TABLE)
    
    try:
        response = table.query(
            KeyConditionExpression='userId = :uid',
            ExpressionAttributeValues={':uid': user_id}
        )
        
        drones = response.get('Items', [])
        
        return json_response(200, {
            'drones': drones,
            'count': len(drones)
        })
        
    except Exception as e:
        return json_response(500, {'error': f'Database error: {str(e)}'})


def delete_handler(event, context):
    """
    DELETE /drones/{droneId} - Unregister a drone.
    
    Also sends a factory reset command to the drone so it can be re-provisioned.
    """
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})
    
    drone_id = event.get('pathParameters', {}).get('droneId')
    if not drone_id:
        return json_response(400, {'error': 'Missing droneId'})
    
    table = dynamodb.Table(DRONE_TABLE)
    
    try:
        # Verify ownership before deleting
        response = table.get_item(
            Key={'userId': user_id, 'droneId': drone_id}
        )
        
        if 'Item' not in response:
            return json_response(404, {'error': 'Drone not found or not owned by you'})

        # Offline-safe decommission: write desired state into Thing Shadow.
        # This persists across disconnects and will be applied when the device reconnects.
        reset_nonce = uuid.uuid4().hex
        requested_at = datetime.now(timezone.utc).isoformat()
        try:
            iot_data.update_thing_shadow(
                thingName=drone_id,
                payload=json.dumps({
                    "state": {
                        "desired": {
                            "decommission": {
                                "action": "factory_reset",
                                "nonce": reset_nonce,
                                "requestedAt": requested_at,
                                "reason": "Drone deleted by user"
                            }
                        }
                    }
                }).encode("utf-8")
            )
        except Exception as e:
            # Log but don't fail delete - device may not exist / shadow may not be reachable
            print(f"Could not update Thing Shadow for {drone_id}: {e}")
        
        # Send factory reset command to drone via IoT
        try:
            iot_data.publish(
                topic=f'drone/{drone_id}/command',
                qos=1,
                payload=json.dumps({
                    'action': 'factory_reset',
                    'reason': 'Drone deleted by user',
                    'nonce': reset_nonce
                })
            )
        except Exception as e:
            # Log but don't fail - drone may be offline
            print(f"Could not send reset command to {drone_id}: {e}")
        
        # Delete the drone from registry
        table.delete_item(
            Key={'userId': user_id, 'droneId': drone_id}
        )
        
        # Also delete from status table
        try:
            status_table = dynamodb.Table(STATUS_TABLE)
            status_table.delete_item(Key={'droneId': drone_id})
        except Exception:
            pass  # Status may not exist
        
        return json_response(200, {
            'message': 'Drone unregistered successfully',
            'droneId': drone_id
        })
        
    except Exception as e:
        return json_response(500, {'error': f'Database error: {str(e)}'})


def update_handler(event, context):
    """
    PATCH /drones/{droneId} - Update drone fields (currently: name).

    Body: { "name": "New Name" }
    """
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})

    drone_id = event.get('pathParameters', {}).get('droneId')
    if not drone_id:
        return json_response(400, {'error': 'Missing droneId'})

    try:
        body = json.loads(event.get('body', '{}'))
    except json.JSONDecodeError:
        return json_response(400, {'error': 'Invalid JSON'})

    new_name = (body.get('name') or '').strip()
    if not new_name:
        return json_response(400, {'error': 'Missing name'})

    table = dynamodb.Table(DRONE_TABLE)
    try:
        # Verify ownership
        existing = table.get_item(Key={'userId': user_id, 'droneId': drone_id})
        if 'Item' not in existing:
            return json_response(404, {'error': 'Drone not found or not owned by you'})

        updated_at = datetime.now(timezone.utc).isoformat()
        response = table.update_item(
            Key={'userId': user_id, 'droneId': drone_id},
            UpdateExpression='SET #n = :name, #ua = :ua',
            ExpressionAttributeNames={'#n': 'name', '#ua': 'updatedAt'},
            ExpressionAttributeValues={':name': new_name, ':ua': updated_at},
            ReturnValues='ALL_NEW'
        )

        return json_response(200, {
            'drone': response.get('Attributes') or {}
        })
    except Exception as e:
        return json_response(500, {'error': f'Database error: {str(e)}'})


def _normalize_wifi_networks(raw_networks):
    if raw_networks is None:
        return []
    if not isinstance(raw_networks, list):
        raise ValueError("networks must be a list")

    if len(raw_networks) > 20:
        raise ValueError("too many networks (max 20)")

    normalized = []
    seen_ids = set()

    for i, net in enumerate(raw_networks):
        if not isinstance(net, dict):
            raise ValueError(f"network at index {i} must be an object")

        net_id = str(net.get('id') or uuid.uuid4().hex)
        if net_id in seen_ids:
            raise ValueError("duplicate network id")
        seen_ids.add(net_id)

        ssid = (net.get('ssid') or '').strip()
        if not ssid:
            raise ValueError("ssid is required")

        password = net.get('password')
        if password is None:
            password = ""
        password = str(password)

        enabled = net.get('enabled')
        if enabled is None:
            enabled = True
        enabled = bool(enabled)

        prio = net.get('priority')
        try:
            prio = int(prio) if prio is not None else i
        except Exception:
            prio = i

        normalized.append({
            'id': net_id,
            'ssid': ssid,
            'password': password,
            'priority': prio,
            'enabled': enabled
        })

    # Sort and renumber priorities for a stable order
    normalized.sort(key=lambda n: n.get('priority', 0))
    for idx, n in enumerate(normalized):
        n['priority'] = idx

    return normalized


def battery_get_handler(event, context):
    """
    GET /drones/{droneId}/battery-config - Get battery configuration for this drone.
    """
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})

    drone_id = event.get('pathParameters', {}).get('droneId')
    if not drone_id:
        return json_response(400, {'error': 'Missing droneId'})

    table = dynamodb.Table(DRONE_TABLE)
    try:
        response = table.get_item(Key={'userId': user_id, 'droneId': drone_id})
        if 'Item' not in response:
            return json_response(404, {'error': 'Drone not found or not owned by you'})

        item = response['Item']
        battery_config = item.get('batteryConfig') or {
            'cellCount': 4,
            'capacityMah': 5000,
            'cellEmptyVoltage': 3.5,
            'cellFullVoltage': 4.2
        }
        return json_response(200, {
            'droneId': drone_id,
            'batteryConfig': battery_config
        })
    except Exception as e:
        return json_response(500, {'error': f'Database error: {str(e)}'})


def battery_put_handler(event, context):
    """
    PUT /drones/{droneId}/battery-config - Update battery configuration.

    Body: { "cellCount": 4, "capacityMah": 5000, "cellEmptyVoltage": 3.5, "cellFullVoltage": 4.2 }
    """
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})

    drone_id = event.get('pathParameters', {}).get('droneId')
    if not drone_id:
        return json_response(400, {'error': 'Missing droneId'})

    try:
        body = json.loads(event.get('body', '{}'))
    except json.JSONDecodeError:
        return json_response(400, {'error': 'Invalid JSON'})

    # Validate battery config
    cell_count = body.get('cellCount')
    capacity_mah = body.get('capacityMah')
    cell_empty = body.get('cellEmptyVoltage')
    cell_full = body.get('cellFullVoltage')

    if not isinstance(cell_count, int) or cell_count < 1 or cell_count > 14:
        return json_response(400, {'error': 'cellCount must be integer 1-14'})
    if not isinstance(capacity_mah, (int, float)) or capacity_mah < 100 or capacity_mah > 100000:
        return json_response(400, {'error': 'capacityMah must be 100-100000'})
    if not isinstance(cell_empty, (int, float)) or cell_empty < 2.5 or cell_empty > 4.0:
        return json_response(400, {'error': 'cellEmptyVoltage must be 2.5-4.0'})
    if not isinstance(cell_full, (int, float)) or cell_full < 3.5 or cell_full > 4.5:
        return json_response(400, {'error': 'cellFullVoltage must be 3.5-4.5'})
    if cell_empty >= cell_full:
        return json_response(400, {'error': 'cellEmptyVoltage must be less than cellFullVoltage'})

    battery_config = {
        'cellCount': cell_count,
        'capacityMah': int(capacity_mah),
        'cellEmptyVoltage': float(cell_empty),
        'cellFullVoltage': float(cell_full)
    }

    table = dynamodb.Table(DRONE_TABLE)
    try:
        # Verify ownership
        if not verify_ownership(user_id, drone_id):
            return json_response(404, {'error': 'Drone not found or not owned by you'})

        nonce = uuid.uuid4().hex
        requested_at = datetime.now(timezone.utc).isoformat()

        # Persist in DynamoDB
        table.update_item(
            Key={'userId': user_id, 'droneId': drone_id},
            UpdateExpression='SET #bc = :config, #bcn = :nonce, #bua = :ua',
            ExpressionAttributeNames={
                '#bc': 'batteryConfig',
                '#bcn': 'batteryConfigNonce',
                '#bua': 'batteryUpdatedAt',
            },
            ExpressionAttributeValues={
                ':config': _to_dynamodb_types(battery_config),
                ':nonce': nonce,
                ':ua': requested_at,
            }
        )

        # Offline-safe delivery to device via Thing Shadow desired state.
        try:
            iot_data.update_thing_shadow(
                thingName=drone_id,
                payload=json.dumps({
                    "state": {
                        "desired": {
                            "batteryConfig": {
                                "nonce": nonce,
                                "requestedAt": requested_at,
                                **battery_config
                            }
                        }
                    }
                }).encode("utf-8")
            )
        except Exception as e:
            # Do not fail the API request if shadow update fails (device may be offline / missing thing).
            print(f"Could not update Thing Shadow batteryConfig for {drone_id}: {e}")

        return json_response(200, {
            'droneId': drone_id,
            'nonce': nonce,
            'batteryConfig': battery_config
        })
    except Exception as e:
        return json_response(500, {'error': f'Database error: {str(e)}'})


def wifi_get_handler(event, context):
    """
    GET /drones/{droneId}/wifi - Get WiFi networks for this drone.
    """
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})

    drone_id = event.get('pathParameters', {}).get('droneId')
    if not drone_id:
        return json_response(400, {'error': 'Missing droneId'})

    table = dynamodb.Table(DRONE_TABLE)
    try:
        response = table.get_item(Key={'userId': user_id, 'droneId': drone_id})
        if 'Item' not in response:
            return json_response(404, {'error': 'Drone not found or not owned by you'})

        item = response['Item']
        networks = item.get('wifiNetworks') or []
        return json_response(200, {
            'droneId': drone_id,
            'networks': networks
        })
    except Exception as e:
        return json_response(500, {'error': f'Database error: {str(e)}'})


def wifi_put_handler(event, context):
    """
    PUT /drones/{droneId}/wifi - Replace full WiFi networks list.

    Body: { "networks": [ { "id": "...", "ssid": "...", "password": "...", "priority": 0, "enabled": true }, ... ] }
    """
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})

    drone_id = event.get('pathParameters', {}).get('droneId')
    if not drone_id:
        return json_response(400, {'error': 'Missing droneId'})

    try:
        body = json.loads(event.get('body', '{}'))
    except json.JSONDecodeError:
        return json_response(400, {'error': 'Invalid JSON'})

    try:
        networks = _normalize_wifi_networks(body.get('networks'))
    except Exception as e:
        return json_response(400, {'error': str(e)})

    table = dynamodb.Table(DRONE_TABLE)
    try:
        # Verify ownership
        if not verify_ownership(user_id, drone_id):
            return json_response(404, {'error': 'Drone not found or not owned by you'})

        nonce = uuid.uuid4().hex
        requested_at = datetime.now(timezone.utc).isoformat()

        # Persist in DynamoDB
        table.update_item(
            Key={'userId': user_id, 'droneId': drone_id},
            UpdateExpression='SET #wn = :nets, #wcn = :nonce, #wua = :ua',
            ExpressionAttributeNames={
                '#wn': 'wifiNetworks',
                '#wcn': 'wifiConfigNonce',
                '#wua': 'wifiUpdatedAt',
            },
            ExpressionAttributeValues={
                ':nets': networks,
                ':nonce': nonce,
                ':ua': requested_at,
            }
        )

        # Offline-safe delivery to device via Thing Shadow desired state.
        try:
            iot_data.update_thing_shadow(
                thingName=drone_id,
                payload=json.dumps({
                    "state": {
                        "desired": {
                            "wifiConfig": {
                                "nonce": nonce,
                                "requestedAt": requested_at,
                                "networks": networks
                            }
                        }
                    }
                }).encode("utf-8")
            )
        except Exception as e:
            # Do not fail the API request if shadow update fails (device may be offline / missing thing).
            print(f"Could not update Thing Shadow wifiConfig for {drone_id}: {e}")

        return json_response(200, {
            'droneId': drone_id,
            'nonce': nonce,
            'networks': networks
        })
    except Exception as e:
        return json_response(500, {'error': f'Database error: {str(e)}'})


def status_handler(event, context):
    """
    GET /drones/{droneId}/status - Get the last known status of a drone.
    """
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})
    
    drone_id = event.get('pathParameters', {}).get('droneId')
    if not drone_id:
        return json_response(400, {'error': 'Missing droneId'})
    
    # Verify ownership
    registry_table = dynamodb.Table(DRONE_TABLE)
    
    try:
        response = registry_table.get_item(
            Key={'userId': user_id, 'droneId': drone_id}
        )
        
        if 'Item' not in response:
            return json_response(404, {'error': 'Drone not found or not owned by you'})
        
        # Get latest status from status table
        status_table = dynamodb.Table(STATUS_TABLE)
        status_response = status_table.get_item(
            Key={'droneId': drone_id}
        )
        
        if 'Item' in status_response:
            status = dict(status_response['Item'])
            # Prefer TTL when present (seconds since epoch)
            ttl = status.get('ttl')
            if ttl:
                try:
                    ttl_int = int(ttl)
                    status['isOnline'] = ttl_int > int(time.time())
                except (ValueError, TypeError):
                    status['isOnline'] = False
            else:
                # Fallback: lastUpdate epoch ms within STATUS_TTL_SECONDS
                last_update = status.get('lastUpdate')
                if last_update:
                    try:
                        last_update_ms = float(last_update)
                        now_ms = time.time() * 1000
                        age_seconds = (now_ms - last_update_ms) / 1000
                        status['isOnline'] = age_seconds < STATUS_TTL_SECONDS
                    except (ValueError, TypeError):
                        status['isOnline'] = False
                else:
                    status['isOnline'] = False
        else:
            status = {
                'droneId': drone_id,
                'status': 'unknown',
                'isOnline': False,
                'message': 'No status updates received yet'
            }

        # Fallback: if status is offline/unknown, check latest log activity
        if not status.get('isOnline'):
            try:
                logs_table = dynamodb.Table(LOGS_TABLE)
                logs_resp = logs_table.query(
                    KeyConditionExpression=boto3.dynamodb.conditions.Key('droneId').eq(drone_id),
                    ScanIndexForward=False,
                    Limit=1
                )
                items = logs_resp.get('Items') or []
                if items:
                    last_ts = items[0].get('timestamp', '')
                    if isinstance(last_ts, str) and "_" in last_ts:
                        last_ms = int(last_ts.split("_")[-1])
                        age_seconds = (time.time() * 1000 - last_ms) / 1000
                        status['isOnline'] = age_seconds < STATUS_TTL_SECONDS
                        if status['isOnline']:
                            status['status'] = 'online'
            except Exception:
                pass
        
        return json_response(200, {
            'drone': response['Item'],
            'status': status
        })
        
    except Exception as e:
        return json_response(500, {'error': f'Database error: {str(e)}'})


def store_status_handler(event, context):
    """
    IoT Rule handler to store status updates in DynamoDB.
    Expects payload to include droneId (or will use topic(2) injected by the rule).
    """
    try:
        now = int(time.time())
        now_ms = int(time.time() * 1000)

        drone_id = event.get('droneId') or event.get('drone_id')
        if not drone_id:
            # Rule should inject topic(2) as droneId
            return {"ok": False, "error": "missing droneId"}

        payload = dict(event)
        payload['droneId'] = drone_id

        if not payload.get('lastUpdate'):
            payload['lastUpdate'] = now_ms
        if not payload.get('ttl'):
            payload['ttl'] = now + STATUS_TTL_SECONDS
        if 'status' not in payload:
            payload['status'] = 'online'

        status_table = dynamodb.Table(STATUS_TABLE)
        status_table.put_item(Item=_to_dynamodb_types(payload))
        return {"ok": True}
    except Exception as e:
        return {"ok": False, "error": str(e)}


def verify_ownership(user_id, drone_id):
    """Helper function to verify if a user owns a drone."""
    table = dynamodb.Table(DRONE_TABLE)
    
    try:
        response = table.get_item(
            Key={'userId': user_id, 'droneId': drone_id}
        )
        return 'Item' in response
    except Exception:
        return False


def store_logs_handler(event, context):
    """
    Store drone logs from IoT Rule.
    Triggered by IoT Rule on drone/+/logs topic.
    """
    table = dynamodb.Table(LOGS_TABLE)
    
    try:
        drone_id = event.get('droneId')
        if not drone_id:
            print(f"Missing droneId in log event: {event}")
            return
        
        # Create timestamp with microseconds for uniqueness
        timestamp = event.get('timestamp') or datetime.now(timezone.utc).isoformat()
        received_at = event.get('receivedAt', 0)
        
        # Add microsecond precision to ensure uniqueness
        if received_at:
            timestamp = f"{timestamp}_{received_at}"
        
        item = {
            'droneId': drone_id,
            'timestamp': timestamp,
            'level': event.get('level', 'INFO'),
            'source': event.get('source', 'unknown'),
            'message': event.get('message', ''),
            'ttl': int(datetime.now(timezone.utc).timestamp()) + (24 * 60 * 60)  # 24 hours
        }
        
        if event.get('code'):
            item['code'] = event['code']
        if event.get('logger'):
            item['logger'] = event['logger']
        
        table.put_item(Item=item)
        
    except Exception as e:
        print(f"Error storing log: {e}")


def get_logs_handler(event, context):
    """
    GET /drones/{droneId}/logs - Get recent logs from a drone.
    Query params:
      - limit: max number of logs (default 100)
      - since: ISO timestamp to get logs after
    """
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})
    
    drone_id = event.get('pathParameters', {}).get('droneId')
    if not drone_id:
        return json_response(400, {'error': 'Missing droneId'})
    
    # Verify ownership
    if not verify_ownership(user_id, drone_id):
        return json_response(404, {'error': 'Drone not found or not owned by you'})
    
    # Get query parameters
    query_params = event.get('queryStringParameters') or {}
    limit = min(int(query_params.get('limit', 100)), 500)
    since = query_params.get('since')
    
    table = dynamodb.Table(LOGS_TABLE)
    
    try:
        query_kwargs = {
            'KeyConditionExpression': 'droneId = :did',
            'ExpressionAttributeValues': {':did': drone_id},
            'ScanIndexForward': False,  # Most recent first
            'Limit': limit
        }
        
        if since:
            query_kwargs['KeyConditionExpression'] += ' AND #ts > :since'
            query_kwargs['ExpressionAttributeValues'][':since'] = since
            query_kwargs['ExpressionAttributeNames'] = {'#ts': 'timestamp'}
        
        response = table.query(**query_kwargs)
        
        logs = []
        for item in response.get('Items', []):
            log = {
                'timestamp': item['timestamp'].split('_')[0],  # Remove uniqueness suffix
                'level': item.get('level', 'INFO'),
                'source': item.get('source', 'unknown'),
                'message': item.get('message', '')
            }
            if item.get('code'):
                log['code'] = item['code']
            logs.append(log)
        
        # Return in chronological order (oldest first)
        logs.reverse()
        
        return json_response(200, {
            'droneId': drone_id,
            'logs': logs,
            'count': len(logs)
        })
        
    except Exception as e:
        return json_response(500, {'error': f'Database error: {str(e)}'})


def attach_iot_policy_handler(event, context):
    """
    POST /auth/iot-policy - Attach IoT policy to Cognito Identity for MQTT access.
    
    This must be called after the iOS app gets Cognito credentials.
    Body: { "identityId": "us-west-2:xxxxxxxx-xxxx-xxxx-xxxx-xxxxxxxxxxxx" }
    """
    user_id = get_user_id(event)
    if not user_id:
        return json_response(401, {'error': 'Unauthorized'})
    
    try:
        body = json.loads(event.get('body', '{}'))
    except json.JSONDecodeError:
        return json_response(400, {'error': 'Invalid JSON'})
    
    identity_id = body.get('identityId', '').strip()
    if not identity_id:
        return json_response(400, {'error': 'Missing identityId'})
    
    # Basic validation: identity ID should look like "us-west-2:uuid"
    if ':' not in identity_id or len(identity_id) < 20:
        return json_response(400, {'error': 'Invalid identityId format'})
    
    try:
        # Check if policy is already attached
        try:
            attached = iot.list_attached_policies(target=identity_id)
            policy_names = [p['policyName'] for p in attached.get('policies', [])]
            
            if IOT_POLICY_NAME in policy_names:
                return json_response(200, {
                    'message': 'IoT policy already attached',
                    'identityId': identity_id,
                    'policyName': IOT_POLICY_NAME
                })
        except Exception:
            pass  # If list fails, try to attach anyway
        
        # Attach the IoT policy to this Cognito identity
        iot.attach_policy(
            policyName=IOT_POLICY_NAME,
            target=identity_id
        )
        
        print(f"Attached IoT policy {IOT_POLICY_NAME} to {identity_id} for user {user_id}")
        
        return json_response(200, {
            'message': 'IoT policy attached successfully',
            'identityId': identity_id,
            'policyName': IOT_POLICY_NAME
        })
        
    except iot.exceptions.ResourceNotFoundException:
        return json_response(404, {'error': f'IoT policy {IOT_POLICY_NAME} not found'})
    except Exception as e:
        print(f"Error attaching IoT policy: {e}")
        return json_response(500, {'error': f'Failed to attach IoT policy: {str(e)}'})

