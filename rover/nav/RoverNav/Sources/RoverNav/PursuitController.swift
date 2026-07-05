import Foundation

/// Pure-pursuit path follower. Stateless: the caller feeds the *current ARKit pose*
/// every tick, which closes the loop (the WAVE ROVER base has no wheel encoders, so
/// ARKit visual-inertial odometry is the feedback source). Emits WheelCommand to send
/// to the ESP32 as {"T":1,"L":..,"R":..}.
public struct PursuitController: Sendable {
    public struct Params: Sendable {
        public var lookahead: Double        // m — target point distance along path
        public var maxLinear: Double        // m/s
        public var maxAngular: Double        // rad/s
        public var wheelBase: Double         // m (track width)
        public var goalTolerance: Double     // m — position error to declare arrival
        public var rotateInPlaceAngle: Double // rad — above this heading error, spin first
        public var slowdownRadius: Double    // m — start easing off within this range of goal

        public init(lookahead: Double = 0.5,
                    maxLinear: Double = 0.35,
                    maxAngular: Double = 1.5,
                    wheelBase: Double = 0.13,
                    goalTolerance: Double = 0.2,
                    rotateInPlaceAngle: Double = 0.7,
                    slowdownRadius: Double = 0.6) {
            self.lookahead = lookahead
            self.maxLinear = maxLinear
            self.maxAngular = maxAngular
            self.wheelBase = wheelBase
            self.goalTolerance = goalTolerance
            self.rotateInPlaceAngle = rotateInPlaceAngle
            self.slowdownRadius = slowdownRadius
        }
    }

    public struct Output: Equatable, Sendable {
        public let command: WheelCommand
        public let reachedGoal: Bool
    }

    public let params: Params
    public init(params: Params = Params()) { self.params = params }

    /// Compute wheel command for the current pose along `path` (world waypoints,
    /// start→goal). Returns `.stop` with `reachedGoal == true` at the goal.
    public func step(pose: Pose2D, path: [Vec2]) -> Output {
        guard let goal = path.last else {
            return Output(command: .stop, reachedGoal: true)
        }
        let distToGoal = pose.position.distance(to: goal)
        if distToGoal <= params.goalTolerance {
            return Output(command: .stop, reachedGoal: true)
        }

        let target = lookaheadPoint(pose: pose, path: path, goal: goal, distToGoal: distToGoal)

        // Target in robot frame (forward = +x).
        let d = target - pose.position
        let cosY = cos(pose.yaw), sinY = sin(pose.yaw)
        let xr =  cosY * d.x + sinY * d.y
        let yr = -sinY * d.x + cosY * d.y
        let headingErr = atan2(yr, xr)

        // Ease speed near the goal.
        let speedScale = min(1.0, distToGoal / params.slowdownRadius)

        // Large heading error (or target behind): rotate in place toward it.
        if abs(headingErr) > params.rotateInPlaceAngle {
            let w = clamp(headingErr * 2.0, -params.maxAngular, params.maxAngular)
            return Output(
                command: DifferentialDrive.wheels(v: 0, w: w, wheelBase: params.wheelBase, maxWheelSpeed: params.maxLinear),
                reachedGoal: false)
        }

        // Pure-pursuit curvature: k = 2*yr / Ld^2.
        let ld = max(1e-3, (xr * xr + yr * yr).squareRoot())
        let curvature = 2.0 * yr / (ld * ld)
        let v = params.maxLinear * speedScale
        let w = clamp(v * curvature, -params.maxAngular, params.maxAngular)
        return Output(
            command: DifferentialDrive.wheels(v: v, w: w, wheelBase: params.wheelBase, maxWheelSpeed: params.maxLinear),
            reachedGoal: false)
    }

    /// First point at least `lookahead` ahead of the robot; falls back to the goal.
    private func lookaheadPoint(pose: Pose2D, path: [Vec2], goal: Vec2, distToGoal: Double) -> Vec2 {
        if distToGoal <= params.lookahead { return goal }
        // Find the closest waypoint index, then walk forward to the lookahead.
        var closestIdx = 0
        var closestDist = Double.infinity
        for (i, p) in path.enumerated() {
            let dd = pose.position.distance(to: p)
            if dd < closestDist { closestDist = dd; closestIdx = i }
        }
        var i = closestIdx
        while i < path.count {
            if pose.position.distance(to: path[i]) >= params.lookahead { return path[i] }
            i += 1
        }
        return goal
    }
}

@inline(__always)
private func clamp(_ x: Double, _ lo: Double, _ hi: Double) -> Double { min(max(x, lo), hi) }
