# WiFi provisioning (hotspot onboarding)

How a drone with no WiFi configured gets set up from the iOS/Android app,
implemented in [`drone/common/provisioning.py`](../drone/common/provisioning.py).

## Flow

1. **Boot with no WiFi configured** → the drone starts a WPA2/WPA3 hotspot
   (`Presidio-<model>-<last4>` by default) for `provisioning_timeout` seconds
   (60s default, see [`config-reference.md`](config-reference.md)).
2. **User connects** to that hotspot using the password shown on the drone
   (or in its setup materials).
3. **App calls `/configure`** with the target WiFi SSID/password and the
   hotspot password as a Bearer token.
4. **Drone saves credentials**, connects to the target WiFi, and (in AWS
   mode) registers with the cloud via Fleet Provisioning — see
   [`certificates.md`](certificates.md).

## The drone's provisioning HTTP contract

Served on port 80 while the hotspot is up, no TLS (it's a closed AP with a
WPA2/WPA3 password as the actual security boundary).

### `GET /` or `GET /status`

```json
{"status": "provisioning", "drone_id": "drone-abc123", "message": "Ready to receive WiFi configuration"}
```

### `GET /info`

```json
{"drone_id": "drone-abc123", "mac_address": "aa:bb:cc:dd:ee:ff", "hotspot_name": "Presidio-quad-ee01"}
```

### `POST /configure`

Requires `Authorization: Bearer <hotspot_password>`.

Request:
```json
{"ssid": "MyHomeWiFi", "password": "wifi-password-here", "user_id": "google_123456"}
```

- `ssid`: required, max 32 characters.
- `password`: required, 8–63 characters (WPA2 range).
- `user_id`: optional — identifies who's claiming the drone (AWS mode; used
  for ownership checks in `control/drones.py`).

Responses: `401` if the Bearer token doesn't match the hotspot password,
`400` for a missing/invalid field, `200` on success.

## After provisioning

Once connected to real WiFi, the drone registers with whichever control
plane `config.yaml`'s `control_plane` key selects — `POST /drones` against
either the cloud API Gateway or a local GCS (see
[`control-plane.md`](control-plane.md) and
[`api-reference.md`](api-reference.md)).
