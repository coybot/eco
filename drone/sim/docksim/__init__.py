"""Passive four-pocket dock on the Pioneer 3200 with SpeedyBee Bee35 delivery drones.

Built from "Passive four-drone dock: simulation spec" v0.1. Every number lives in
docking/vehicles.yaml (blocks bee35, dock, mothership_airframe, dock_sensors, dock_ops,
dock_matrix). Contact physics is MuJoCo, simulated in the dock frame D (+x forward,
+y up, +z starboard), with the mothership's hover motion entering as fictitious forces.
"""
