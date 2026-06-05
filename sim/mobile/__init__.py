"""Headless mobile clients for the Astral platform — the test harness's "iPhone".

``app_client.AppClient`` speaks the same cloud contract as the iOS DroneOperator
app (the MQTT topics in ``DroneOperator/Services/MQTTService.swift`` and the
``POST /command`` cloud API), so an Ishmael test can send a mission and receive
the resulting picture/video without a Mac or a real device in the loop.

The real iOS Simulator path (for true UI verification) lives in
``drive_ios_sim.sh`` + ``ios_sim_driver.md`` and runs on a Mac.
"""
