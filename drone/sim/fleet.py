"""Fleet roster + registration helpers.

`parse_roster("quad:10,rover:5")` yields deterministic drone ids so the sim host
(fleet_bridge) and the cloud registry agree. Run this module as a CLI on a host
with AWS creds (AWS_PROFILE=astral) to bulk-register the fleet in DynamoDB under
a user; then launch fleet_bridge with the same --fleet spec on hoopoe.
"""

from __future__ import annotations

import argparse

_TYPE_ALIASES = {"quad": "quadcopter", "quadcopter": "quadcopter",
                 "rover": "rover", "carter": "rover",
                 "fixedwing": "fixedwing", "fw": "fixedwing", "plane": "fixedwing"}


def parse_roster(spec: str) -> list[dict]:
    """'quad:10,rover:5' -> [{'id':'sim-quadcopter-001','type':'quadcopter'},...]"""
    roster = []
    for part in spec.split(","):
        part = part.strip()
        if not part:
            continue
        kind, _, count = part.partition(":")
        vtype = _TYPE_ALIASES.get(kind.strip().lower())
        if not vtype:
            raise ValueError(f"unknown vehicle kind: {kind}")
        n = int(count or "1")
        for i in range(1, n + 1):
            roster.append({"id": f"sim-{vtype}-{i:03d}", "type": vtype})
    return roster


def register_fleet(user_sub: str, roster: list[dict], environment: str = "office",
                   isaac_host: str = "hoopoe", region: str = "us-west-2") -> int:
    """Bulk write drone-registry-dev rows (one per vehicle). Needs AWS creds."""
    import boto3
    from datetime import datetime
    table = boto3.resource("dynamodb", region_name=region).Table("drone-registry-dev")
    now = datetime.utcnow().isoformat()
    with table.batch_writer() as bw:
        for spec in roster:
            label = {"quadcopter": "Quad", "fixedwing": "Fixed-Wing"}.get(spec["type"], "Rover")
            bw.put_item(Item={
                "userId": user_sub, "droneId": spec["id"],
                "name": f"{label} {spec['id'].rsplit('-', 1)[-1]}",
                "registeredAt": now, "status": "registered", "droneType": "sim",
                "vehicleType": spec["type"], "simEnvironment": environment,
                "isaacHost": isaac_host})
    return len(roster)


def main():
    ap = argparse.ArgumentParser(description="Register a sim fleet in DynamoDB")
    ap.add_argument("--fleet", required=True, help="e.g. quad:10,rover:10")
    ap.add_argument("--user-sub", required=True, help="owner Cognito sub")
    ap.add_argument("--env", default="office")
    ap.add_argument("--isaac-host", default="hoopoe")
    ap.add_argument("--region", default="us-west-2")
    args = ap.parse_args()
    roster = parse_roster(args.fleet)
    n = register_fleet(args.user_sub, roster, args.env, args.isaac_host, args.region)
    print(f"registered {n} drones: {roster[0]['id']} … {roster[-1]['id']}")


if __name__ == "__main__":
    main()
