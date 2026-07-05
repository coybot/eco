import XCTest
@testable import RoverNav

final class DifferentialDriveTests: XCTestCase {
    func testStraightForwardEqualWheels() {
        let cmd = DifferentialDrive.wheels(v: 0.3, w: 0, wheelBase: 0.13, maxWheelSpeed: 0.5)
        XCTAssertEqual(cmd.left, 0.3, accuracy: 1e-9)
        XCTAssertEqual(cmd.right, 0.3, accuracy: 1e-9)
    }

    func testSpinInPlaceOppositeWheels() {
        let cmd = DifferentialDrive.wheels(v: 0, w: 1.0, wheelBase: 0.2, maxWheelSpeed: 0.5)
        XCTAssertEqual(cmd.left, -0.1, accuracy: 1e-9)
        XCTAssertEqual(cmd.right, 0.1, accuracy: 1e-9)
    }

    func testSaturationPreservesCurvatureSign() {
        // Command that would exceed the wheel limit gets scaled but keeps its shape.
        let cmd = DifferentialDrive.wheels(v: 1.0, w: 2.0, wheelBase: 0.5, maxWheelSpeed: 0.5)
        XCTAssertLessThanOrEqual(max(abs(cmd.left), abs(cmd.right)), 0.5 + 1e-9)
        XCTAssertGreaterThan(cmd.right, cmd.left) // still turning the same way
    }
}
