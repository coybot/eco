import XCTest
@testable import RoverNav

/// End-to-end (off-device) analog of the plan's Phase-1 verification: plan a path
/// around a corner, then drive a kinematic differential-drive rover along it using
/// the pursuit controller, and assert it reaches the goal without hitting an obstacle.
final class NavIntegrationTests: XCTestCase {
    func testDriveAroundCornerToGoalCollisionFree() {
        // Two layers, like a real nav stack: `obstacles` = collision truth (the raw
        // wall), `planning` = same wall inflated by the robot radius for clearance.
        var obstacles = Costmap(width: 50, height: 50, resolution: 0.1, origin: .zero)
        // Wall at x≈2.5 from y=0 up to y=3.5, leaving a gap in the upper part.
        var y = 0.0
        while y < 3.5 {
            obstacles.markObstacle(at: Vec2(2.5, y))
            y += 0.05
        }
        var planning = obstacles
        planning.inflate(radius: 0.25) // robot radius + margin

        let start = Vec2(0.5, 0.5)
        let goal = Vec2(4.5, 0.5)
        guard let path = AStarPlanner().plan(from: start, to: goal, in: planning) else {
            return XCTFail("no path found")
        }

        // Kinematic unicycle integration.
        let controller = PursuitController(params: .init(
            lookahead: 0.3, maxLinear: 0.35, maxAngular: 1.8,
            wheelBase: 0.13, goalTolerance: 0.2))
        var pose = Pose2D(position: start, yaw: 0)
        let dt = 0.05
        var reached = false
        let maxSteps = 4000

        for _ in 0..<maxSteps {
            let out = controller.step(pose: pose, path: path)
            if out.reachedGoal { reached = true; break }

            // Integrate: v = (l+r)/2, w = (r-l)/wheelBase.
            let v = (out.command.left + out.command.right) / 2
            let w = (out.command.right - out.command.left) / 0.13
            pose.yaw = normalizeAngle(pose.yaw + w * dt)
            pose.position = pose.position + Vec2(cos(pose.yaw), sin(pose.yaw)) * (v * dt)

            // Collision truth: the rover center must never enter an actual wall cell.
            let c = obstacles.worldToCell(pose.position)
            XCTAssertFalse(obstacles.isBlocked(c.cx, c.cy),
                           "rover entered an obstacle at \(pose.position)")
        }

        XCTAssertTrue(reached, "rover failed to reach the goal")
        XCTAssertLessThanOrEqual(pose.position.distance(to: goal), 0.3,
                                 "final position error too large")
    }

    func testRotatesTowardTargetBehind() {
        let controller = PursuitController()
        // Goal directly behind the robot (facing +x, goal at -x).
        let out = controller.step(pose: Pose2D(position: .zero, yaw: 0),
                                  path: [Vec2(-1, 0)])
        XCTAssertFalse(out.reachedGoal)
        // Should command a spin (wheels opposite), not drive forward.
        XCTAssertEqual(out.command.left + out.command.right, 0, accuracy: 1e-6)
        XCTAssertNotEqual(out.command.left, 0)
    }
}
