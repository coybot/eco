"""
Kinesis Video Streams WebRTC Signaling Lambda Handlers.

Manages signaling channels for live video streaming from drones to iOS app.
"""

import json
import os
import hashlib
import hmac
import boto3
from botocore.exceptions import ClientError
from datetime import datetime, timezone
from urllib.parse import urlparse, quote

# Initialize clients
kvs_region_env = os.environ.get("KVS_REGION")
KVS_REGION = kvs_region_env or os.environ.get("AWS_REGION") or os.environ.get("AWS_DEFAULT_REGION") or "us-west-2"
kvs = boto3.client('kinesisvideo', region_name=KVS_REGION)
dynamodb = boto3.resource('dynamodb')
drone_table = dynamodb.Table(os.environ.get('DRONE_TABLE', 'drone-registry-dev'))
ENVIRONMENT = os.environ.get('ENVIRONMENT', 'dev')


def create_presigned_wss_url(wss_endpoint: str, channel_arn: str, client_id: str = "ios-viewer") -> str:
    """
    Create a presigned WebSocket URL for Kinesis Video Signaling.
    Uses Lambda's execution role credentials.
    """
    # Get credentials from boto3 session (Lambda execution role)
    session = boto3.Session()
    credentials = session.get_credentials()
    frozen_creds = credentials.get_frozen_credentials()
    
    access_key = frozen_creds.access_key
    secret_key = frozen_creds.secret_key
    session_token = frozen_creds.token
    
    parsed = urlparse(wss_endpoint)
    host = parsed.netloc
    
    t = datetime.now(timezone.utc)
    amz_date = t.strftime("%Y%m%dT%H%M%SZ")
    date_stamp = t.strftime("%Y%m%d")
    
    service = "kinesisvideo"
    method = "GET"
    canonical_uri = "/"
    
    algorithm = "AWS4-HMAC-SHA256"
    credential_scope = f"{date_stamp}/{KVS_REGION}/{service}/aws4_request"
    
    # Query parameters
    query_params = {
        "X-Amz-Algorithm": algorithm,
        "X-Amz-ChannelARN": channel_arn,
        "X-Amz-ClientId": client_id,
        "X-Amz-Credential": f"{access_key}/{credential_scope}",
        "X-Amz-Date": amz_date,
        "X-Amz-Expires": "299",
        "X-Amz-SignedHeaders": "host",
    }
    
    if session_token:
        query_params["X-Amz-Security-Token"] = session_token
    
    # Create canonical query string (sorted)
    canonical_querystring = "&".join([
        f"{quote(k, safe='')}={quote(v, safe='~')}" 
        for k, v in sorted(query_params.items())
    ])
    
    # Canonical headers
    canonical_headers = f"host:{host}\n"
    signed_headers = "host"
    
    # Payload hash (empty for GET)
    payload_hash = hashlib.sha256(b"").hexdigest()
    
    # Create canonical request
    canonical_request = f"{method}\n{canonical_uri}\n{canonical_querystring}\n{canonical_headers}\n{signed_headers}\n{payload_hash}"
    
    # String to sign
    string_to_sign = f"{algorithm}\n{amz_date}\n{credential_scope}\n{hashlib.sha256(canonical_request.encode()).hexdigest()}"
    
    # Signing key
    def sign(key, msg):
        return hmac.new(key, msg.encode(), hashlib.sha256).digest()
    
    k_date = sign(f"AWS4{secret_key}".encode(), date_stamp)
    k_region = sign(k_date, KVS_REGION)
    k_service = sign(k_region, service)
    k_signing = sign(k_service, "aws4_request")
    
    signature = hmac.new(k_signing, string_to_sign.encode(), hashlib.sha256).hexdigest()
    
    # Add signature
    query_params["X-Amz-Signature"] = signature
    
    # Build final URL
    final_querystring = "&".join([
        f"{quote(k, safe='')}={quote(v, safe='~')}" 
        for k, v in sorted(query_params.items())
    ])
    
    return f"{wss_endpoint}?{final_querystring}"


def get_signaling_client(endpoint: str):
    """Get a signaling channels client for a specific endpoint."""
    return boto3.client(
        'kinesis-video-signaling',
        region_name=KVS_REGION,
        endpoint_url=endpoint
    )


def get_channel_name(drone_id: str) -> str:
    """Generate consistent channel name for a drone."""
    return f"drone-{drone_id}-{ENVIRONMENT}"


def create_or_get_signaling_channel(drone_id: str) -> dict:
    """
    Create a signaling channel for a drone if it doesn't exist.
    Returns channel ARN and endpoints.
    """
    channel_name = get_channel_name(drone_id)
    
    # Try to describe existing channel first
    try:
        response = kvs.describe_signaling_channel(ChannelName=channel_name)
        channel_info = response['ChannelInfo']
        channel_arn = channel_info['ChannelARN']
        print(f"Found existing channel: {channel_name}")
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            # Create new channel
            print(f"Creating new signaling channel: {channel_name}")
            response = kvs.create_signaling_channel(
                ChannelName=channel_name,
                ChannelType='SINGLE_MASTER',
                SingleMasterConfiguration={
                    'MessageTtlSeconds': 60
                },
                Tags=[
                    {'Key': 'droneId', 'Value': drone_id},
                    {'Key': 'environment', 'Value': ENVIRONMENT}
                ]
            )
            channel_arn = response['ChannelARN']
            print(f"Created channel: {channel_arn}")
        else:
            raise
    
    # Get signaling endpoints
    endpoints_response = kvs.get_signaling_channel_endpoint(
        ChannelARN=channel_arn,
        SingleMasterChannelEndpointConfiguration={
            'Protocols': ['WSS', 'HTTPS'],
            'Role': 'MASTER'
        }
    )
    
    endpoints = {ep['Protocol']: ep['ResourceEndpoint'] 
                 for ep in endpoints_response['ResourceEndpointList']}
    
    return {
        'channelName': channel_name,
        'channelARN': channel_arn,
        'region': KVS_REGION,
        'endpoints': endpoints
    }


def get_viewer_endpoints(drone_id: str) -> dict:
    """
    Get signaling channel endpoints for a viewer (iOS app).
    """
    channel_name = get_channel_name(drone_id)
    
    # Get channel ARN
    response = kvs.describe_signaling_channel(ChannelName=channel_name)
    channel_arn = response['ChannelInfo']['ChannelARN']
    
    # Get viewer endpoints
    endpoints_response = kvs.get_signaling_channel_endpoint(
        ChannelARN=channel_arn,
        SingleMasterChannelEndpointConfiguration={
            'Protocols': ['WSS', 'HTTPS'],
            'Role': 'VIEWER'
        }
    )
    
    endpoints = {ep['Protocol']: ep['ResourceEndpoint'] 
                 for ep in endpoints_response['ResourceEndpointList']}
    
    # Get ICE server config using the signaling client
    # The ICE server API requires calling the HTTPS endpoint
    https_endpoint = endpoints.get('HTTPS')
    if https_endpoint:
        signaling_client = get_signaling_client(https_endpoint)
        ice_response = signaling_client.get_ice_server_config(
            ChannelARN=channel_arn,
            ClientId='ios-viewer',
            Service='TURN'
        )
        
        ice_servers = []
        for config in ice_response['IceServerList']:
            ice_servers.append({
                'urls': config['Uris'],
                'username': config.get('Username'),
                'credential': config.get('Password'),
                'ttl': config.get('Ttl', 300)
            })
    else:
        ice_servers = []
    
    # Create a presigned WSS URL for the iOS viewer
    wss_endpoint = endpoints.get('WSS', '')
    client_id = f"viewer-{os.urandom(4).hex()}"
    signed_wss_url = create_presigned_wss_url(wss_endpoint, channel_arn, client_id) if wss_endpoint else ''
    
    return {
        'channelName': channel_name,
        'channelARN': channel_arn,
        'region': KVS_REGION,
        'endpoints': endpoints,
        'signedWssUrl': signed_wss_url,
        'clientId': client_id,
        'iceServers': ice_servers
    }


def verify_drone_ownership(user_id: str, drone_id: str) -> bool:
    """Verify the user owns this drone."""
    try:
        response = drone_table.get_item(
            Key={'userId': user_id, 'droneId': drone_id}
        )
        return 'Item' in response
    except Exception as e:
        print(f"Error checking drone ownership: {e}")
        return False


def signaling_handler(event, context):
    """
    GET /drones/{droneId}/video/signaling
    
    Returns signaling channel info for the drone (master role).
    Called by the drone to get WebRTC signaling endpoints.
    """
    print(f"Event: {json.dumps(event)}")
    
    # Get user from authorizer
    user_id = event.get('requestContext', {}).get('authorizer', {}).get('principalId')
    if not user_id:
        return {
            'statusCode': 401,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': 'Unauthorized'})
        }
    
    # Get drone ID from path
    drone_id = event.get('pathParameters', {}).get('droneId')
    if not drone_id:
        return {
            'statusCode': 400,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': 'Missing droneId'})
        }
    
    # Verify ownership
    if not verify_drone_ownership(user_id, drone_id):
        return {
            'statusCode': 403,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': 'Drone not found or not owned by user'})
        }
    
    try:
        channel_info = create_or_get_signaling_channel(drone_id)
        
        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({
                'success': True,
                'role': 'MASTER',
                **channel_info
            })
        }
    except Exception as e:
        print(f"Error: {e}")
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': str(e)})
        }


def viewer_handler(event, context):
    """
    GET /drones/{droneId}/video/viewer
    
    Returns signaling channel info for viewer (iOS app).
    Includes ICE server configuration for NAT traversal.
    """
    print(f"Event: {json.dumps(event)}")
    
    # Get user from authorizer
    user_id = event.get('requestContext', {}).get('authorizer', {}).get('principalId')
    if not user_id:
        return {
            'statusCode': 401,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': 'Unauthorized'})
        }
    
    # Get drone ID from path
    drone_id = event.get('pathParameters', {}).get('droneId')
    if not drone_id:
        return {
            'statusCode': 400,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': 'Missing droneId'})
        }
    
    # Verify ownership
    if not verify_drone_ownership(user_id, drone_id):
        return {
            'statusCode': 403,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': 'Drone not found or not owned by user'})
        }
    
    try:
        viewer_info = get_viewer_endpoints(drone_id)
        
        return {
            'statusCode': 200,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({
                'success': True,
                'role': 'VIEWER',
                **viewer_info
            })
        }
    except ClientError as e:
        if e.response['Error']['Code'] == 'ResourceNotFoundException':
            return {
                'statusCode': 404,
                'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
                'body': json.dumps({'error': 'Signaling channel not found. Drone may not be streaming.'})
            }
        raise
    except Exception as e:
        print(f"Error: {e}")
        return {
            'statusCode': 500,
            'headers': {'Content-Type': 'application/json', 'Access-Control-Allow-Origin': '*'},
            'body': json.dumps({'error': str(e)})
        }

