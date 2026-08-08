#!/usr/bin/env python3
"""Register the 5 sim vehicles in DynamoDB drone-registry-dev so the API recognises them.

Usage:
    AWS_PROFILE=presidio python3 register_sim_drones.py
"""

from __future__ import annotations

import boto3
from datetime import datetime

TABLE = "drone-registry-dev"
USER_ID = "sim-demo"

VEHICLES = [
    {"droneId": "sim-quadcopter-01", "type": "quadcopter", "label": "Quad 1"},
    {"droneId": "sim-quadcopter-02", "type": "quadcopter", "label": "Quad 2"},
    {"droneId": "sim-quadcopter-03", "type": "quadcopter", "label": "Quad 3"},
    {"droneId": "sim-rover-04",      "type": "rover",      "label": "Rover 1"},
    {"droneId": "sim-rover-05",      "type": "rover",      "label": "Rover 2"},
]


def main():
    dynamodb = boto3.resource("dynamodb", region_name="us-west-2")
    table = dynamodb.Table(TABLE)
    registered_at = datetime.utcnow().isoformat()

    for v in VEHICLES:
        item = {
            "userId": USER_ID,
            "droneId": v["droneId"],
            "type": v["type"],
            "label": v["label"],
            "variant": "sim",
            "environment": "plaza",
            "registeredAt": registered_at,
        }
        table.put_item(Item=item)
        print(f"Upserted {v['droneId']} ({v['type']}) into {TABLE}")

    print(f"\nAll {len(VEHICLES)} sim vehicles registered under userId='{USER_ID}'.")


if __name__ == "__main__":
    main()
