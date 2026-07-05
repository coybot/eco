import Foundation

/// 2D point / vector in the navigation plane (meters, ARKit-derived world frame
/// flattened to the ground: x = world x, y = world z).
public struct Vec2: Equatable, Sendable {
    public var x: Double
    public var y: Double

    public init(_ x: Double, _ y: Double) {
        self.x = x
        self.y = y
    }

    public static let zero = Vec2(0, 0)

    public static func + (a: Vec2, b: Vec2) -> Vec2 { Vec2(a.x + b.x, a.y + b.y) }
    public static func - (a: Vec2, b: Vec2) -> Vec2 { Vec2(a.x - b.x, a.y - b.y) }
    public static func * (a: Vec2, s: Double) -> Vec2 { Vec2(a.x * s, a.y * s) }

    public var length: Double { (x * x + y * y).squareRoot() }

    public func distance(to other: Vec2) -> Double { (self - other).length }
}

/// Robot pose on the ground plane: position + heading (yaw, radians, CCW from +x).
public struct Pose2D: Equatable, Sendable {
    public var position: Vec2
    public var yaw: Double

    public init(position: Vec2, yaw: Double) {
        self.position = position
        self.yaw = yaw
    }

    /// Unit vector pointing along the robot's forward axis.
    public var forward: Vec2 { Vec2(cos(yaw), sin(yaw)) }
}

/// Wrap an angle to (-pi, pi].
public func normalizeAngle(_ a: Double) -> Double {
    var r = a.truncatingRemainder(dividingBy: 2 * .pi)
    if r > .pi { r -= 2 * .pi }
    if r <= -(.pi) { r += 2 * .pi }
    return r
}
