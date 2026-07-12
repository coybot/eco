## Person actor: walks back and forth along `waypoints` (ENU ground positions) at
## `speed` m/s when `active` is true. Toggled by PhroverManager's `person_walk` inject.
## Self-contained — no dependency on PhroverManager beyond the `active` flag it sets.
extends CharacterBody3D

var waypoints: Array = []
var speed: float = 0.8
var active: bool = false

var _target_idx: int = 1


func _physics_process(delta: float) -> void:
	if not active or waypoints.size() < 2:
		velocity = Vector3.ZERO
		return
	var here := Vector2(position.x, -position.z)
	var target: Vector2 = waypoints[_target_idx]
	var to_target := target - here
	var dist := to_target.length()
	if dist < 0.15:
		_target_idx = (_target_idx + 1) % waypoints.size()
		velocity = Vector3.ZERO
		return
	var dir := to_target.normalized()
	velocity = Vector3(dir.x, 0.0, -dir.y) * speed
	move_and_slide()


func enu_position() -> Vector2:
	return Vector2(position.x, -position.z)
